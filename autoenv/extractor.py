from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Protocol, Sequence

from .recorder import RunRecorder
from .results import ExtractResult
from .selectors import (
    LocalFileSelector,
    ResolvedLocalFile,
    SelectorResolutionError,
    describe_selector,
    resolve_local_file,
    validate_archive_target,
)


class CommandRunner(Protocol):
    """subprocess.run-compatible callable used to make .run extraction testable."""

    def __call__(self, command: Sequence[str], **kwargs: object) -> object: ...


class _ExtractRuntimeError(RuntimeError):
    def __init__(self, status: str, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


class Extractor:
    """Explicitly extract one file or directory from a local package archive."""

    def __init__(
        self,
        package_dir: Path | str,
        run_id: str,
        recorder: RunRecorder,
        image_pattern_for: Callable[[str], str],
        *,
        local_file_resolver: (
            Callable[[LocalFileSelector], ResolvedLocalFile] | None
        ) = None,
        command_runner: CommandRunner | None = None,
        bash_executable: str | None = None,
        run_timeout: float = 300.0,
    ) -> None:
        self.package_dir = Path(package_dir).resolve()
        self.run_id = run_id
        self.recorder = recorder
        self.image_pattern_for = image_pattern_for
        if local_file_resolver is not None and not callable(local_file_resolver):
            raise TypeError("local_file_resolver must be callable")
        self.local_file_resolver = local_file_resolver
        self.command_runner = command_runner or subprocess.run
        self.bash_executable = bash_executable
        if isinstance(run_timeout, bool) or not isinstance(run_timeout, (int, float)):
            raise TypeError("run_timeout must be a number")
        if float(run_timeout) <= 0:
            raise ValueError("run_timeout must be greater than zero")
        self.run_timeout = float(run_timeout)

    def extract(
        self,
        source: LocalFileSelector,
        target_file: str | None = None,
        target_dir: str | None = None,
    ) -> ExtractResult:
        """Extract exactly one requested target and return a recorded result."""

        if (target_file is None) == (target_dir is None):
            raise ValueError("exactly one of target_file and target_dir must be provided")
        target_type = "file" if target_file is not None else "directory"
        raw_target = target_file if target_file is not None else target_dir
        assert raw_target is not None
        target = validate_archive_target(raw_target, f"target_{'file' if target_file is not None else 'dir'}")
        target_name = PurePosixPath(target).name
        if not target_name or target in (".", "./"):
            raise ValueError("archive target must name a file or directory")
        selector_type, selector_value = describe_selector(source)

        operation_id = self.recorder.next_operation_id()
        started_at = _now()
        source_file: Path | None = None
        destination = self.package_dir / target_name
        destination_existed = False
        md5_before: str | None = None
        md5_after: str | None = None
        tree_md5_before: str | None = None
        tree_md5_after: str | None = None
        file_count_before: int | None = None
        file_count_after: int | None = None
        content_changed: bool | None = None
        temporary_root: Path | None = None

        def result(
            *,
            success: bool,
            status: str,
            error: BaseException | None = None,
            error_type: str | None = None,
        ) -> ExtractResult:
            finished_at = _now()
            value = ExtractResult(
                run_id=self.run_id,
                operation_id=operation_id,
                success=success,
                status=status,
                selector_type=selector_type,
                selector=selector_value,
                package_dir=str(self.package_dir),
                target_type=target_type,
                target=target,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=max(
                    0, int((finished_at - started_at).total_seconds() * 1000)
                ),
                source_file=str(source_file) if source_file is not None else None,
                destination=str(destination),
                destination_existed=destination_existed,
                md5_before=md5_before,
                md5_after=md5_after,
                tree_md5_before=tree_md5_before,
                tree_md5_after=tree_md5_after,
                file_count_before=file_count_before,
                file_count_after=file_count_after,
                content_changed=content_changed,
                error_type=error_type or (type(error).__name__ if error else None),
                error_message=str(error) if error else None,
            )
            self.recorder.record_result("EXTRACT", value)
            return value

        try:
            try:
                resolved = (
                    self.local_file_resolver(source)
                    if self.local_file_resolver is not None
                    else resolve_local_file(
                        source, self.package_dir, self.image_pattern_for
                    )
                )
            except SelectorResolutionError as exc:
                return result(
                    success=False,
                    status=exc.code.lower(),
                    error=exc,
                    error_type=exc.code,
                )
            source_file = resolved.path

            archive_type = _archive_type(source_file)
            if archive_type is None:
                return result(
                    success=False,
                    status="unsupported_archive_type",
                    error=ValueError(
                        f"supported archive types are .run, .tar.gz, and .tgz: {source_file.name}"
                    ),
                    error_type="UNSUPPORTED_ARCHIVE_TYPE",
                )

            try:
                temporary_root = Path(
                    tempfile.mkdtemp(prefix=".autoenv_extract_", dir=self.package_dir)
                ).resolve()
                if archive_type == "run":
                    self._extract_run(source_file, temporary_root)
                else:
                    _safe_extract_tar(source_file, temporary_root)
                _validate_extracted_tree(temporary_root)
                selected = _find_target(temporary_root, target, target_type)

                if destination.resolve() == source_file.resolve():
                    raise _ExtractRuntimeError(
                        "destination_conflicts_with_source",
                        "DESTINATION_CONFLICTS_WITH_SOURCE",
                        "the extracted destination would overwrite the source package",
                    )

                destination_existed = destination.exists() or destination.is_symlink()
                if target_type == "file":
                    if destination_existed and not destination.is_file():
                        raise _ExtractRuntimeError(
                            "destination_replace_failed",
                            "DESTINATION_TYPE_MISMATCH",
                            f"file destination is not a regular file: {destination}",
                        )
                    if destination_existed:
                        md5_before = _file_md5(destination)
                    pending = destination.with_name(destination.name + ".extract.part")
                    try:
                        pending.unlink(missing_ok=True)
                        shutil.copy2(selected, pending)
                        md5_after = _file_md5(pending)
                        os.replace(pending, destination)
                    finally:
                        pending.unlink(missing_ok=True)
                    content_changed = md5_before != md5_after
                else:
                    if destination_existed and destination.is_dir() and not destination.is_symlink():
                        tree_md5_before, file_count_before = tree_md5(destination)
                    tree_md5_after, file_count_after = _replace_directory_safely(
                        selected, destination
                    )
                    content_changed = tree_md5_before != tree_md5_after
            except _ExtractRuntimeError as exc:
                return result(
                    success=False,
                    status=exc.status,
                    error=exc,
                    error_type=exc.error_type,
                )
            except Exception as exc:
                return result(
                    success=False,
                    status="extraction_failed",
                    error=exc,
                    error_type="EXTRACTION_FAILED",
                )

            return result(success=True, status="success")
        finally:
            if temporary_root is not None:
                shutil.rmtree(temporary_root, ignore_errors=True)

    def _extract_run(self, source: Path, destination: Path) -> None:
        bash = self.bash_executable or shutil.which("bash") or "bash"
        command = [bash, str(source), "--noexec", f"--extract={destination}"]
        completed = self.command_runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=self.run_timeout,
        )
        return_code = getattr(completed, "returncode", 0)
        if return_code:
            stderr = getattr(completed, "stderr", "")
            raise subprocess.CalledProcessError(
                int(return_code), command, stderr=stderr
            )


