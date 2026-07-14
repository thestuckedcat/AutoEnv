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

    def stream(self, prefix: str, text: str, *, console: bool = True) -> None:
        if not text:
            return
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        for line in normalized.splitlines(keepends=False):
            self.log(f"{prefix} {line}", console=console)

    def record_result(self, operation: str, result: Any) -> None:
        data = json.dumps(result_to_dict(result), ensure_ascii=False, sort_keys=True)
        self.log(f"{operation} {data}")

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
