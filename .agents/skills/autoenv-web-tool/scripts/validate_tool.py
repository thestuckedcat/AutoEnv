from __future__ import annotations

import ast
import sys
from pathlib import Path


FORBIDDEN_IMPORTS = {"httpx", "paramiko", "requests", "socket", "subprocess", "urllib"}


def _literal_keyword(decorator: ast.Call, name: str, default: str) -> str | None:
    keyword = next((item for item in decorator.keywords if item.arg == name), None)
    if keyword is None:
        return default
    if not isinstance(keyword.value, ast.Constant) or not isinstance(keyword.value.value, str):
        return None
    return keyword.value.value


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    registered: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]] = []
    for function in functions:
        for decorator in function.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and getattr(decorator.func, "id", "") == "register_web_tool"
            ):
                registered.append((function, decorator))
    if len(registered) != 1:
        errors.append("tool module must contain exactly one @register_web_tool function")
    else:
        function, decorator = registered[0]
        if len(function.args.args) != 1:
            errors.append("registered tool must accept exactly one argument")
        kind = _literal_keyword(decorator, "kind", "local")
        renderer = _literal_keyword(decorator, "renderer", "json")
        if kind is None or kind not in {"local", "workflow"}:
            errors.append("tool kind must be a literal 'local' or 'workflow'")
        if renderer is None or renderer not in {"json", "log_collection"}:
            errors.append("tool renderer must be a supported string literal")
        if kind == "local" and renderer != "json":
            errors.append("local tools only support renderer='json'")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"eval", "exec"}:
                errors.append(f"line {node.lineno}: eval/exec is not allowed")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in FORBIDDEN_IMPORTS:
                    errors.append(
                        f"line {node.lineno}: direct network/subprocess imports are not allowed"
                    )
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".", 1)[0] in FORBIDDEN_IMPORTS:
                errors.append(
                    f"line {node.lineno}: direct network/subprocess imports are not allowed"
                )
    return errors


if __name__ == "__main__":
    problems = validate(Path(sys.argv[1]))
    print("\n".join(problems) if problems else "OK")
    raise SystemExit(bool(problems))