def _archive_type(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".run"):
        return "run"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar"
    return None


def _safe_extract_tar(source: Path, destination: Path) -> None:
    """Validate every member before extraction to prevent archive traversal."""

    with tarfile.open(source, mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            normalized = member.name.replace("\\", "/")
            posix_path = PurePosixPath(normalized)
            windows_path = PureWindowsPath(member.name)
            if (
                not normalized
                or posix_path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or ".." in posix_path.parts
            ):
                raise _ExtractRuntimeError(
                    "unsafe_archive",
                    "ARCHIVE_PATH_TRAVERSAL",
                    f"unsafe archive member path: {member.name!r}",
                )
            member_path = (destination / Path(*posix_path.parts)).resolve()
            _ensure_inside(destination, member_path)
            if member.issym() or member.islnk():
                raise _ExtractRuntimeError(
                    "unsafe_archive",
                    "ARCHIVE_LINK_NOT_ALLOWED",
                    f"archive links are not allowed: {member.name!r}",
                )
            if not (member.isfile() or member.isdir()):
                raise _ExtractRuntimeError(
                    "unsafe_archive",
                    "ARCHIVE_SPECIAL_FILE_NOT_ALLOWED",
                    f"archive special files are not allowed: {member.name!r}",
                )
        archive.extractall(destination, members=members)


def _validate_extracted_tree(root: Path) -> None:
    """Reject links and special files produced by a self-extracting .run file."""

    for current_root, directories, files in os.walk(root, followlinks=False):
        current = Path(current_root)
        _ensure_inside(root, current.resolve())
        for name in [*directories, *files]:
            item = current / name
            if item.is_symlink():
                raise _ExtractRuntimeError(
                    "unsafe_archive",
                    "EXTRACTED_LINK_NOT_ALLOWED",
                    f"extracted links are not allowed: {item}",
                )
            _ensure_inside(root, item.resolve())
            if not item.is_dir() and not item.is_file():
                raise _ExtractRuntimeError(
                    "unsafe_archive",
                    "EXTRACTED_SPECIAL_FILE_NOT_ALLOWED",
                    f"extracted special files are not allowed: {item}",
                )


def _find_target(root: Path, target: str, target_type: str) -> Path:
    relative = PurePosixPath(target)
    direct = root.joinpath(*relative.parts)
    expected = direct.is_file() if target_type == "file" else direct.is_dir()
    if expected:
        return direct

    target_name = relative.name
    matches = sorted(
        (
            item
            for item in root.rglob("*")
            if item.name == target_name
            and (item.is_file() if target_type == "file" else item.is_dir())
        ),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    if not matches:
        suffix = "FILE" if target_type == "file" else "DIR"
        status_target = "file" if target_type == "file" else "dir"
        raise _ExtractRuntimeError(
            f"target_{status_target}_not_found",
            f"TARGET_{suffix}_NOT_FOUND",
            f"archive target was not found: {target!r}",
        )
    if len(matches) > 1:
        suffix = "FILES" if target_type == "file" else "DIRS"
        status_target = "files" if target_type == "file" else "dirs"
        locations = ", ".join(item.relative_to(root).as_posix() for item in matches)
        raise _ExtractRuntimeError(
            f"multiple_target_{status_target}_found",
            f"MULTIPLE_TARGET_{suffix}_FOUND",
            f"archive target {target_name!r} is ambiguous: {locations}",
        )
    return matches[0]


def tree_md5(directory: Path | str) -> tuple[str, int]:
    """Return a deterministic digest and regular-file count for a directory tree."""

    root = Path(directory).resolve()
    digest = hashlib.md5()
    files = sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for item in files:
        if item.is_symlink():
            raise ValueError(f"tree_md5 does not accept symbolic links: {item}")
        relative = item.relative_to(root).as_posix()
        size = item.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_file_md5(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), len(files)


def _remove_destination(destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.is_dir():
        shutil.rmtree(destination)


def _replace_directory_safely(source: Path, destination: Path) -> tuple[str, int]:
    """Stage and verify a directory before replacing an existing destination."""

    staging_parent = Path(
        tempfile.mkdtemp(prefix=".autoenv_dir_stage_", dir=destination.parent)
    )
    backup_parent = Path(
        tempfile.mkdtemp(prefix=".autoenv_dir_backup_", dir=destination.parent)
    )
    staged = staging_parent / destination.name
    backup = backup_parent / destination.name
    moved_old = False
    committed = False
    try:
        shutil.copytree(source, staged)
        digest, count = tree_md5(staged)
        if destination.exists() or destination.is_symlink():
            os.replace(destination, backup)
            moved_old = True
        try:
            os.replace(staged, destination)
            committed = True
        except Exception:
            if moved_old and (backup.exists() or backup.is_symlink()):
                if destination.exists() or destination.is_symlink():
                    _remove_destination(destination)
                os.replace(backup, destination)
                moved_old = False
            raise
        if moved_old and (backup.exists() or backup.is_symlink()):
            _remove_destination(backup)
            moved_old = False
        return digest, count
    finally:
        if not committed and moved_old and (backup.exists() or backup.is_symlink()):
            if destination.exists() or destination.is_symlink():
                _remove_destination(destination)
            os.replace(backup, destination)
        shutil.rmtree(staging_parent, ignore_errors=True)
        shutil.rmtree(backup_parent, ignore_errors=True)


def _ensure_inside(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise _ExtractRuntimeError(
            "unsafe_archive",
            "ARCHIVE_PATH_TRAVERSAL",
            f"extracted path escapes the package directory: {candidate}",
        ) from exc


def _file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> datetime:
    # Kept in one helper so tests can monkeypatch time without changing interfaces.
    return datetime.now().astimezone()
