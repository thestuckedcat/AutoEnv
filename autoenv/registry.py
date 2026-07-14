from __future__ import annotations

import contextvars
import functools
import getpass
import importlib
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
    result_to_dict,
)
from .runtime import DEFAULT_PACKAGE_CACHE_LIMIT, LastRunNotFoundError, RunContext, RunMode


ScriptBody = Callable[[RunContext], object | None]


@dataclass(frozen=True)
class ScriptDefinition:
    name: str
    description: str
    body: ScriptBody
    entrypoint: Callable[[], ScriptResult]


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


def register_script(
    name: str,
    description: str = "",
) -> Callable[[ScriptBody], Callable[[], ScriptResult]]:
    normalized = _validate_script_name(name)
    if not isinstance(description, str):
        raise TypeError("script description must be a string")

    def decorator(body: ScriptBody) -> Callable[[], ScriptResult]:
        if normalized in _REGISTRY:
            raise ValueError(f"script already registered: {normalized}")

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
        )
        return entrypoint

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
        error_type: str | None = None
        error_message: str | None = None
        try:
            returned = definition.body(context)
            final_operation = _validate_body_result(returned)
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
        }
        context.recorder.write_json(context.result_path, result_to_dict(result_summary))
        context.recorder.log(
            f"SCRIPT END name={definition.name} status={status} success={success} "
            f"duration_ms={result.duration_ms}"
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


def _validate_body_result(value: object | None) -> object | None:
    allowed = (CommandResult, DownloadResult, UploadResult, ExtractResult, ScriptResult)
    if value is None or isinstance(value, allowed):
        return value
    raise TypeError(
        "registered script must return None or an AutoEnv result object, "
        f"not {type(value).__name__}"
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


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return max(0, int((finished_at - started_at).total_seconds() * 1000))
