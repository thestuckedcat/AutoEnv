from __future__ import annotations

import ast
import sys
from pathlib import Path

def validate(path: Path) -> list[str]:
    errors: list[str] = []; tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    registered = [node for node in functions if any(isinstance(d, ast.Call) and getattr(d.func, "id", "") == "register_web_tool" for d in node.decorator_list)]
    if len(registered) != 1: errors.append("tool module must contain exactly one @register_web_tool function")
    elif len(registered[0].args.args) != 1: errors.append("registered tool must accept exactly one argument")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}: errors.append(f"line {node.lineno}: eval/exec is not allowed")
    return errors

if __name__ == "__main__":
    problems = validate(Path(sys.argv[1])); print("\n".join(problems) if problems else "OK"); raise SystemExit(bool(problems))
