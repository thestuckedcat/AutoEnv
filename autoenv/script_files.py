from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from .remote_catalog import allowed_pairs, compatible_topologies, read_catalog


def resolve_script(root: Path | str, script: str) -> Path:
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script must be a nonempty Python file name or path")
    root = Path(root).resolve()
    candidate = Path(script)
    if not candidate.is_absolute():
        candidate = root / (candidate if candidate.suffix == ".py" else Path("scripts") / f"{script}.py")
    candidate = candidate.resolve()
    if candidate.suffix != ".py" or not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def inspect_script(path: Path | str) -> dict:
    path = Path(path).resolve()
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    names = {"init_script", "get_remote_info", "hdfs_download"}
    aliases = {name: name for name in names}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "autoenv":
            for name in node.names:
                if name.name in names:
                    aliases[name.asname or name.name] = name.name

    def call_name(node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            return aliases.get(node.func.id, node.func.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    main = next((node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"), None)
    if isinstance(main, ast.AsyncFunctionDef) or (main and (main.args.posonlyargs or main.args.args or main.args.kwonlyargs or main.args.vararg or main.args.kwarg)):
        raise ValueError("script main() must be synchronous with no parameters")
    def expand_scope(statements):
        expanded = []
        for statement in statements:
            expanded.append(statement)
            if isinstance(statement, ast.With) and len(statement.items) == 1 and call_name(statement.items[0].context_expr) == "init_script":
                expanded.extend(expand_scope(statement.body))
        return expanded

    scope = expand_scope(main.body if main else tree.body)
    slots, packages = [], []
    initialization = []
    for statement in scope:
        value = statement.value if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)) else None
        initialization_with = isinstance(statement, ast.With) and len(statement.items) == 1 and call_name(statement.items[0].context_expr) == "init_script"
        if initialization_with:
            value = statement.items[0].context_expr
        name = call_name(value)
        if name == "init_script":
            if value.args or value.keywords:
                raise ValueError("init_script() takes no arguments")
            initialization.append(statement.lineno)
        if name == "get_remote_info":
            target = statement.targets[0] if isinstance(statement, ast.Assign) and len(statement.targets) == 1 else getattr(statement, "target", None)
            if not isinstance(target, ast.Name):
                raise ValueError("get_remote_info must be assigned directly to a named role")
            args = list(value.args)
            keywords = {item.arg: item.value for item in value.keywords}
            if len(args) > 2 or set(keywords) - {"required_type", "allowed_envs"}:
                raise ValueError("get_remote_info accepts required_type and allowed_envs")
            if (args and "required_type" in keywords) or (len(args) > 1 and "allowed_envs" in keywords):
                raise ValueError("get_remote_info argument was supplied twice")
            kind = ast.literal_eval(args[0] if args else keywords.get("required_type"))
            pairs = allowed_pairs(ast.literal_eval(args[1] if len(args) > 1 else keywords.get("allowed_envs")))
            if kind not in {"ssh", "telnet", "ftp"}:
                raise ValueError("required_type must be ssh, telnet or ftp")
            slots.append({"role": target.id, "required_type": kind, "allowed_envs": pairs, "line": value.lineno, "end_line": value.end_lineno})
        for nested in ast.walk(value if initialization_with else statement):
            nested_name = call_name(nested)
            if nested_name == "get_remote_info" and nested is not value:
                raise ValueError("environment slots must be unconditional assignments in main() or at module scope")
            if nested_name == "hdfs_download":
                config = nested.args[1] if len(nested.args) > 1 else next((k.value for k in nested.keywords if k.arg == "config_json_name"), None)
                package = ast.literal_eval(config)
                if not isinstance(package, str) or not package:
                    raise ValueError("hdfs_download config name must be a literal string")
                if package not in packages:
                    packages.append(package)
    if not initialization:
        raise ValueError("script must explicitly call init_script()")
    if slots and initialization[0] >= min(slot["line"] for slot in slots):
        raise ValueError("init_script() must precede environment requests")
    roles = [slot["role"] for slot in slots]
    if len(set(roles)) != len(roles):
        raise ValueError("environment role names must be unique")
    arguments = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SCRIPT_ARGUMENTS" for t in node.targets):
            arguments = ast.literal_eval(node.value)
    if not isinstance(arguments, (list, tuple)):
        raise TypeError("SCRIPT_ARGUMENTS must be a literal array")
    seen = set()
    for schema in arguments:
        if not isinstance(schema, dict) or not isinstance(schema.get("name"), str) or not schema["name"]:
            raise ValueError("argument schema requires a name")
        if schema["name"] in seen or schema.get("type", "str") not in {"str", "int", "float", "bool", "list", "dict"}:
            raise ValueError("invalid or duplicate argument schema")
        if "required" in schema and not isinstance(schema["required"], bool):
            raise TypeError("required must be a boolean")
        seen.add(schema["name"])
    layout = [{key: slot[key] for key in ("role", "required_type", "allowed_envs")} for slot in slots]
    return {
        "script": path.stem, "name": path.stem, "path": str(path),
        "description": (ast.get_docstring(tree) or "").split("\n")[0],
        "entry": "main" if main else "module", "slots": slots,
        "packages": packages, "arguments": list(arguments),
        "revision": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "layout": hashlib.sha256(json.dumps(layout, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
    }


def describe_script(root: Path | str, script: str) -> dict:
    descriptor = inspect_script(resolve_script(root, script))
    descriptor["topologies"] = compatible_topologies(descriptor["slots"], read_catalog(root))
    return descriptor


def list_script_files(root: Path | str) -> list[dict]:
    result = []
    for path in sorted((Path(root) / "scripts").glob("*.py")):
        if path.name.startswith("_") or path.stem == "template":
            continue
        try:
            result.append(inspect_script(path))
        except (ValueError, TypeError, SyntaxError) as exc:
            result.append({"script": path.stem, "name": path.stem, "path": str(path.resolve()), "description": "", "error": str(exc)})
    return result
