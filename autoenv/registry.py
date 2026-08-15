from __future__ import annotations

import contextvars
import functools
import getpass
import importlib
import inspect
import pkgutil
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TextIO

from .results import (
    CommandResult,
    DownloadResult,
    ExtractResult,
    ScriptResult,
    UploadResult,
    RemoteDownloadResult,
    result_to_dict,
)
from .runtime import DEFAULT_PACKAGE_CACHE_LIMIT, LastRunNotFoundError, RunContext, RunMode
from .script_metadata import infer_script_metadata


ScriptBody = Callable[[RunContext], object | None]
FuncBody = Callable[[RunContext], object | None]


@dataclass(frozen=True)
class ScriptDefinition:
    name: str
    description: str
    body: ScriptBody
    entrypoint: Callable[[], ScriptResult]
    packages: tuple[str, ...] = ()
    package_inputs: tuple[dict[str, str], ...] = ()
    parameters: tuple[dict[str, object], ...] = ()
    resources: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class FuncDefinition:
    name: str
    description: str
    body: FuncBody


_REGISTRY: dict[str, ScriptDefinition] = {}
_DISCOVERED_ROOTS: set[Path] = set()
_CURRENT_MODE: contextvars.ContextVar[RunMode] = contextvars.ContextVar(
    "autoenv_run_mode", default=RunMode.RUN
)
_CURRENT_ROOT: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "autoenv_root", default=None
)
_CURRENT_OPTIONS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "autoenv_run_options", default=None
)
_CURRENT_FUNCS: contextvars.ContextVar[list[FuncDefinition] | None] = (
    contextvars.ContextVar("autoenv_registered_funcs", default=None)
)


def register_script(
    name: str,
    description: str = "",
    *,
    packages: tuple[str | dict[str, object], ...] | list[str | dict[str, object]] = (),
    parameters: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
    resources: tuple[dict[str, object], ...] | list[dict[str, object]] = (),
) -> Callable[[ScriptBody], Callable[[], ScriptResult]]:
    normalized = _validate_script_name(name)
    if not isinstance(description, str):
        raise TypeError("script description must be a string")
    normalized_package_inputs = tuple(_validate_package_input(item) for item in packages)
    normalized_packages = tuple(item["name"] for item in normalized_package_inputs)
    if len(set(normalized_packages)) != len(normalized_packages):
        raise ValueError("script package names must be unique")
    normalized_parameters = tuple(_validate_parameter(item) for item in parameters)
    normalized_resources = tuple(_validate_resource(item) for item in resources)
    if len({item["name"] for item in normalized_resources}) != len(normalized_resources):
        raise ValueError("script resource names must be unique")

    def decorator(body: ScriptBody) -> Callable[[], ScriptResult]:
        if normalized in _REGISTRY:
            raise ValueError(f"script already registered: {normalized}")

        inferred = infer_script_metadata(body)
        inherited_package_inputs: list[dict[str, object]] = []
        inherited_parameters: list[dict[str, object]] = []
        inherited_resources: list[dict[str, object]] = []
        for called_name in inferred.called_functions:
            called = body.__globals__.get(called_name)
            child_name = getattr(called, "__autoenv_script_name__", None)
            child = _REGISTRY.get(child_name) if isinstance(child_name, str) else None
            if child is None or child.name == normalized:
                continue
            inherited_package_inputs.extend(child.package_inputs)
            inherited_parameters.extend(child.parameters)
            inherited_resources.extend(child.resources)

        final_package_inputs = tuple(
            _validate_package_input(item)
            for item in _merge_named_metadata(
                [*inherited_package_inputs, *inferred.packages],
                list(normalized_package_inputs),
                "package",
            )
        )
        final_parameters = tuple(
            _validate_parameter(item)
            for item in _merge_named_metadata(
                inherited_parameters,
                list(normalized_parameters),
                "parameter",
            )
        )
        final_resources = tuple(
            _validate_resource(item)
            for item in _merge_named_metadata(
                [*inherited_resources, *inferred.resources],
                list(normalized_resources),
                "resource",
            )
        )

        @functools.wraps(body)
        def entrypoint() -> ScriptResult:
            root = _CURRENT_ROOT.get() or Path.cwd()
            options = dict(_CURRENT_OPTIONS.get() or {})
            return run_script(
                normalized,
                mode=_CURRENT_MODE.get(),
                root_dir=root,
                **options,
            )

        _REGISTRY[normalized] = ScriptDefinition(
            name=normalized,
            description=description.strip(),
            body=body,
            entrypoint=entrypoint,
            packages=tuple(item["name"] for item in final_package_inputs),
            package_inputs=final_package_inputs,
            parameters=final_parameters,
            resources=final_resources,
        )
        setattr(entrypoint, "__autoenv_script_name__", normalized)
        return entrypoint

    return decorator


