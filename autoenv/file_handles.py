from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Callable, overload


def filename_only(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError("filename must be a nonempty basename")
    if any(char in value for char in ("/", "\\", ":", "\x00")):
        raise ValueError("filename must not contain a path")
    return value


def _digest(path: Path, *, check_cancelled: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if check_cancelled:
                check_cancelled()
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

    def verify(self, *, check_cancelled: Callable[[], None] | None = None) -> None:
        if not self.path.is_file() or self.path.is_symlink():
            raise FileNotFoundError(self.path)
        digest = _digest(self.path, check_cancelled=check_cancelled) if check_cancelled else _digest(self.path)
        if self.path.stat().st_size != self.size or digest != self.sha256:
            raise ValueError(f"file changed since handler creation: {self.path}")

    def unzip(
        self,
        unzip_path: str | os.PathLike[str],
        *,
        max_files: int = 10_000,
        max_bytes: int = 5 * 1024 * 1024 * 1024,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Path:
        """Expand one archive layer into a new or empty directory atomically."""
        from .local_archives import extract_archive
        from .script_session import operation_scope

        with operation_scope(f"unzip {self.path} -> {unzip_path}"):
            if check_cancelled:
                check_cancelled()
            self.verify(check_cancelled=check_cancelled)
            destination = extract_archive(
                self.path, _local_path(unzip_path), max_files=max_files, max_bytes=max_bytes,
                check_cancelled=check_cancelled,
            )
            _record_local("LOCAL UNZIP", source=str(self.path), destination=str(destination))
            return destination

    def move_to(self, destination_dir: str | os.PathLike[str]) -> FileHandle:
        """Copy this file, retaining the source; return the destination handle."""
        from .script_session import operation_scope

        with operation_scope(f"move_to copy {self.path} -> {destination_dir}"):
            self.verify()
            _assert_no_links(self.path)
            directory = _local_path(destination_dir)
            _assert_no_links(directory)
            directory.mkdir(parents=True, exist_ok=True)
            directory = _regular_directory(directory)
            target = directory / self.filename
            if target == self.path:
                raise ValueError("copy destination is the source file")
            if target.exists() or target.is_symlink():
                raise FileExistsError(target)
            fd, name = tempfile.mkstemp(prefix=".autoenv-copy-", dir=directory)
            os.close(fd)
            temporary = Path(name)
            try:
                shutil.copy2(self.path, temporary)
                if temporary.stat().st_size != self.size or _digest(temporary) != self.sha256:
                    raise ValueError(f"file changed while copying: {self.path}")
                # Both operations refuse a concurrently created destination.
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            result = _record_handle(FileHandle(target))
            _record_local("LOCAL COPY", source=str(self.path), destination=str(result.path))
            return result

    def rename(self, new_name: str) -> FileHandle:
        """Rename the actual file without overwriting; this handle stays immutable."""
        from .script_session import operation_scope

        with operation_scope(f"rename {self.path} -> {new_name}"):
            self.verify()
            name = filename_only(new_name)
            target = self.path.with_name(name)
            if target == self.path:
                raise ValueError("rename destination is the source file")
            if target.exists() or target.is_symlink():
                raise FileExistsError(target)
            _assert_no_links(target.parent)
            if os.name == "nt":
                os.rename(self.path, target)  # Windows rename refuses an existing target.
            else:
                os.link(self.path, target)
                try:
                    self.path.unlink()
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
            result = _record_handle(FileHandle(target))
            _record_local("LOCAL RENAME", source=str(self.path), destination=str(result.path))
            return result


class FileHandleGroup(Sequence[FileHandle]):
    """A sorted path snapshot with cached, lazy SHA-256 file handles."""

    def __init__(self, paths: Iterable[str | os.PathLike[str]], *, root: Path | None = None) -> None:
        candidates = tuple(Path(path).absolute() for path in paths)
        self._root = Path(root).absolute() if root is not None else None
        self._paths = tuple(sorted(candidates, key=self._sort_key))
        self._cache: dict[int, FileHandle] = {}
        self._lock = threading.RLock()

    def _sort_key(self, path: Path) -> tuple[str, str]:
        relative = path.relative_to(self._root).as_posix() if self._root is not None else path.as_posix()
        return path.name, relative

    @property
    def paths(self) -> tuple[Path, ...]:
        """Paths captured by the search; accessing them does not hash file contents."""
        return self._paths

    def __len__(self) -> int:
        return len(self._paths)

    @overload
    def __getitem__(self, index: int) -> FileHandle: ...

    @overload
    def __getitem__(self, index: slice) -> FileHandleGroup: ...

    def __getitem__(self, index: int | slice) -> FileHandle | FileHandleGroup:
        if isinstance(index, slice):
            result = FileHandleGroup(self._paths[index], root=self._root)
            # Preserve already captured integrity snapshots when slicing.
            with self._lock:
                cached = {handle.path: handle for handle in self._cache.values()}
            result._cache = {rank: cached[path] for rank, path in enumerate(result.paths) if path in cached}
            return result
        path = self._paths[index]  # Raises IndexError, including for an empty group.
        rank = index if index >= 0 else len(self._paths) + index
        with self._lock:
            if rank not in self._cache:
                _assert_no_links(path)
                self._cache[rank] = _record_handle(FileHandle(path))
            return self._cache[rank]

    def __iter__(self) -> Iterator[FileHandle]:
        for index in range(len(self)):
            yield self[index]


def _local_path(path: str | os.PathLike[str]) -> Path:
    """Resolve the session base without following filesystem links."""
    from .script_session import current_session

    session = current_session()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (session.root_dir if session else Path.cwd()) / candidate
    return Path(os.path.abspath(candidate))


def _is_link_stat(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _assert_no_links(path: Path) -> None:
    for component in (path, *path.parents):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        if _is_link_stat(metadata):
            raise ValueError(f"symbolic links and junctions are not allowed: {component}")


def _regular_directory(path: str | os.PathLike[str]) -> Path:
    root = _local_path(path)
    _assert_no_links(root)
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise NotADirectoryError(root)
    return root.resolve()


def _walk_paths(
    root: Path, recursive: bool, *, directories: bool = False, exclude_paths: tuple[Path, ...] = ()
) -> Iterator[Path]:
    if not isinstance(recursive, bool):
        raise TypeError("recursive must be bool")
    if any(root == excluded or excluded in root.parents for excluded in exclude_paths):
        return
    pending = [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if any(path == excluded or excluded in path.parents for excluded in exclude_paths):
                    continue
                metadata = entry.stat(follow_symlinks=False)
                if _is_link_stat(metadata):
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if directories:
                        yield path
                    if recursive:
                        pending.append(path)
                elif not directories and stat.S_ISREG(metadata.st_mode):
                    yield path


def _pattern(pattern: str) -> str:
    if not isinstance(pattern, str):
        raise TypeError("pattern must be a string")
    if any(char in pattern for char in ("/", "\\", ":", "\x00")):
        raise ValueError("pattern must match a filename, not a path")
    return pattern or "*"


def _matches(name: str, pattern: str, *, fuzzy: bool) -> bool:
    if any(char in pattern for char in "*?["):
        return fnmatch.fnmatchcase(name, pattern)
    return pattern in name if fuzzy else name == pattern


def fuzz_search(
    path: str | os.PathLike[str], pattern: str = "", recursive: bool = False
) -> FileHandleGroup:
    """Match basename keywords or shell wildcards, using case-sensitive ordering."""
    from .script_session import operation_scope

    with operation_scope(f"fuzz_search {path} {pattern!r} recursive={recursive}"):
        root = _regular_directory(path)
        value = _pattern(pattern)
        group = FileHandleGroup(
            (candidate for candidate in _walk_paths(root, recursive) if _matches(candidate.name, value, fuzzy=True)),
            root=root,
        )
        _record_local("LOCAL SEARCH", path=str(root), pattern=pattern, recursive=recursive, count=len(group))
        return group


def accurate_search(
    path: str | os.PathLike[str], pattern: str = "", recursive: bool = False
) -> FileHandle | None:
    """Return the first lexically sorted exact basename or wildcard match."""
    from .script_session import operation_scope

    with operation_scope(f"accurate_search {path} {pattern!r} recursive={recursive}"):
        root = _regular_directory(path)
        value = _pattern(pattern)
        group = FileHandleGroup(
            (candidate for candidate in _walk_paths(root, recursive) if _matches(candidate.name, value, fuzzy=False)),
            root=root,
        )
        _record_local("LOCAL SEARCH", path=str(root), pattern=pattern, recursive=recursive, count=len(group))
        return group[0] if group else None


def get_handler(path: str | os.PathLike[str], recursive: bool = False) -> FileHandleGroup:
    """Collect ordinary files into a stable, lazy handler group."""
    from .script_session import operation_scope

    with operation_scope(f"get_handler {path} recursive={recursive}"):
        return fuzz_search(path, "", recursive)


def find_subdir(
    root: str | os.PathLike[str], partial_path: str, recursive: bool = True,
    *, exclude_paths: Iterable[str | os.PathLike[str]] = (),
) -> Path:
    """Find one directory; multi-component patterns match full consecutive components."""
    from .script_session import operation_scope

    with operation_scope(f"find_subdir {root} {partial_path!r} recursive={recursive}"):
        base = _regular_directory(root)
        if not isinstance(partial_path, str):
            raise TypeError("partial_path must be a string")
        normalized = partial_path.replace("\\", "/")
        components = normalized.split("/")
        if (
            not normalized
            or normalized.startswith("/")
            or PureWindowsPath(partial_path).drive
            or ":" in normalized
            or "\x00" in normalized
            or any(not part or part in {".", ".."} or "**" in part for part in components)
        ):
            raise ValueError("partial_path must contain safe relative directory components")
        if isinstance(exclude_paths, (str, os.PathLike)):
            raise TypeError("exclude_paths must be an iterable of paths")
        excluded = tuple(_local_path(path) for path in exclude_paths)
        candidates = list(_walk_paths(base, recursive, directories=True, exclude_paths=excluded))
        if not any(base == path or path in base.parents for path in excluded):
            candidates.insert(0, base)
        matches = []
        for candidate in candidates:
            parts = (base.name, *candidate.relative_to(base).parts)
            if len(components) == 1:
                found = _matches(parts[-1], components[0], fuzzy=True)
            else:
                found = len(parts) >= len(components) and all(
                    _matches(name, pattern, fuzzy=False)
                    for name, pattern in zip(parts[-len(components):], components)
                )
            if found:
                matches.append(candidate)
        matches.sort(key=lambda path: path.relative_to(base).as_posix())
        if not matches:
            raise FileNotFoundError(f"no directory matches {partial_path!r} under {base}")
        if len(matches) > 1:
            raise ValueError(f"ambiguous directory {partial_path!r}: " + ", ".join(str(path) for path in matches))
        _record_local("LOCAL FIND DIRECTORY", path=str(base), pattern=partial_path, destination=str(matches[0]))
        return matches[0]


def _record_local(operation: str, **details: object) -> None:
    from .script_session import current_session

    session = current_session()
    if session:
        session.recorder.record_result(operation, {
            "operation_id": session.recorder.next_operation_id(),
            "status": "success", "success": True, **details,
        })


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
