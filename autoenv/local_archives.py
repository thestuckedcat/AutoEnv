"""Content-based, bounded archive extraction for ordinary local file handlers.

This core shares the validation model of ``logs`` (validate member paths and
types, reject collisions, bound expansion, then publish). It intentionally does
not change the older remote LogCollection or Extractor replacement contracts.
"""
from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath
from typing import BinaryIO, Callable

MAX_EXPANDED_FILES = 10_000
MAX_EXPANDED_BYTES = 5 * 1024 * 1024 * 1024
_CHUNK = 1024 * 1024


def _open_payload(source: Path) -> BinaryIO:
    with source.open("rb") as stream:
        signature = stream.read(6)
    if signature[:2] == b"\x1f\x8b":
        return gzip.open(source, "rb")
    if signature[:3] == b"BZh":
        return bz2.open(source, "rb")
    if signature == b"\xfd7zXZ\x00":
        return lzma.open(source, "rb")
    return source.open("rb")


def detect_archive(path: str | os.PathLike[str]) -> str | None:
    """Identify ZIP, any supported TAR compression, or standalone GZIP by content.

    A known ZIP/GZIP/BZ2/XZ signature is returned even when its payload is damaged;
    extraction then reports the corruption instead of silently treating it as a
    plain log. Other ordinary files return ``None``.
    """
    from .file_handles import _assert_no_links, _local_path

    source = _local_path(path)
    _assert_no_links(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    with source.open("rb") as stream:
        signature = stream.read(6)
    if signature[:4] in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"} or zipfile.is_zipfile(source):
        return "zip"
    try:
        # Probe one checksum-validated TAR header only. Opening TarFile here
        # would eagerly consume a potentially enormous leading PAX header.
        with _open_payload(source) as stream:
            header = stream.read(tarfile.BLOCKSIZE)
        if len(header) == tarfile.BLOCKSIZE and not header.strip(b"\x00"):
            return "tar"
        tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
        return "tar"
    except (tarfile.HeaderError, EOFError, OSError, lzma.LZMAError):
        if signature[:2] == b"\x1f\x8b":
            return "gz"
        if signature[:3] == b"BZh" or signature == b"\xfd7zXZ\x00":
            return "tar"
        if source.name.lower().endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            # A broken TAR header has no dependable content signature. The
            # filename is only a final hint, after all content-based probes.
            return "tar"
        return None


def _member_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    windows = PureWindowsPath(normalized)
    raw = normalized.split("/")
    if not name or normalized.startswith("/") or windows.drive or "\x00" in name or ".." in raw:
        raise ValueError(f"unsafe archive member path: {name!r}")
    parts = tuple(part for part in raw if part not in {"", "."})
    for part in parts:
        if (
            any(char in part for char in ':<>"|?*')
            or part.endswith((".", " "))
            or PureWindowsPath(part).is_reserved()
        ):
            raise ValueError(f"unsafe archive member filename: {name!r}")
    return parts


class _Members:
    """Validate explicit members and implicit directories on every platform."""

    def __init__(self, max_files: int, max_bytes: int, check_cancelled: Callable[[], None] | None = None) -> None:
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0
        self.members = 0
        self.check_cancelled = check_cancelled
        # key -> (directory, explicitly listed, exact spelling of components).
        self.paths: dict[str, tuple[bool, bool, tuple[str, ...]]] = {}

    def add(self, name: str, *, directory: bool, size: int = 0) -> tuple[str, ...]:
        if self.check_cancelled:
            self.check_cancelled()
        parts = _member_parts(name)
        self.members += 1
        # Directory-only archives must not bypass the memory budget.
        if self.members > max(1024, self.max_files * 8):
            raise ValueError("expanded archive member count exceeds safety limit")
        if not parts:
            if not directory:
                raise ValueError(f"archive file has an empty destination: {name!r}")
            return parts  # TAR commonly includes an explicit '.' root directory.
        for length in range(1, len(parts)):
            parent = parts[:length]
            key = "/".join(parent).casefold()
            found = self.paths.get(key)
            if found is not None and (not found[0] or found[2] != parent):
                raise FileExistsError(f"archive member parent collision: {name!r}")
            if found is None:
                self.paths[key] = (True, False, parent)
        key = "/".join(parts).casefold()
        found = self.paths.get(key)
        if found is not None and (found[1] or not directory or found[2] != parts):
            raise FileExistsError(f"archive member collision: {name!r}")
        self.paths[key] = (directory, True, parts)
        if not directory:
            if size < 0:
                raise ValueError(f"archive member has a negative size: {name!r}")
            self.files += 1
            self.bytes += size
            self.check()
        return parts

    def check(self) -> None:
        if self.files > self.max_files:
            raise ValueError(f"expanded file count exceeds {self.max_files}")
        if self.bytes > self.max_bytes:
            raise ValueError(f"expanded bytes exceed {self.max_bytes}")


class _TarReader:
    """Bound metadata before tarfile expands PAX/GNU extended headers in memory."""

    def __init__(self, stream: BinaryIO, members: _Members) -> None:
        self.stream = stream
        self.members = members
        self.validating = True
        self.metadata_bytes = 0
        self.metadata_limit = max(1024, members.max_files * 8) * tarfile.BLOCKSIZE + 8 * _CHUNK
        self.stream_limit = members.max_bytes + self.metadata_limit

    def tell(self) -> int:
        return self.stream.tell()

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if self.members.check_cancelled:
            self.members.check_cancelled()
        if whence == os.SEEK_SET and offset > self.stream_limit:
            raise ValueError("expanded archive stream exceeds safety limit")
        return self.stream.seek(offset, whence)

    def read(self, size: int) -> bytes:
        if self.members.check_cancelled:
            self.members.check_cancelled()
        if size < 0 or size > self.metadata_limit:
            raise ValueError("archive metadata exceeds safety limit")
        if self.validating:
            self.metadata_bytes += size
            if self.metadata_bytes > self.metadata_limit:
                raise ValueError("archive metadata exceeds safety limit")
        if self.tell() + size > self.stream_limit:
            raise ValueError("expanded archive stream exceeds safety limit")
        return self.stream.read(size)


def _copy_limited(
    source: BinaryIO, target: BinaryIO, limit: int, *, exact: bool = True,
    check_cancelled: Callable[[], None] | None = None,
) -> int:
    written = 0
    while True:
        if check_cancelled:
            check_cancelled()
        payload = source.read(min(_CHUNK, limit - written + 1))
        if not payload:
            break
        written += len(payload)
        if written > limit:
            raise ValueError(f"expanded bytes exceed {limit}")
        target.write(payload)
    if exact and written != limit:
        raise ValueError(f"truncated archive member: expected {limit} bytes, read {written}")
    return written


def _extract_zip(source: Path, destination: Path, members: _Members) -> None:
    with zipfile.ZipFile(source) as archive:
        checked: list[tuple[zipfile.ZipInfo, tuple[str, ...], bool]] = []
        for member in archive.infolist():
            file_type = stat.S_IFMT((member.external_attr >> 16) & 0xFFFF)
            if file_type == stat.S_IFLNK:
                raise ValueError(f"archive links are not allowed: {member.filename!r}")
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError(f"archive special files are not allowed: {member.filename!r}")
            if member.flag_bits & 1:
                raise ValueError(f"encrypted archives are not supported: {member.filename!r}")
            directory = member.is_dir() or file_type == stat.S_IFDIR
            parts = members.add(member.filename, directory=directory, size=member.file_size)
            checked.append((member, parts, directory))
        for member, parts, directory in checked:
            target = destination.joinpath(*parts)
            if directory:
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as reader, target.open("xb") as writer:
                    _copy_limited(reader, writer, member.file_size, check_cancelled=members.check_cancelled)


def _extract_tar(source: Path, destination: Path, members: _Members) -> None:
    with _open_payload(source) as payload:
        reader = _TarReader(payload, members)
        with tarfile.open(fileobj=reader, mode="r:") as archive:
            _extract_tar_contents(archive, destination, members, reader)


def _extract_tar_contents(
    archive: tarfile.TarFile, destination: Path, members: _Members, archive_reader: _TarReader
) -> None:
    checked: list[tuple[tarfile.TarInfo, tuple[str, ...]]] = []
    for member in archive:
        if member.issym() or member.islnk():
            raise ValueError(f"archive links are not allowed: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"archive special files are not allowed: {member.name!r}")
        parts = members.add(member.name, directory=member.isdir(), size=member.size)
        checked.append((member, parts))
    archive_reader.validating = False
    for member, parts in checked:
        target = destination.joinpath(*parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        reader = archive.extractfile(member)
        if reader is None:
            raise ValueError(f"cannot read archive member: {member.name!r}")
        with reader, target.open("xb") as writer:
            _copy_limited(reader, writer, member.size, check_cancelled=members.check_cancelled)
    # TAR stops at its end marker; consume the compressed wrapper through
    # EOF so gzip CRC/length and bz2/xz checksum failures cannot go unnoticed.
    # Bound excessive trailing/padding data independently of member sizes.
    trailing = 0
    trailing_limit = members.max_bytes + max(1024, members.max_files * 8) * 512 + 8 * _CHUNK
    while payload := archive.fileobj.read(_CHUNK):
        if members.check_cancelled:
            members.check_cancelled()
        trailing += len(payload)
        if trailing > trailing_limit:
            raise ValueError("archive trailing data exceeds safety limit")


def _extract_gzip(source: Path, destination: Path, members: _Members) -> None:
    # Ignore gzip's optional original name, which is untrusted and may be a path.
    name = source.name[:-3] if source.name.lower().endswith(".gz") else "data"
    parts = members.add(name, directory=False)
    target = destination.joinpath(*parts)
    with gzip.open(source, "rb") as reader, target.open("xb") as writer:
        members.bytes = _copy_limited(
            reader, writer, members.max_bytes, exact=False, check_cancelled=members.check_cancelled
        )
    members.check()


def _destination_empty(destination: Path) -> bool:
    from .file_handles import _assert_no_links

    _assert_no_links(destination)
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise FileExistsError(f"archive destination must be a new or empty directory: {destination}")
        return True
    return False


def extract_archive(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    max_files: int = MAX_EXPANDED_FILES,
    max_bytes: int = MAX_EXPANDED_BYTES,
    check_cancelled: Callable[[], None] | None = None,
) -> Path:
    """Safely extract one layer, publishing only after all content is validated."""
    from .file_handles import _assert_no_links, _local_path, _record_local

    if check_cancelled is not None and not callable(check_cancelled):
        raise TypeError("check_cancelled must be callable or None")
    if check_cancelled:
        check_cancelled()
    for value, name in ((max_files, "max_files"), (max_bytes, "max_bytes")):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} must be nonnegative")
    source_path = _local_path(source)
    target = _local_path(destination)
    _assert_no_links(source_path)
    _destination_empty(target)
    archive_type = detect_archive(source_path)
    if archive_type is None:
        raise ValueError(f"unsupported archive format: {source_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_links(target.parent)
    temporary = Path(tempfile.mkdtemp(prefix=".autoenv-unzip-", dir=target.parent))
    members = _Members(max_files, max_bytes, check_cancelled)
    try:
        if archive_type == "zip":
            _extract_zip(source_path, temporary, members)
        elif archive_type == "tar":
            _extract_tar(source_path, temporary, members)
        else:
            _extract_gzip(source_path, temporary, members)
        if check_cancelled:
            check_cancelled()
        # Recheck immediately before publication; never merge or overwrite data.
        existed = _destination_empty(target)
        if existed:
            target.rmdir()
        try:
            os.rename(temporary, target)
        except BaseException:
            if existed and not target.exists():
                target.mkdir()
            raise
        result = target.resolve()
        _record_local(
            "LOCAL ARCHIVE EXTRACT", source=str(source_path), destination=str(result),
            archive_format=archive_type, file_count=members.files, expanded_bytes=members.bytes,
        )
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