def register_func(
    name: str,
    description: str = "",
) -> Callable[[FuncBody], FuncBody]:
    """Register an interactive post-start function for the active script run."""

    normalized = _validate_func_name(name)
    if not isinstance(description, str):
        raise TypeError("func description must be a string")

    def decorator(body: FuncBody) -> FuncBody:
        if not callable(body):
            raise TypeError("registered func body must be callable")
        parameters = list(inspect.signature(body).parameters.values())
        if (
            len(parameters) != 1
            or parameters[0].kind
            not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ):
            raise TypeError("registered func must accept exactly one RunContext argument")
        registered = _CURRENT_FUNCS.get()
        if registered is None:
            raise RuntimeError(
                "register_func() must be called inside a running registered script"
            )
        if any(item.name == normalized for item in registered):
            raise ValueError(f"func already registered in this run: {normalized}")
        registered.append(
            FuncDefinition(
                name=normalized,
                description=description.strip(),
                body=body,
            )
        )
        return body

    return decorator


def list_scripts(*, root_dir: Path | None = None) -> list[ScriptDefinition]:
    discover_scripts(root_dir=root_dir)
    return [_REGISTRY[name] for name in sorted(_REGISTRY)]


def get_script(name: str, *, root_dir: Path | None = None) -> ScriptDefinition:
    discover_scripts(root_dir=root_dir)
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown AutoEnv script: {name}") from exc


def discover_scripts(*, root_dir: Path | None = None) -> None:
    root = (root_dir or Path.cwd()).resolve()
    if root in _DISCOVERED_ROOTS:
        return
    if _DISCOVERED_ROOTS:
        active = next(iter(_DISCOVERED_ROOTS))
        raise RuntimeError(
            f"AutoEnv script registry is already bound to {active}; "
            f"cannot discover a second project root {root} in the same process"
        )
    scripts_dir = root / "scripts"
    if not scripts_dir.is_dir():
        _DISCOVERED_ROOTS.add(root)
        return
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        package_module = importlib.import_module("scripts")
        for module in sorted(pkgutil.iter_modules(package_module.__path__), key=lambda item: item.name):
            if not module.name.startswith("_"):
                try:
                    importlib.import_module(f"scripts.{module.name}")
                except Exception as exc:
                    raise RuntimeError(
                        f"failed to import registered script module scripts.{module.name}: {exc}"
                    ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"failed to import scripts package from {root}: {exc}") from exc
    _DISCOVERED_ROOTS.add(root)


