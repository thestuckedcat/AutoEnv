from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class InferredScriptMetadata:
    packages: tuple[dict[str, object], ...] = ()
    resources: tuple[dict[str, object], ...] = ()
    called_functions: tuple[str, ...] = ()


_RESOURCE_PROTOCOLS = {
    "register_ssh_host": "ssh",
    "register_telnet": "telnet",
    "register_ftp_host": "ftp",
}


def infer_script_metadata(body: Callable[..., object]) -> InferredScriptMetadata:
    """Read metadata from literal API calls without executing the script body."""

    try:
        source = textwrap.dedent(inspect.getsource(body))
    except (OSError, TypeError):
        return InferredScriptMetadata()
    try:
        module = ast.parse(source)
    except SyntaxError:
        return InferredScriptMetadata()
    function = next(
        (
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == body.__name__
        ),
        None,
    )
    if function is None:
        return InferredScriptMetadata()
    return extract_script_metadata(function)


def extract_script_metadata(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> InferredScriptMetadata:
    packages: list[dict[str, object]] = []
    resources: list[dict[str, object]] = []
    called_functions: list[str] = []

    for node in _root_scope_nodes(function):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name == "package":
            name = _required_literal_text(node, 0, "name")
            if name is None:
                continue
            packages.append(
                {
                    "name": name,
                    "alias": _optional_literal_text(node, "alias", name),
                    "description": _optional_literal_text(node, "description", ""),
                }
            )
        elif call_name in _RESOURCE_PROTOCOLS:
            name = _required_literal_text(node, 0, "name")
            label = _required_literal_text(node, None, "resource_label")
            if name is None or label is None:
                continue
            resources.append(
                {
                    "name": name,
                    "alias": _optional_literal_text(node, "alias", name),
                    "description": _optional_literal_text(node, "description", ""),
                    "label": label,
                    "protocol": _RESOURCE_PROTOCOLS[call_name],
                }
            )
        elif isinstance(node.func, ast.Name):
            called_functions.append(node.func.id)

    return InferredScriptMetadata(
        packages=tuple(_deduplicate(packages)),
        resources=tuple(_deduplicate(resources)),
        called_functions=tuple(dict.fromkeys(called_functions)),
    )


def _root_scope_nodes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        if node is not function and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            return
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in function.body:
        visit(statement)
    return nodes


def _call_name(function: ast.expr) -> str | None:
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _required_literal_text(
    call: ast.Call,
    position: int | None,
    keyword: str,
) -> str | None:
    value = (
        call.args[position]
        if position is not None and len(call.args) > position
        else _keyword(call, keyword)
    )
    if (
        not isinstance(value, ast.Constant)
        or not isinstance(value.value, str)
        or not value.value.strip()
    ):
        return None
    return value.value.strip()


def _optional_literal_text(call: ast.Call, keyword: str, default: str) -> str:
    value = _keyword(call, keyword)
    if value is None:
        return default
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        return default
    return value.value.strip()


def _deduplicate(values: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: dict[str, dict[str, object]] = {}
    for value in values:
        name = str(value["name"])
        previous = seen.get(name)
        if previous is not None:
            continue
        seen[name] = value
        result.append(value)
    return result
