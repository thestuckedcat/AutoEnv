#!/usr/bin/env python3
"""Static contract checks for AutoEnv environment registration scripts."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


SELECTOR_FACTORIES = {"package", "extra_file", "match"}
UPLOAD_METHODS = {"scp_upload", "sftp_upload"}
PLACEHOLDER = re.compile(r"S\{([^{}]+)\}")
CONNECTION_METHODS = {"register_ssh_host", "register_telnet", "register_ftp_host"}
RESOURCE_PROTOCOLS = {
    "register_ssh_host": "ssh",
    "register_telnet": "telnet",
    "register_ftp_host": "ftp",
}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_text(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _placeholder_keys(text: str, known_keys: set[str]) -> list[str]:
    """Find exact known selectors first so regex selectors may contain braces."""
    residual = text
    found: list[str] = []
    for key in sorted(known_keys, key=len, reverse=True):
        token = f"S{{{key}}}"
        if token in residual:
            found.append(key)
            residual = residual.replace(token, "")
    found.extend(PLACEHOLDER.findall(residual))
    return found


def _assigned_call(statement: ast.stmt) -> tuple[str, ast.Call] | None:
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return None
    target = statement.targets[0]
    if not isinstance(target, ast.Name) or not isinstance(statement.value, ast.Call):
        return None
    return target.id, statement.value


def _registered(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _call_name(item.func if isinstance(item, ast.Call) else item) == "register_script"
        for item in function.decorator_list
    )


def _registered_func(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _call_name(item.func if isinstance(item, ast.Call) else item) == "register_func"
        for item in function.decorator_list
    )


def _declared_resources(
    path: Path, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> tuple[dict[str, dict[str, str]], list[Violation]]:
    violations: list[Violation] = []
    decorator = next(
        item
        for item in function.decorator_list
        if _call_name(item.func if isinstance(item, ast.Call) else item) == "register_script"
    )
    if not isinstance(decorator, ast.Call):
        return {}, violations
    resources_node = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "resources"),
        None,
    )
    if resources_node is None:
        return {}, violations
    try:
        raw_resources = ast.literal_eval(resources_node)
    except (ValueError, TypeError, SyntaxError):
        violations.append(
            Violation(path, decorator.lineno, "register_script resources must be literal metadata")
        )
        return {}, violations
    declared: dict[str, dict[str, str]] = {}
    if not isinstance(raw_resources, (tuple, list)):
        violations.append(
            Violation(path, decorator.lineno, "register_script resources must be a tuple or list")
        )
        return {}, violations
    for item in raw_resources:
        if not isinstance(item, dict):
            violations.append(
                Violation(path, decorator.lineno, "each script resource must be a dictionary")
            )
            continue
        name = item.get("name")
        alias = item.get("alias")
        description = item.get("description")
        label = item.get("label")
        protocol = item.get("protocol")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (name, alias, description, label, protocol)
        ):
            violations.append(
                Violation(
                    path,
                    decorator.lineno,
                    "script resource requires non-empty name, alias, description, label and protocol",
                )
            )
            continue
        if name in declared:
            violations.append(
                Violation(path, decorator.lineno, f"script resource name {name!r} is duplicated")
            )
        declared[name] = {
            "name": name,
            "alias": alias,
            "description": description,
            "label": label,
            "protocol": protocol,
        }
    return declared, violations


def _package_metadata_violations(
    path: Path, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[Violation]:
    decorator = next(
        item
        for item in function.decorator_list
        if _call_name(item.func if isinstance(item, ast.Call) else item) == "register_script"
    )
    if not isinstance(decorator, ast.Call):
        return []
    packages_node = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "packages"),
        None,
    )
    if packages_node is None:
        return []
    try:
        packages = ast.literal_eval(packages_node)
    except (ValueError, TypeError, SyntaxError):
        return [
            Violation(path, decorator.lineno, "register_script packages must be literal metadata")
        ]
    if not isinstance(packages, (tuple, list)):
        return [
            Violation(path, decorator.lineno, "register_script packages must be a tuple or list")
        ]
    violations: list[Violation] = []
    names: set[str] = set()
    for item in packages:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("name", "alias", "description")
        ):
            violations.append(
                Violation(
                    path,
                    decorator.lineno,
                    "each Web-facing package requires non-empty name, alias and description",
                )
            )
            continue
        name = item["name"]
        if name in names:
            violations.append(
                Violation(path, decorator.lineno, f"script package name {name!r} is duplicated")
            )
        names.add(name)
    return violations


def _function_violations(
    path: Path, function: ast.FunctionDef | ast.AsyncFunctionDef
) -> list[Violation]:
    violations: list[Violation] = []
    selector_by_var: dict[str, str] = {}
    selector_factory_by_var: dict[str, str] = {}
    selector_var_by_key: dict[str, str] = {}
    ssh_by_var: dict[str, str] = {}
    telnet_source_by_var: dict[str, str | None] = {}
    declaration_lines: set[int] = set()
    registered_func_names: set[str] = set()
    declared_resources, resource_violations = _declared_resources(path, function)
    violations.extend(resource_violations)
    violations.extend(_package_metadata_violations(path, function))

    func_indexes = [
        index
        for index, statement in enumerate(function.body)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _registered_func(statement)
    ]
    direct_func_ids = {
        id(function.body[index])
        for index in func_indexes
    }
    for node in ast.walk(function):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node is not function
            and _registered_func(node)
            and id(node) not in direct_func_ids
        ):
            violations.append(
                Violation(
                    path,
                    node.lineno,
                    "register_func must be a direct final declaration in register_script",
                )
            )
    if func_indexes:
        first_func_index = min(func_indexes)
        for statement in function.body[first_func_index:]:
            if isinstance(statement, ast.Return):
                continue
            if not (
                isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and _registered_func(statement)
            ):
                violations.append(
                    Violation(
                        path,
                        statement.lineno,
                        "register_func definitions must be the final main-flow declarations",
                    )
                )
        for index in func_indexes:
            func = function.body[index]
            assert isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))
            if (
                len(func.args.posonlyargs) + len(func.args.args) != 1
                or func.args.vararg is not None
                or func.args.kwarg is not None
            ):
                violations.append(
                    Violation(path, func.lineno, "registered func must accept exactly one RunContext argument")
                )
            decorator = next(
                item
                for item in func.decorator_list
                if _call_name(item.func if isinstance(item, ast.Call) else item)
                == "register_func"
            )
            name = None
            if isinstance(decorator, ast.Call):
                name = _literal_text(decorator.args[0] if decorator.args else None)
                for keyword in decorator.keywords:
                    if keyword.arg == "name":
                        name = _literal_text(keyword.value)
            if name is None:
                violations.append(
                    Violation(path, func.lineno, "register_func name must be a literal string")
                )
            elif name in registered_func_names:
                violations.append(
                    Violation(path, func.lineno, f"registered func name {name!r} is duplicated")
                )
            else:
                registered_func_names.add(name)

    for statement in function.body:
        assigned = _assigned_call(statement)
        if assigned is None:
            continue
        variable, call = assigned
        name = _call_name(call.func)
        if name in SELECTOR_FACTORIES:
            key = _literal_text(call.args[0] if call.args else None)
            if key is None:
                violations.append(
                    Violation(path, statement.lineno, "file selector must use one literal string")
                )
                continue
            selector_by_var[variable] = key
            selector_factory_by_var[variable] = name
            if key in selector_var_by_key:
                violations.append(
                    Violation(path, statement.lineno, f"selector string {key!r} is declared more than once")
                )
            selector_var_by_key[key] = variable
            declaration_lines.add(statement.lineno)
        elif name in CONNECTION_METHODS:
            logical_name = _literal_text(call.args[0] if call.args else None)
            if logical_name is None:
                violations.append(
                    Violation(path, statement.lineno, "connection name must be a literal string")
                )
                continue
            resource_label = next(
                (
                    _literal_text(keyword.value)
                    for keyword in call.keywords
                    if keyword.arg == "resource_label"
                ),
                None,
            )
            if resource_label is None:
                violations.append(
                    Violation(
                        path,
                        statement.lineno,
                        f"{name} requires one literal resource_label",
                    )
                )
            declared = declared_resources.get(logical_name)
            protocol = RESOURCE_PROTOCOLS[name]
            if declared is None:
                violations.append(
                    Violation(
                        path,
                        statement.lineno,
                        f"connection {logical_name!r} must be declared in register_script resources",
                    )
                )
            elif declared["protocol"] != protocol or declared["label"] != resource_label:
                violations.append(
                    Violation(
                        path,
                        statement.lineno,
                        f"connection {logical_name!r} must match its script resource protocol and label",
                    )
                )
            if name == "register_ssh_host":
                ssh_by_var[variable] = logical_name
            if name == "register_telnet":
                source = None
                for keyword in call.keywords:
                    if keyword.arg == "uploaded_files_from":
                        source = _literal_text(keyword.value)
                        if source is None:
                            violations.append(
                                Violation(
                                    path,
                                    statement.lineno,
                                    "uploaded_files_from must be a literal SSH host name",
                                )
                            )
                telnet_source_by_var[variable] = source
            declaration_lines.add(statement.lineno)
    ssh_var_by_name = {name: variable for variable, name in ssh_by_var.items()}
    for variable, source in telnet_source_by_var.items():
        if source is not None and source not in ssh_var_by_name:
            violations.append(
                Violation(
                    path,
                    next(
                        statement.lineno
                        for statement in function.body
                        if (assigned := _assigned_call(statement)) is not None
                        and assigned[0] == variable
                    ),
                    f"uploaded_files_from references undeclared SSH host {source!r}",
                )
            )

    workflow_line = min(
        (
            statement.lineno
            for statement in function.body
            if statement.lineno not in declaration_lines
            and not (
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
        ),
        default=None,
    )
    if workflow_line is not None:
        for line in declaration_lines:
            if line > workflow_line:
                violations.append(
                    Violation(
                        path,
                        line,
                        "selectors and connections must be declared before workflow operations",
                    )
                )

    direct_declaration_calls = {
        id(assigned[1])
        for statement in function.body
        if (assigned := _assigned_call(statement)) is not None
        and _call_name(assigned[1].func)
        in SELECTOR_FACTORIES | CONNECTION_METHODS
    }
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if (
                name in SELECTOR_FACTORIES | CONNECTION_METHODS
                and id(node) not in direct_declaration_calls
            ):
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        f"{name}() must be assigned once in the function declaration block",
                    )
                )

    uploads: dict[tuple[str, str], list[int]] = {}
    generated_names: dict[str, int] = {}
    calls = sorted(
        (node for node in ast.walk(function) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for call in calls:
        method = _call_name(call.func)
        owner = call.func.value.id if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name) else None
        if method in UPLOAD_METHODS and owner is not None:
            selector_node = None
            if call.args:
                selector_node = call.args[0]
            for keyword in call.keywords:
                if keyword.arg == "local_file":
                    selector_node = keyword.value
            if not isinstance(selector_node, ast.Name) or selector_node.id not in selector_by_var:
                violations.append(
                    Violation(path, call.lineno, "upload local_file must reuse a declared selector variable")
                )
            else:
                uploads.setdefault((owner, selector_node.id), []).append(call.lineno)

        if method == "generate_sh_file":
            file_name_node = call.args[0] if call.args else None
            script_node = call.args[1] if len(call.args) > 1 else None
            for keyword in call.keywords:
                if keyword.arg == "file_name":
                    file_name_node = keyword.value
                elif keyword.arg == "script":
                    script_node = keyword.value
            file_name = _literal_text(file_name_node)
            script = _literal_text(script_node)
            if file_name is None or not file_name.endswith(".sh"):
                violations.append(
                    Violation(path, call.lineno, "generate_sh_file filename must be one literal .sh name")
                )
            else:
                generated_names[file_name] = call.lineno
                if file_name not in selector_var_by_key:
                    violations.append(
                        Violation(path, call.lineno, f"generated {file_name!r} needs a declared extra_file selector")
                    )
                elif selector_factory_by_var[selector_var_by_key[file_name]] != "extra_file":
                    violations.append(
                        Violation(path, call.lineno, f"generated {file_name!r} selector must use extra_file()")
                    )
            if script is None:
                violations.append(
                    Violation(path, call.lineno, "generate_sh_file script must be one complete literal string")
                )
                continue
            placeholder_vars: list[str] = []
            for key in _placeholder_keys(script, set(selector_var_by_key)):
                variable = selector_var_by_key.get(key)
                if variable is None:
                    violations.append(
                        Violation(path, call.lineno, f"S{{{key}}} has no declared selector")
                    )
                else:
                    placeholder_vars.append(variable)
            common_hosts: set[str] | None = None
            for variable in placeholder_vars:
                hosts = {
                    host
                    for (host, selector), lines in uploads.items()
                    if selector == variable and any(line < call.lineno for line in lines)
                }
                common_hosts = hosts if common_hosts is None else common_hosts & hosts
            if placeholder_vars and not common_hosts:
                violations.append(
                    Violation(
                        path,
                        call.lineno,
                        "all shell placeholders must be uploaded to one common SSH host before generation",
                    )
                )

        if method in {"execute", "execute_on_output"} and owner is not None:
            command_node = call.args[0] if call.args else None
            for keyword in call.keywords:
                if keyword.arg == "command":
                    command_node = keyword.value
            command = _literal_text(command_node)
            if command is None:
                continue
            for key in _placeholder_keys(command, set(selector_var_by_key)):
                variable = selector_var_by_key.get(key)
                if variable is None:
                    violations.append(
                        Violation(path, call.lineno, f"S{{{key}}} has no declared selector")
                    )
                    continue
                upload_owner = owner
                if owner in telnet_source_by_var:
                    source = telnet_source_by_var[owner]
                    if source is not None:
                        upload_owner = ssh_var_by_name.get(source, "")
                    else:
                        candidates = {
                            host
                            for (host, selector), lines in uploads.items()
                            if selector == variable and any(line < call.lineno for line in lines)
                        }
                        upload_owner = next(iter(candidates)) if len(candidates) == 1 else ""
                if not any(
                    line < call.lineno
                    for line in uploads.get((upload_owner, variable), [])
                ):
                    violations.append(
                        Violation(
                            path,
                            call.lineno,
                            f"S{{{key}}} must be uploaded to the command target before execution",
                        )
                    )

    for file_name, generate_line in generated_names.items():
        selector = selector_var_by_key.get(file_name)
        if selector is not None and not any(
            line > generate_line
            for (host, variable), lines in uploads.items()
            if variable == selector
            for line in lines
        ):
            violations.append(
                Violation(path, generate_line, f"generated {file_name!r} must be uploaded after generation")
            )

    return violations


def validate_path(path: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        line = getattr(exc, "lineno", None) or 1
        return [Violation(path, line, f"cannot parse script: {exc}")]

    violations: list[Violation] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _registered_func(node):
            violations.append(
                Violation(
                    path,
                    node.lineno,
                    "register_func must be nested at the end of a running register_script body",
                )
            )
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _registered(node)
    ]
    if not functions and path.name != "__init__.py":
        violations.append(Violation(path, 1, "script has no @register_script function"))
    for function in functions:
        violations.extend(_function_violations(path, function))
    return violations


def _default_paths() -> list[Path]:
    return sorted(path for path in Path("scripts").glob("*.py") if path.name != "__init__.py")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    paths = args.paths or _default_paths()
    violations = [item for path in paths for item in validate_path(path)]
    for violation in violations:
        print(violation)
    if violations:
        print(f"AutoEnv script contract failed: {len(violations)} violation(s)")
        return 1
    print(f"AutoEnv script contract passed: {len(paths)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
