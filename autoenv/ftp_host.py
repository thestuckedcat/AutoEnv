from __future__ import annotations

import ftplib
import hashlib
import math
import posixpath
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .recorder import RunRecorder
from .results import UploadResult
from .selectors import LocalFileSelector, SelectorResolutionError, describe_selector, resolve_local_file


def _text(value: str, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    return value


def _port(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("FTP port must be an integer between 1 and 65535")
    return value


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("FTP timeout must be a number")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("FTP timeout must be finite and greater than zero")
    return value


@dataclass(frozen=True)
class FTPDefaults:
    host: str = ""
    port: int = 21
    username: str = "anonymous"
    password: str = ""
    timeout: float = 30.0
    passive: bool = True


@dataclass(frozen=True)
class FTPConnectionInfo:
    host: str
    port: int = 21
    username: str = "anonymous"
    password: str = ""
    timeout: float = 30.0
    passive: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", _text(self.host, "FTP host"))
        object.__setattr__(self, "port", _port(self.port))
        object.__setattr__(self, "username", _text(self.username, "FTP username"))
        object.__setattr__(self, "password", _text(self.password, "FTP password", allow_empty=True))
        object.__setattr__(self, "timeout", _timeout(self.timeout))
        if not isinstance(self.passive, bool):
            raise TypeError("FTP passive must be a boolean")


class FTPHost:
    """Lazily connected plain FTP upload target owned by one RunContext."""

    def __init__(
        self,
        *,
        name: str,
        info: FTPConnectionInfo,
        run_id: str,
        package_dir: Path,
        recorder: RunRecorder,
        image_pattern_for: Callable[[str], str],
        ftp_factory: Callable[[], Any] = ftplib.FTP,
    ) -> None:
        self.name = name
        self.info = info
        self.run_id = run_id
        self.package_dir = package_dir.resolve()
        self.recorder = recorder
        self.image_pattern_for = image_pattern_for
        self.ftp_factory = ftp_factory
        self._ftp: Any | None = None
        self._closed = False

    def upload(self, local_file: LocalFileSelector, remote_dir: str, overwrite: bool = True) -> UploadResult:
        selector_type, selector_value = describe_selector(local_file)
        if not isinstance(overwrite, bool):
            raise TypeError("overwrite must be a boolean")
        remote_dir = _normalize_remote_dir(remote_dir)
        operation_id = self.recorder.next_operation_id()
        started = datetime.now().astimezone()
        resolved_path: Path | None = None
        remote_file: str | None = None
        remote_existed = False
        local_md5: str | None = None
        remote_size: int | None = None
        success = False
        status = "upload_failed"
        error_type: str | None = None
        error_message: str | None = None
        try:
            resolved_path = resolve_local_file(local_file, self.package_dir, self.image_pattern_for).path
            local_md5 = _md5(resolved_path)
            remote_file = posixpath.join(remote_dir, resolved_path.name) if remote_dir != "." else resolved_path.name
            ftp = self._connect()
            self._mkdir_recursive(ftp, remote_dir)
            remote_existed = self._exists(ftp, remote_file)
            if remote_existed and not overwrite:
                status = "remote_file_exists"
                error_type = "REMOTE_FILE_EXISTS"
                error_message = f"remote file already exists: {remote_file}"
            else:
                with resolved_path.open("rb") as handle:
                    ftp.storbinary(f"STOR {remote_file}", handle)
                remote_size = ftp.size(remote_file)
                success = remote_size == resolved_path.stat().st_size
                status = "success" if success else "size_verification_failed"
                if not success:
                    error_type = "SIZE_VERIFICATION_FAILED"
                    error_message = f"uploaded size mismatch: local={resolved_path.stat().st_size}, remote={remote_size}"
        except SelectorResolutionError as exc:
            status, error_type, error_message = exc.code.lower(), exc.code, str(exc)
        except ftplib.error_perm as exc:
            status, error_type, error_message = "ftp_rejected", "FTP_REJECTED", str(exc)
        except Exception as exc:
            status, error_type, error_message = "upload_failed", type(exc).__name__, str(exc) or repr(exc)
            self.close()
        finished = datetime.now().astimezone()
        result = UploadResult(
            run_id=self.run_id, operation_id=operation_id, protocol="ftp", target_name=self.name,
            selector_type=selector_type, selector=selector_value, package_dir=str(self.package_dir),
            remote_dir=remote_dir, success=success, status=status, overwrite=overwrite,
            started_at=started, finished_at=finished,
            duration_ms=max(0, int((finished-started).total_seconds()*1000)),
            resolved_local_file=str(resolved_path) if resolved_path else None, remote_file=remote_file,
            remote_existed=remote_existed, local_md5=local_md5, remote_size=remote_size,
            size_verified=success, error_type=error_type, error_message=error_message,
        )
        self.recorder.record_result("FTP UPLOAD", result)
        return result

    def _connect(self) -> Any:
        if self._closed:
            raise RuntimeError(f"FTP host {self.name!r} is closed")
        if self._ftp is not None:
            return self._ftp
        ftp = self.ftp_factory()
        ftp.connect(self.info.host, self.info.port, timeout=self.info.timeout)
        ftp.login(self.info.username, self.info.password)
        ftp.set_pasv(self.info.passive)
        self._ftp = ftp
        return ftp

    @classmethod
    def _mkdir_recursive(cls, ftp: Any, remote_dir: str) -> None:
        if remote_dir in {"", ".", "/"}:
            return
        current = "/" if remote_dir.startswith("/") else ""
        for part in remote_dir.strip("/").split("/"):
            current = posixpath.join(current, part)
            try:
                ftp.mkd(current)
            except ftplib.error_perm as exc:
                if not str(exc).startswith("550"):
                    raise

    @staticmethod
    def _exists(ftp: Any, remote_file: str) -> bool:
        try:
            return ftp.size(remote_file) is not None
        except ftplib.error_perm as exc:
            if str(exc).startswith("550"):
                return False
            raise

    def close(self) -> None:
        ftp, self._ftp = self._ftp, None
        self._closed = True
        if ftp is not None:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass


def _normalize_remote_dir(value: str) -> str:
    value = _text(value, "FTP remote_dir")
    if "\0" in value:
        raise ValueError("FTP remote_dir must not contain NUL")
    return posixpath.normpath(value.replace("\\", "/"))


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
