from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from .registry import get_script, run_script
from .results import ScriptResult, result_to_dict
from .runtime import RunMode


@dataclass(frozen=True)
class LaunchRequest:
    script: str
    mode: str = "run"
    environment: str | None = None
    parameters: dict[str, object] | None = None

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "LaunchRequest":
        if not isinstance(value, dict):
            raise TypeError("launch request must be a JSON object")
        script = str(value.get("script", "")).strip()
        mode = str(value.get("mode", "run")).strip()
        environment = value.get("environment")
        parameters = value.get("parameters", {})
        if not script:
            raise ValueError("launch request requires script")
        if mode not in {item.value for item in RunMode}:
            raise ValueError("launch request mode must be run or rerun")
        if environment is not None and not isinstance(environment, str):
            raise TypeError("launch request environment must be a string")
        if not isinstance(parameters, dict):
            raise TypeError("launch request parameters must be an object")
        return cls(script, mode, environment.strip() if environment else None, dict(parameters))


def load_request(path: Path | str) -> LaunchRequest:
    with Path(path).open("r", encoding="utf-8") as handle:
        return LaunchRequest.from_dict(json.load(handle))


def load_environment(root_dir: Path, name: str) -> dict[str, object]:
    safe_name = Path(name).name
    if safe_name != name or safe_name in {"", ".", ".."}:
        raise ValueError("environment name must be a plain filename stem")
    path = root_dir / "environments" / f"{safe_name}.json"
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"environment file must contain an object: {path}")
    return value


def merge_parameters(environment: dict[str, object], overrides: dict[str, object]) -> dict[str, object]:
    merged: dict[str, object] = {}
    for section in ("ssh_hosts", "telnet_connections", "ftp_hosts", "packages", "arguments"):
        base = environment.get(section, {})
        override = overrides.get(section, {})
        if base is not None and not isinstance(base, dict):
            raise ValueError(f"environment section {section} must be an object")
        if override is not None and not isinstance(override, dict):
            raise ValueError(f"request parameter section {section} must be an object")
        section_value: dict[str, object] = dict(base or {})
        for key, value in dict(override or {}).items():
            if isinstance(section_value.get(key), dict) and isinstance(value, dict):
                section_value[key] = {**section_value[key], **value}  # type: ignore[arg-type]
            else:
                section_value[key] = value
        merged[section] = section_value
    return merged


def launch(request: LaunchRequest, *, root_dir: Path | str, console: TextIO | None = None) -> ScriptResult:
    root = Path(root_dir).resolve()
    get_script(request.script, root_dir=root)
    environment = load_environment(root, request.environment) if request.environment else {}
    parameters = merge_parameters(environment, request.parameters or {})
    return run_script(
        request.script,
        mode=request.mode,
        root_dir=root,
        console=console,
        parameters=parameters,
        non_interactive=True,
    )


def result_json(result: ScriptResult) -> str:
    return json.dumps(result_to_dict(result), ensure_ascii=False, indent=2)