def run_script(
    name: str,
    *,
    mode: RunMode | str = RunMode.RUN,
    root_dir: Path | str | None = None,
    input_func: Callable[[str], str] = input,
    password_input: Callable[[str], str] = getpass.getpass,
    console: TextIO | None = None,
    package_cache_limit: int = DEFAULT_PACKAGE_CACHE_LIMIT,
    hdfs_client: object | None = None,
    parameters: dict[str, object] | None = None,
    non_interactive: bool = False,
) -> ScriptResult:
    root = Path(root_dir or Path.cwd()).resolve()
    definition = get_script(name, root_dir=root)
    selected_mode = RunMode(mode)
    options = {
        "input_func": input_func,
        "password_input": password_input,
        "console": console,
        "package_cache_limit": package_cache_limit,
        "hdfs_client": hdfs_client,
        "parameters": parameters,
        "non_interactive": non_interactive,
    }
    mode_token = _CURRENT_MODE.set(selected_mode)
    root_token = _CURRENT_ROOT.set(root)
    options_token = _CURRENT_OPTIONS.set(options)
    started_at = datetime.now().astimezone()
    context: RunContext | None = None
    try:
        try:
            context = RunContext(
                root_dir=root,
                script_name=definition.name,
                mode=selected_mode,
                **options,
            )
        except LastRunNotFoundError as exc:
            finished_at = datetime.now().astimezone()
            return ScriptResult(
                run_id="",
                script_name=definition.name,
                success=False,
                status="last_run_not_found",
                run_dir="",
                package_dir="",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=_duration_ms(started_at, finished_at),
                error_type="LAST_RUN_NOT_FOUND",
                error_message=str(exc),
            )
        except Exception as exc:
            finished_at = datetime.now().astimezone()
            return ScriptResult(
                run_id="",
                script_name=definition.name,
                success=False,
                status="program_error",
                run_dir="",
                package_dir="",
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=_duration_ms(started_at, finished_at),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        final_operation: object | None = None
        func_runs: list[dict[str, object]] = []
        error_type: str | None = None
        error_message: str | None = None
        try:
            registered_funcs: list[FuncDefinition] = []
            funcs_token = _CURRENT_FUNCS.set(registered_funcs)
            try:
                returned = definition.body(context)
            finally:
                _CURRENT_FUNCS.reset(funcs_token)
            final_operation = _validate_body_result(returned, label="registered script")
            if isinstance(final_operation, ScriptResult):
                success = final_operation.success
                status = final_operation.status
                error_type = final_operation.error_type
                error_message = final_operation.error_message
            elif final_operation is None:
                success = True
                status = "success"
            else:
                success = final_operation.success  # type: ignore[attr-defined]
                raw_status = final_operation.status  # type: ignore[attr-defined]
                status = getattr(raw_status, "value", str(raw_status))
                error_type = final_operation.error_type  # type: ignore[attr-defined]
                error_message = final_operation.error_message  # type: ignore[attr-defined]
            if success and registered_funcs:
                func_runs = _run_func_menu(context, registered_funcs)
            context.commit_last_run()
        except Exception as exc:
            success = False
            status = "program_error"
            error_type = type(exc).__name__
            error_message = str(exc)
            context.recorder.log(
                "UNHANDLED EXCEPTION\n" + "".join(traceback.format_exception(exc))
            )
            if context.recorder.final_operation_id is not None:
                try:
                    context.commit_last_run()
                except Exception as save_exc:
                    context.recorder.log(
                        f"LAST RUN UPDATE FAILED after exception: {save_exc}"
                    )

        context.close()
        finished_at = datetime.now().astimezone()
        result = ScriptResult(
            run_id=context.run_id,
            script_name=definition.name,
            success=success,
            status=status,
            run_dir=str(context.run_dir),
            package_dir=str(context.package_dir),
            started_at=context.started_at,
            finished_at=finished_at,
            duration_ms=_duration_ms(context.started_at, finished_at),
            final_operation=final_operation,
            error_type=error_type,
            error_message=error_message,
        )
        result_summary = {
            "run_id": result.run_id,
            "script_name": result.script_name,
            "success": result.success,
            "status": result.status,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "duration_ms": result.duration_ms,
            "final_operation_id": context.recorder.final_operation_id,
            "error_type": result.error_type,
            "error_message": result.error_message,
            "func_runs": func_runs,
        }
        context.recorder.write_json(context.result_path, result_to_dict(result_summary))
        context.recorder.log(
            f"SCRIPT END name={definition.name} status={status} success={success} "
            f"duration_ms={result.duration_ms}",
            console=False,
        )
        context.recorder.console_block(
            "SCRIPT END",
            (
                f"name: {definition.name}",
                f"status: {status}",
                f"duration_ms: {result.duration_ms}",
                f"run_dir: {context.run_dir}",
            ),
            state="SUCCESS" if success else "FAILED",
        )
        context.finish_recording()
        return result
    finally:
        if context is not None:
            context.close()
            context.finish_recording()
        _CURRENT_OPTIONS.reset(options_token)
        _CURRENT_ROOT.reset(root_token)
        _CURRENT_MODE.reset(mode_token)


def clear_registry_for_tests() -> None:
    _REGISTRY.clear()
    _DISCOVERED_ROOTS.clear()


def _validate_body_result(
    value: object | None,
    *,
    label: str,
) -> object | None:
    allowed = (
        CommandResult,
        DownloadResult,
        RemoteDownloadResult,
        UploadResult,
        ExtractResult,
        ScriptResult,
    )
    if value is None or isinstance(value, allowed):
        return value
    raise TypeError(
        f"{label} must return None or an AutoEnv result object, "
        f"not {type(value).__name__}"
    )


def _run_func_menu(
    context: RunContext,
    registered: list[FuncDefinition],
) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    while True:
        context.recorder.log("AVAILABLE FUNCS")
        for index, definition in enumerate(registered, start=1):
            context.recorder.log(
                f"{index}. {definition.name:<24} {definition.description}"
            )
        context.recorder.log("0. exit")
        try:
            answer = context.input_func("Select a func: ").strip()
        except (EOFError, KeyboardInterrupt):
            context.recorder.log("FUNC MENU EXIT input_closed")
            return runs
        try:
            selected = int(answer)
        except ValueError:
            context.recorder.log("FUNC MENU INVALID selection_must_be_number")
            continue
        if selected == 0:
            context.recorder.log("FUNC MENU EXIT selected")
            return runs
        if not 1 <= selected <= len(registered):
            context.recorder.log("FUNC MENU INVALID selection_out_of_range")
            continue

        definition = registered[selected - 1]
        context.recorder.log(f"FUNC START name={definition.name}")
        try:
            returned = definition.body(context)
            result = _validate_body_result(returned, label="registered func")
            if result is None:
                success = True
                status = "success"
                error_type = None
                error_message = None
            else:
                success = bool(result.success)  # type: ignore[attr-defined]
                raw_status = result.status  # type: ignore[attr-defined]
                status = getattr(raw_status, "value", str(raw_status))
                error_type = result.error_type  # type: ignore[attr-defined]
                error_message = result.error_message  # type: ignore[attr-defined]
        except Exception as exc:
            success = False
            status = "program_error"
            error_type = type(exc).__name__
            error_message = str(exc)
            context.recorder.log(
                "FUNC UNHANDLED EXCEPTION\n" + "".join(traceback.format_exception(exc))
            )

        summary: dict[str, object] = {
            "name": definition.name,
            "success": success,
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
        }
        runs.append(summary)
        context.recorder.log(
            f"FUNC END name={definition.name} status={status} success={success}"
        )


def _validate_script_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("script name must be a string")
    normalized = name.strip()
    if not normalized:
        raise ValueError("script name must not be empty")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if any(character not in allowed for character in normalized):
        raise ValueError("script name may contain letters, digits, '_' and '-' only")
    return normalized


def _validate_func_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("func name must be a string")
    normalized = name.strip()
    if not normalized:
        raise ValueError("func name must not be empty")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if any(character not in allowed for character in normalized):
        raise ValueError("func name may contain letters, digits, '_' and '-' only")
    return normalized


def _validate_parameter(value: dict[str, object]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("script parameter metadata must be a dictionary")
    normalized = dict(value)
    name = normalized.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("script parameter metadata requires a non-empty name")
    normalized["name"] = name.strip()
    normalized.setdefault("type", "string")
    normalized.setdefault("required", False)
    return normalized


def _validate_resource(value: dict[str, object]) -> dict[str, str]:
    from .resources import validate_resource_label

    if not isinstance(value, dict):
        raise TypeError("script resource metadata must be a dictionary")
    name = _validate_script_name(value.get("name"))  # type: ignore[arg-type]
    protocol = str(value.get("protocol", "")).strip().lower()
    if protocol not in {"ssh", "telnet", "ftp"}:
        raise ValueError("script resource protocol must be ssh, telnet or ftp")
    label = validate_resource_label(value.get("label"), protocol=protocol)
    alias = _validate_optional_prompt_text(
        value.get("alias"), name, "script resource alias"
    )
    description = _validate_optional_prompt_text(
        value.get("description"), "", "script resource description"
    )
    return {
        "name": name,
        "alias": alias,
        "description": description,
        "label": label,
        "protocol": protocol,
    }


def _validate_package_input(value: str | dict[str, object]) -> dict[str, str]:
    if isinstance(value, str):
        name = _validate_script_name(value)
        return {"name": name, "alias": name, "description": ""}
    if not isinstance(value, dict):
        raise TypeError("script package metadata must be a string or dictionary")
    name = _validate_script_name(value.get("name"))  # type: ignore[arg-type]
    alias = _validate_optional_prompt_text(
        value.get("alias"), name, "script package alias"
    )
    description = _validate_optional_prompt_text(
        value.get("description"), "", "script package description"
    )
    return {"name": name, "alias": alias, "description": description}


def _validate_optional_prompt_text(value: object, default: str, label: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    return value.strip() or default


def _merge_named_metadata(
    inferred: list[dict[str, object]],
    explicit: list[dict[str, object]],
    label: str,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    indexes: dict[str, int] = {}
    for item in [*inferred, *explicit]:
        name = str(item.get("name", "")).strip()
        if name not in indexes:
            indexes[name] = len(result)
            result.append(dict(item))
            continue
        current = result[indexes[name]]
        if label == "resource":
            for field in ("protocol", "label"):
                old = str(current.get(field, "")).strip().lower()
                new = str(item.get(field, "")).strip().lower()
                if old and new and old != new:
                    raise ValueError(
                        f"conflicting script resource {field} for {name!r}: {old!r} != {new!r}"
                    )
        current.update(item)
    return result


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return max(0, int((finished_at - started_at).total_seconds() * 1000))
