from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def filename_only(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError("filename must be a nonempty basename")
    if any(char in value for char in ("/", "\\", ":", "\x00")):
        raise ValueError("filename must not contain a path")
    return value


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FileHandle:
    """One exact, immutable local file reference; never owns user file deletion."""

    path: Path
    filename: str = field(init=False)
    size: int = field(init=False)
    sha256: str = field(init=False)

    def __post_init__(self) -> None:
        raw = Path(self.path)
        if raw.is_symlink():
            raise ValueError("file handlers do not accept symbolic links")
        path = raw.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "filename", filename_only(path.name))
        object.__setattr__(self, "size", path.stat().st_size)
        object.__setattr__(self, "sha256", _digest(path))

    def verify(self) -> None:
        if not self.path.is_file() or self.path.is_symlink():
            raise FileNotFoundError(self.path)
        if self.path.stat().st_size != self.size or _digest(self.path) != self.sha256:
            raise ValueError(f"file changed since handler creation: {self.path}")


def _directory(path: str | os.PathLike[str]) -> Path:
    from .script_session import current_session

    session = current_session()
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else (session.root_dir if session else Path.cwd()) / candidate).resolve()


def get_file(path: str | os.PathLike[str], filename: str) -> FileHandle:
    from .script_session import operation_scope

    with operation_scope(f"get_file {filename}"):
        return _record_handle(FileHandle(_directory(path) / filename_only(filename)))


def create_file(path: str | os.PathLike[str], filename: str, content: str | bytes) -> FileHandle:
    from .script_session import operation_scope

    if not isinstance(content, (str, bytes)):
        raise TypeError("content must be str or bytes")
    with operation_scope(f"create_file {filename}"):
        directory = _directory(path)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename_only(filename)
        fd, temporary = tempfile.mkstemp(prefix=".autoenv-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content.encode("utf-8") if isinstance(content, str) else content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return _record_handle(FileHandle(target))


def _record_handle(handle: FileHandle) -> FileHandle:
    from .script_session import current_session

    session = current_session()
    if session:
        session.recorder.record_result("LOCAL FILE", {
            "operation_id": session.recorder.next_operation_id(), "status": "success", "success": True,
            "local_file": str(handle.path), "filename": handle.filename,
            "size": handle.size, "sha256": handle.sha256,
        })
    return handle


def hdfs_download(path: str | os.PathLike[str], config_json_name: str) -> FileHandle:
    """Reuse PackageManager's proven WebHDFS selection and atomic transfer."""
    import io

    from .errors import AutoEnvOperationError
    from .package_manager import PackageManager
    from .recorder import RunRecorder
    from .script_session import current_session, operation_scope
    from .selectors import package

    with operation_scope(f"hdfs_download {config_json_name}"):
        session = current_session()
        directory = _directory(path)
        recorder = session.recorder if session else RunRecorder(directory / "hdfs-download.log", console=io.StringIO())
        try:
            manager = PackageManager(
                (session.root_dir if session else Path.cwd()) / "config.json",
                directory,
                session.run_id if session else "standalone-hdfs",
                recorder,
            )
            override = session.packages.get(config_json_name) if session else None
            result = manager.download(package(config_json_name), path_override=override)
            if not result.success:
                raise AutoEnvOperationError(result)
            return FileHandle(Path(result.local_file))
        finally:
            if session is None:
                recorder.close()
