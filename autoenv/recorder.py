from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, TextIO

from .results import result_to_dict


SENSITIVE_KEYS = {"password", "passwd", "secret", "token"}


class RunRecorder:
    """Internal run logger. Environment scripts intentionally do not receive it."""

    def __init__(
        self,
        log_path: Path,
        *,
        console: TextIO | None = None,
        now: callable = datetime.now,
    ) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._console = console if console is not None else sys.stdout
        self._now = now
        self._lock = Lock()
        self._operation = 0
        self._file = self.log_path.open("a", encoding="utf-8", newline="")

    def next_operation_id(self) -> str:
        with self._lock:
            self._operation += 1
            return f"{self._operation:04d}"

    @property
    def final_operation_id(self) -> str | None:
        with self._lock:
            return f"{self._operation:04d}" if self._operation else None

    def log(self, message: str, *, console: bool = True) -> None:
        timestamp = self._now().astimezone().isoformat(timespec="milliseconds")
        line = f"[{timestamp}] {message}"
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()
            if console and self._console is not None:
                print(line, file=self._console, flush=True)

    def console_block(
        self,
        title: str,
        lines: list[str] | tuple[str, ...],
        *,
        state: str | None = None,
    ) -> None:
        if self._console is None:
            return
        timestamp = self._now().astimezone().isoformat(timespec="milliseconds")
        suffix = f" [{state}]" if state else ""
        with self._lock:
            print(file=self._console)
            print(
                f"[{timestamp}] === {title}{suffix} ===",
                file=self._console,
            )
            for line in lines:
                print(f"  {line}", file=self._console)
            print(file=self._console, flush=True)

    def stream(self, prefix: str, text: str, *, console: bool = True) -> None:
        if not text:
            return
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        for line in normalized.splitlines(keepends=False):
            self.log(f"{prefix} {line}", console=console)

    def record_result(self, operation: str, result: Any) -> None:
        serializable = result_to_dict(result)
        data = json.dumps(serializable, ensure_ascii=False, sort_keys=True)
        self.log(f"{operation} {data}", console=False)
        state, lines = _format_console_result(serializable)
        self.console_block(operation, lines, state=state)

    def write_json(self, path: Path, value: Any, *, mask: bool = False) -> None:
        serializable = result_to_dict(value)
        if mask:
            serializable = mask_sensitive(serializable)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(serializable, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def close(self) -> None:
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                self._file.close()

    def __enter__(self) -> "RunRecorder":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            if any(part in key.lower() for part in SENSITIVE_KEYS):
                masked[key] = "******" if item not in (None, "") else item
            else:
                masked[key] = mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    return value


def _format_console_result(value: Any) -> tuple[str, list[str]]:
    if not isinstance(value, dict):
        return "RESULT", [str(value)]

    success = value.get("success")
    if success is None:
        success = value.get("status") == "success"
    state = "SUCCESS" if success else "FAILED"
    lines: list[str] = []
    operation_id = value.get("operation_id")
    if operation_id is not None:
        lines.append(f"operation_id: {operation_id}")

    if "command" in value:
        _append_command_summary(lines, value)
    elif "remote_file" in value and "protocol" in value:
        _append_upload_summary(lines, value)
    elif "config_name" in value:
        _append_download_summary(lines, value)
    elif "destination" in value:
        _append_extract_summary(lines, value)
    else:
        lines.append(f"status: {value.get('status', 'unknown')}")
        _append_if_present(lines, "duration_ms", value.get("duration_ms"))

    error_type = value.get("error_type")
    error_message = value.get("error_message")
    if error_type or error_message:
        detail = ": ".join(str(item) for item in (error_type, error_message) if item)
        lines.append(f"error: {detail}")
    return state, lines


def _append_command_summary(lines: list[str], value: dict[str, Any]) -> None:
    protocol = str(value.get("protocol", "command")).upper()
    target = value.get("target_name")
    lines.append(f"target: {protocol} {target}" if target else f"protocol: {protocol}")
    lines.append("command:")
    command_lines = str(value.get("command", "")).splitlines() or [""]
    lines.extend(f"  {line}" for line in command_lines)
    result_parts = [
        f"status={value.get('status', 'unknown')}",
        f"phase={value.get('phase', 'unknown')}",
    ]
    if value.get("exit_code") is not None:
        result_parts.append(f"exit_code={value['exit_code']}")
    result_parts.append(f"duration_ms={value.get('duration_ms', 0)}")
    lines.append("result: " + " ".join(result_parts))


def _append_upload_summary(lines: list[str], value: dict[str, Any]) -> None:
    protocol = str(value.get("protocol", "upload")).upper()
    target = value.get("target_name")
    lines.append(f"target: {protocol} {target}" if target else f"protocol: {protocol}")
    _append_if_present(lines, "source", value.get("resolved_local_file"))
    _append_if_present(lines, "destination", value.get("remote_file"))
    lines.append(
        "result: "
        f"status={value.get('status', 'unknown')} "
        f"duration_ms={value.get('duration_ms', 0)}"
    )
    local_md5 = value.get("local_md5")
    remote_md5 = value.get("remote_md5_after")
    if local_md5 or remote_md5:
        verified = "yes" if value.get("md5_verified") else "no"
        lines.append(
            f"md5: verified={verified} local={local_md5 or '-'} "
            f"remote={remote_md5 or '-'}"
        )


def _append_download_summary(lines: list[str], value: dict[str, Any]) -> None:
    _append_if_present(lines, "package", value.get("config_name"))
    _append_if_present(lines, "source", value.get("remote_file"))
    _append_if_present(lines, "destination", value.get("local_file"))
    lines.append(
        "result: "
        f"status={value.get('status', 'unknown')} "
        f"size={value.get('local_size') or 0} "
        f"duration_ms={value.get('duration_ms', 0)}"
    )
    _append_if_present(lines, "md5", value.get("local_md5_after"))


def _append_extract_summary(lines: list[str], value: dict[str, Any]) -> None:
    _append_if_present(lines, "source", value.get("source_file"))
    _append_if_present(lines, "destination", value.get("destination"))
    lines.append(
        "result: "
        f"status={value.get('status', 'unknown')} "
        f"duration_ms={value.get('duration_ms', 0)}"
    )


def _append_if_present(lines: list[str], label: str, value: Any) -> None:
    if value is not None and value != "":
        lines.append(f"{label}: {value}")
