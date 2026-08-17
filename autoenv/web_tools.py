from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO

from .results import ScriptResult, result_to_dict
from .runtime import RunContext, RunMode
from .script_metadata import infer_script_metadata


ToolBody = Callable[[dict[str, object]], object]


@dataclass(frozen=True)
class WebToolDefinition:
    name: str
    title: str
    description: str
    fields: tuple[dict[str, object], ...]
    body: ToolBody
    source: str
    kind: str = "local"
    renderer: str = "json"
    resources: tuple[dict[str, str], ...] = ()


_TOOLS: dict[str, WebToolDefinition] = {}


def register_web_tool(
    *,
    name: str,
    title: str,
    description: str = "",
    fields: list[dict[str, object]] | tuple[dict[str, object], ...],
    kind: str = "local",
    renderer: str = "json",
) -> Callable[[ToolBody], ToolBody]:
    normalized = _name(name)
    normalized_fields = tuple(_field(item) for item in fields)
    normalized_kind = str(kind).strip().lower()
    if normalized_kind not in {"local", "workflow"}:
        raise ValueError("web tool kind must be local or workflow")
    normalized_renderer = str(renderer).strip().lower() or "json"
    if normalized_renderer not in {"json", "log_collection"}:
        raise ValueError("unsupported web tool renderer")
    if normalized_kind == "local" and normalized_renderer != "json":
        raise ValueError("local web tools only support the json renderer")

    def decorator(body: ToolBody) -> ToolBody:
        if normalized in _TOOLS:
            raise ValueError(f"web tool already registered: {normalized}")
        parameters = list(inspect.signature(body).parameters.values())
        if len(parameters) != 1:
            raise TypeError("web tool must accept exactly one argument")
        inferred = infer_script_metadata(body) if normalized_kind == "workflow" else None
        _TOOLS[normalized] = WebToolDefinition(
            name=normalized, title=str(title).strip() or normalized,
            description=str(description).strip(), fields=normalized_fields,
            body=body, source=inspect.getsourcefile(body) or "",
            kind=normalized_kind,
            renderer=normalized_renderer,
            resources=tuple(inferred.resources) if inferred else (),
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
         "fields": list(item.fields), "source": item.source, "kind": item.kind,
         "renderer": item.renderer, "resources": list(item.resources)}
        for item in discover_web_tools(root_dir)
    ]


def run_web_tool(root_dir: Path | str, name: str, values: dict[str, object]) -> object:
    definition = get_web_tool(root_dir, name)
    if definition.kind != "local":
        raise ValueError("workflow web tools must be started through the workflow API")
    if not isinstance(values, dict):
        raise TypeError("tool values must be an object")
    return definition.body(values)


def get_web_tool(root_dir: Path | str, name: str) -> WebToolDefinition:
    discover_web_tools(root_dir)
    try:
        return _TOOLS[name]
    except KeyError as exc:
        raise KeyError(f"unknown web tool: {name}") from exc


def run_workflow_tool(
    root_dir: Path | str,
    name: str,
    *,
    parameters: dict[str, object],
    console: TextIO | None = None,
) -> ScriptResult:
    definition = get_web_tool(root_dir, name)
    if definition.kind != "workflow":
        raise ValueError("local web tools cannot be started as workflows")
    root = Path(root_dir).resolve()
    context: RunContext | None = None
    internal_name = f"tool_{definition.name}"
    try:
        context = RunContext(
            root_dir=root,
            script_name=internal_name,
            mode=RunMode.RUN,
            console=console,
            parameters=parameters,
            non_interactive=True,
        )
        final_operation: object | None = None
        try:
            final_operation = definition.body(context)  # type: ignore[arg-type]
            if final_operation is None:
                success, status, error_type, error_message = True, "success", None, None
            elif all(hasattr(final_operation, item) for item in ("success", "status")):
                success = bool(final_operation.success)  # type: ignore[attr-defined]
                raw_status = final_operation.status  # type: ignore[attr-defined]
                status = str(getattr(raw_status, "value", raw_status))
                error_type = getattr(final_operation, "error_type", None)
                error_message = getattr(final_operation, "error_message", None)
            else:
                raise TypeError("workflow web tool must return None or an AutoEnv result object")
            context.commit_last_run()
        except Exception as exc:
            success, status = False, "program_error"
            error_type, error_message = type(exc).__name__, str(exc)
            context.recorder.log("UNHANDLED EXCEPTION\n" + "".join(traceback.format_exception(exc)))
        context.close()
        finished = datetime.now().astimezone()
        result = ScriptResult(
            run_id=context.run_id,
            script_name=definition.name,
            success=success,
            status=status,
            run_dir=str(context.run_dir),
            package_dir=str(context.package_dir),
            started_at=context.started_at,
            finished_at=finished,
            duration_ms=max(0, int((finished - context.started_at).total_seconds() * 1000)),
            final_operation=final_operation,
            error_type=error_type,
            error_message=error_message,
        )
        context.recorder.write_json(context.result_path, result_to_dict(result))
        context.recorder.console_block(
            "TOOL END",
            (f"name: {definition.name}", f"status: {status}", f"run_dir: {context.run_dir}"),
            state="SUCCESS" if success else "FAILED",
        )
        context.finish_recording()
        return result
    finally:
        if context is not None:
            context.close()
            context.finish_recording()


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
