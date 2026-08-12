from __future__ import annotations

import importlib.util
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


ToolBody = Callable[[dict[str, object]], object]


@dataclass(frozen=True)
class WebToolDefinition:
    name: str
    title: str
    description: str
    fields: tuple[dict[str, object], ...]
    body: ToolBody
    source: str


_TOOLS: dict[str, WebToolDefinition] = {}


def register_web_tool(
    *, name: str, title: str, description: str = "", fields: list[dict[str, object]] | tuple[dict[str, object], ...]
) -> Callable[[ToolBody], ToolBody]:
    normalized = _name(name)
    normalized_fields = tuple(_field(item) for item in fields)

    def decorator(body: ToolBody) -> ToolBody:
        if normalized in _TOOLS:
            raise ValueError(f"web tool already registered: {normalized}")
        parameters = list(inspect.signature(body).parameters.values())
        if len(parameters) != 1:
            raise TypeError("web tool must accept exactly one values dictionary")
        _TOOLS[normalized] = WebToolDefinition(
            name=normalized, title=str(title).strip() or normalized,
            description=str(description).strip(), fields=normalized_fields,
            body=body, source=inspect.getsourcefile(body) or "",
        )
        return body
    return decorator


def discover_web_tools(root_dir: Path | str) -> list[WebToolDefinition]:
    tools_dir = Path(root_dir).resolve() / "webPage" / "tools"
    if not tools_dir.is_dir():
        return []
    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"autoenv_web_tool_{path.stem}"
        if module_name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load web tool: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return [_TOOLS[name] for name in sorted(_TOOLS)]


def describe_tools(root_dir: Path | str) -> list[dict[str, object]]:
    return [
        {"name": item.name, "title": item.title, "description": item.description,
         "fields": list(item.fields), "source": item.source}
        for item in discover_web_tools(root_dir)
    ]


def run_web_tool(root_dir: Path | str, name: str, values: dict[str, object]) -> object:
    discover_web_tools(root_dir)
    try:
        definition = _TOOLS[name]
    except KeyError as exc:
        raise KeyError(f"unknown web tool: {name}") from exc
    if not isinstance(values, dict):
        raise TypeError("tool values must be an object")
    return definition.body(values)


def _name(value: str) -> str:
    value = str(value).strip()
    if not value or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in value):
        raise ValueError("web tool name must use lowercase letters, digits, '-' or '_'")
    return value


def _field(value: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict) or not str(value.get("name", "")).strip():
        raise ValueError("each web tool field requires a name")
    result = dict(value)
    result["name"] = str(result["name"]).strip()
    result.setdefault("label", result["name"])
    result.setdefault("type", "text")
    result.setdefault("required", False)
    json.dumps(result)
    return result
