from __future__ import annotations

import fnmatch
import gzip
import json
import os
import re
import shutil
import sqlite3
import stat
import tarfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Sequence

from .recorder import RunRecorder
from .results import LogOperationResult, RemoteBatchDownloadResult, result_to_dict


MAX_EXPANDED_FILES = 10_000
MAX_EXPANDED_BYTES = 5 * 1024 * 1024 * 1024
_TIME_FIELDS = ("year", "month", "day", "hour", "minute", "second")


@dataclass(frozen=True)
class LogTimestamp:
    year: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    second: int | None = None

    @property
    def clock_seconds(self) -> int | None:
        if self.hour is None or self.minute is None:
            return None
        return self.hour * 3600 + self.minute * 60 + (self.second or 0)

    @property
    def date_key(self) -> str | None:
        if None in (self.year, self.month, self.day):
            return None
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"

    def display(self) -> str:
        if self.clock_seconds is None:
            return "-"
        clock = f"{self.hour:02d}:{self.minute:02d}"
        if self.second is not None:
            clock += f":{self.second:02d}"
        return f"{self.date_key} {clock}" if self.date_key else clock


class TimestampPattern:
    def __init__(self, regex: str) -> None:
        if not isinstance(regex, str) or not regex.strip():
            raise ValueError("timestamp regex must not be empty")
        self.regex = regex
        self.compiled = re.compile(regex)
        names = set(self.compiled.groupindex)
        unknown = names - set(_TIME_FIELDS)
        if unknown:
            raise ValueError(f"unsupported timestamp groups: {sorted(unknown)}")
        if not {"hour", "minute"}.issubset(names):
            raise ValueError("timestamp regex requires named hour and minute groups")

    def parse(self, line: str) -> LogTimestamp | None:
        match = self.compiled.search(line)
        if match is None:
            return None
        values: dict[str, int | None] = {}
        for name in _TIME_FIELDS:
            raw = match.groupdict().get(name)
            values[name] = int(raw) if raw not in (None, "") else None
        value = LogTimestamp(**values)
        self._validate(value)
        return value

    @staticmethod
    def _validate(value: LogTimestamp) -> None:
        if value.hour is not None and not 0 <= value.hour <= 23:
            raise ValueError("timestamp hour is outside 0..23")
        if value.minute is not None and not 0 <= value.minute <= 59:
            raise ValueError("timestamp minute is outside 0..59")
        if value.second is not None and not 0 <= value.second <= 59:
            raise ValueError("timestamp second is outside 0..59")
        if value.year is not None and value.month is not None and value.day is not None:
            datetime(value.year, value.month, value.day, value.hour or 0, value.minute or 0, value.second or 0)


@dataclass(frozen=True)
class _Record:
    target: str
    file_order: int
    line_number: int
    rule_order: int
    text: str
    timestamp: LogTimestamp | None
    timestamp_source: str
    source_file: str
    incomplete_block: bool = False


class LogCollection:
    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        recorder: RunRecorder,
        alias: str = "",
    ) -> None:
        if not isinstance(alias, str):
            raise TypeError("log collection alias must be a string")
        normalized_alias = alias.strip()
        if len(normalized_alias) > 100:
            raise ValueError("log collection alias must not exceed 100 characters")
        self.run_id = run_id
        self.recorder = recorder
        self.collected_at = datetime.now().astimezone().isoformat()
        self.alias = normalized_alias or datetime.now().astimezone().strftime("日志 %Y-%m-%d %H:%M:%S")
        self.batch_dir = (Path(run_dir) / "log_collection").resolve()
        self.raw_dir = self.batch_dir / "raw"
        self.expanded_dir = self.batch_dir / "expanded"
        self.targets_dir = self.batch_dir / "targets"
        self.index_path = self.batch_dir / "index.sqlite3"
        self.manifest_path = self.batch_dir / "manifest.json"
        self.raw_dir.mkdir(parents=True, exist_ok=False)
        self.expanded_dir.mkdir()
        self.targets_dir.mkdir()
        self._records: list[_Record] = []
        self._source_mtimes: dict[Path, float] = {}
        self._source_encodings: dict[str, str] = {}
        self._download: RemoteBatchDownloadResult | None = None
        self._extracted = False
        self._failed = False
        self._finalized = False
        self._write_manifest("building")

    def download(
        self,
        host: object,
        *,
        remote_dir: str,
        glob: str = "cpdt_*",
        protocol: str = "scp",
    ) -> RemoteBatchDownloadResult:
        if protocol != "scp":
            raise ValueError("log collection currently supports protocol='scp' only")
        method = getattr(host, "scp_download_many", None)
        if method is None:
            raise TypeError("host must be a registered SSHHost")
        result = method(remote_dir, glob=glob, destination=self.raw_dir)
        if not isinstance(result, RemoteBatchDownloadResult):
            raise TypeError("scp_download_many returned an invalid result")
        self._download = result
        for item in result.files:
            self._source_mtimes[Path(item.local_file).resolve()] = item.remote_mtime
        if not result.success:
            self._failed = True
            self._write_manifest(
                "failed", error=result.error_message, download=self._download_manifest()
            )
        return result

    def extract_all(self) -> LogOperationResult:
        started = datetime.now().astimezone()
        operation_id = self.recorder.next_operation_id()
        try:
            if self._download is None or not self._download.success:
                raise RuntimeError("a successful download is required before extraction")
            counter = [0, 0]
            for item in self._download.files:
                source = Path(item.local_file).resolve()
                self._expand_top_level(source, item.remote_mtime, counter)
            self._extracted = True
            result = self._operation_result(operation_id, started, True, "success", counter[0])
        except Exception as exc:
            self._failed = True
            self._write_manifest(
                "failed", error=str(exc), download=self._download_manifest()
            )
            result = self._operation_result(
                operation_id, started, False, "extraction_failed", 0, exc
            )
        self.recorder.record_result("LOG EXTRACT", result)
        return result

    def group(
        self,
        *,
        glob: str = "cpdt*.log",
        timestamp: TimestampPattern,
        encoding: str | Sequence[str] = ("utf-8", "utf-8-sig", "gb18030", "latin-1"),
    ) -> "LogGroup":
        if not self._extracted:
            raise RuntimeError("extract_all() must succeed before group()")
        encodings = (encoding,) if isinstance(encoding, str) else tuple(encoding)
        if not encodings or any(not isinstance(item, str) or not item for item in encodings):
            raise ValueError("encoding must contain at least one valid codec name")
        files = [
            path
            for path in self.expanded_dir.rglob("*")
            if path.is_file() and fnmatch.fnmatchcase(path.name, glob)
        ]
        files.sort(
            key=lambda path: (
                self._source_mtimes.get(path.resolve(), path.stat().st_mtime),
                str(path.relative_to(self.expanded_dir)).casefold(),
                str(path.relative_to(self.expanded_dir)),
            )
        )
        if not files:
            self._failed = True
            self._write_manifest(
                "failed",
                error=f"no expanded file matched glob {glob!r}",
                download=self._download_manifest(),
            )
            raise FileNotFoundError(f"no expanded file matched glob {glob!r}")
        return LogGroup(self, files, timestamp, encodings)

    def finalize(self) -> LogOperationResult:
        started = datetime.now().astimezone()
        operation_id = self.recorder.next_operation_id()
        try:
            if self._finalized:
                raise RuntimeError("log collection is already finalized")
            if self._failed:
                raise RuntimeError("log collection contains a failed operation")
            ordered = sorted(
                self._records,
                key=lambda item: (
                    item.target.casefold(),
                    item.file_order,
                    item.line_number,
                    item.rule_order,
                ),
            )
            targets = sorted({item.target for item in ordered}, key=str.casefold)
            for target in targets:
                rows = [item for item in ordered if item.target == target]
                output = self.targets_dir / target
                with output.open("w", encoding="utf-8", newline="\n") as handle:
                    for row in rows:
                        stamp = row.timestamp.display() if row.timestamp else "-"
                        handle.write(f"[{stamp}] {row.text}\n")
            self._write_index(ordered)
            self._finalized = True
            self._write_manifest(
                "ready",
                targets=targets,
                record_count=len(ordered),
                source_encodings=dict(sorted(self._source_encodings.items())),
                download=self._download_manifest(),
            )
            result = self._operation_result(operation_id, started, True, "success", len(ordered))
        except Exception as exc:
            self._failed = True
            self._write_manifest(
                "failed", error=str(exc), download=self._download_manifest()
            )
            result = self._operation_result(operation_id, started, False, "finalize_failed", 0, exc)
        self.recorder.record_result("LOG FINALIZE", result)
        return result

    def _expand_top_level(self, source: Path, remote_mtime: float, counter: list[int]) -> None:
        target = self.expanded_dir / source.name
        if target.exists():
            raise FileExistsError(f"expanded path already exists: {target.name}")
        shutil.copy2(source, target)
        self._source_mtimes[target.resolve()] = remote_mtime
        self._count_file(target, counter)
        self._expand_archive_recursive(target, remote_mtime, counter)

    def _expand_archive_recursive(self, archive: Path, inherited_mtime: float, counter: list[int]) -> None:
        archive_type = _archive_type(archive)
        if archive_type is None:
            return
        destination = archive.with_name(archive.name + ".expanded")
        if destination.exists():
            raise FileExistsError(f"archive destination already exists: {destination}")
        destination.mkdir()
        try:
            remaining_files = MAX_EXPANDED_FILES - counter[0]
            remaining_bytes = MAX_EXPANDED_BYTES - counter[1]
            if archive_type == "zip":
                _extract_zip(
                    archive,
                    destination,
                    max_files=remaining_files,
                    max_bytes=remaining_bytes,
                )
            elif archive_type == "tar":
                _extract_tar(
                    archive,
                    destination,
                    max_files=remaining_files,
                    max_bytes=remaining_bytes,
                )
            else:
                output = destination / archive.name[:-3]
                with gzip.open(archive, "rb") as source, output.open("wb") as target:
                    _copy_limited(source, target, remaining_bytes)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                self._count_file(path, counter)
                self._source_mtimes[path.resolve()] = inherited_mtime
        for path in sorted(destination.rglob("*")):
            if path.is_file() and _archive_type(path) is not None:
                self._expand_archive_recursive(path, inherited_mtime, counter)

    @staticmethod
    def _count_file(path: Path, counter: list[int]) -> None:
        counter[0] += 1
        counter[1] += path.stat().st_size
        if counter[0] > MAX_EXPANDED_FILES:
            raise ValueError(f"expanded file count exceeds {MAX_EXPANDED_FILES}")
        if counter[1] > MAX_EXPANDED_BYTES:
            raise ValueError(f"expanded bytes exceed {MAX_EXPANDED_BYTES}")

    def _write_index(self, records: Iterable[_Record]) -> None:
        self.index_path.unlink(missing_ok=True)
        with sqlite3.connect(self.index_path) as connection:
            connection.execute(
                """
                CREATE TABLE records (
                    id INTEGER PRIMARY KEY,
                    target TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    source_file TEXT NOT NULL,
                    source_line INTEGER NOT NULL,
                    year INTEGER, month INTEGER, day INTEGER,
                    hour INTEGER, minute INTEGER, second INTEGER,
                    clock_seconds INTEGER,
                    date_key TEXT,
                    timestamp_source TEXT NOT NULL,
                    incomplete_block INTEGER NOT NULL
                )
                """
            )
            sequence_by_target: dict[str, int] = {}
            for row in records:
                sequence = sequence_by_target.get(row.target, 0) + 1
                sequence_by_target[row.target] = sequence
                stamp = row.timestamp or LogTimestamp()
                connection.execute(
                    """
                    INSERT INTO records (
                        target, sequence, text, source_file, source_line,
                        year, month, day, hour, minute, second,
                        clock_seconds, date_key, timestamp_source, incomplete_block
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.target, sequence, row.text, row.source_file, row.line_number,
                        stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second,
                        stamp.clock_seconds, stamp.date_key, row.timestamp_source,
                        int(row.incomplete_block),
                    ),
                )
            connection.execute("CREATE INDEX records_target_sequence ON records(target, sequence)")
            connection.execute("CREATE INDEX records_target_clock ON records(target, clock_seconds)")

    def _write_manifest(self, status: str, **extra: object) -> None:
        value = {
            "schema_version": 1,
            "batch_id": self.run_id,
            "alias": self.alias,
            "collected_at": self.collected_at,
            "status": status,
            "updated_at": datetime.now().astimezone().isoformat(),
            **extra,
        }
        pending = self.manifest_path.with_suffix(".json.tmp")
        pending.write_text(json.dumps(result_to_dict(value), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(pending, self.manifest_path)

    def _download_manifest(self) -> dict[str, object] | None:
        if self._download is None:
            return None
        return {
            "protocol": self._download.protocol,
            "target_name": self._download.target_name,
            "remote_dir": self._download.remote_dir,
            "glob": self._download.glob,
            "files": [
                {
                    "name": item.name,
                    "remote_file": item.remote_file,
                    "remote_size": item.remote_size,
                    "remote_mtime": item.remote_mtime,
                }
                for item in self._download.files
            ],
        }

    def _operation_result(
        self,
        operation_id: str,
        started: datetime,
        success: bool,
        status: str,
        output_count: int,
        error: Exception | None = None,
    ) -> LogOperationResult:
        finished = datetime.now().astimezone()
        return LogOperationResult(
            run_id=self.run_id,
            operation_id=operation_id,
            success=success,
            status=status,
            started_at=started,
            finished_at=finished,
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            batch_dir=str(self.batch_dir),
            output_count=output_count,
            error_type=type(error).__name__ if error else None,
            error_message=str(error) if error else None,
        )


class LogGroup:
    def __init__(
        self,
        collection: LogCollection,
        files: list[Path],
        timestamp: TimestampPattern,
        encodings: tuple[str, ...],
    ) -> None:
        self.collection = collection
        self.files = files
        self.timestamp = timestamp
        self.encodings = encodings
        self._rule_order = 0

    def match_line(self, regex: str, target_file: str) -> LogOperationResult:
        pattern = re.compile(regex)
        target = _target_file(target_file)
        started = datetime.now().astimezone()
        operation_id = self.collection.recorder.next_operation_id()
        self._rule_order += 1
        count = 0
        try:
            for file_order, path in enumerate(self.files):
                previous: LogTimestamp | None = None
                for line_number, line in enumerate(self._read_lines(path), start=1):
                    parsed = self.timestamp.parse(line)
                    if parsed is not None:
                        previous = parsed
                    if pattern.search(line):
                        self.collection._records.append(
                            _Record(
                                target, file_order, line_number, self._rule_order, line,
                                parsed or previous,
                                "parsed" if parsed else "inherited" if previous else "unknown",
                                str(path.relative_to(self.collection.expanded_dir)),
                            )
                        )
                        count += 1
            result = self.collection._operation_result(operation_id, started, True, "success", count)
        except Exception as exc:
            self.collection._failed = True
            self.collection._write_manifest(
                "failed", error=str(exc), download=self.collection._download_manifest()
            )
            result = self.collection._operation_result(operation_id, started, False, "match_failed", count, exc)
        self.collection.recorder.record_result("LOG MATCH LINE", result)
        return result

    def match_block(self, begin_regex: str, end_regex: str, target_file: str) -> LogOperationResult:
        begin = re.compile(begin_regex)
        end = re.compile(end_regex)
        target = _target_file(target_file)
        started = datetime.now().astimezone()
        operation_id = self.collection.recorder.next_operation_id()
        self._rule_order += 1
        count = 0
        try:
            for file_order, path in enumerate(self.files):
                previous: LogTimestamp | None = None
                segment_previous: LogTimestamp | None = None
                explicit = False
                anchor: LogTimestamp | None = None
                buffer: list[tuple[int, str, LogTimestamp | None]] = []

                def flush(*, incomplete: bool) -> None:
                    nonlocal count, buffer
                    if not buffer:
                        return
                    selected = anchor if explicit else next((stamp for _, _, stamp in buffer if stamp), segment_previous)
                    if explicit and selected:
                        source = "block_begin"
                    elif any(stamp for _, _, stamp in buffer):
                        source = "block_first"
                    elif selected:
                        source = "block_inherited"
                    else:
                        source = "unknown"
                    for line_number, text, _stamp in buffer:
                        self.collection._records.append(
                            _Record(
                                target, file_order, line_number, self._rule_order, text,
                                selected, source,
                                str(path.relative_to(self.collection.expanded_dir)),
                                incomplete,
                            )
                        )
                        count += 1
                    buffer = []

                for line_number, line in enumerate(self._read_lines(path), start=1):
                    before = previous
                    parsed = self.timestamp.parse(line)
                    if parsed is not None:
                        previous = parsed
                    if begin.search(line):
                        if not explicit:
                            buffer = []
                            explicit = True
                            anchor = parsed or before
                        continue
                    if end.search(line):
                        flush(incomplete=False)
                        buffer = []
                        explicit = False
                        anchor = None
                        segment_previous = previous
                        continue
                    buffer.append((line_number, line, parsed))
                if explicit:
                    flush(incomplete=True)
            result = self.collection._operation_result(operation_id, started, True, "success", count)
        except Exception as exc:
            self.collection._failed = True
            self.collection._write_manifest(
                "failed", error=str(exc), download=self.collection._download_manifest()
            )
            result = self.collection._operation_result(operation_id, started, False, "match_failed", count, exc)
        self.collection.recorder.record_result("LOG MATCH BLOCK", result)
        return result

    def _read_lines(self, path: Path) -> list[str]:
        payload = path.read_bytes()
        last_error: UnicodeDecodeError | None = None
        for encoding in self.encodings:
            try:
                text = payload.decode(encoding)
                relative = str(path.relative_to(self.collection.expanded_dir))
                self.collection._source_encodings[relative] = encoding
                self.collection.recorder.log(f"LOG ENCODING path={path} encoding={encoding}")
                return text.splitlines()
            except UnicodeDecodeError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error


def _target_file(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("target_file must be a string")
    name = value.strip()
    if not name.endswith(".log") or Path(name).name != name or name in {".log", ".."}:
        raise ValueError("target_file must be a safe .log filename")
    return name


def _archive_type(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".gz"):
        return "gz"
    return None


def _validated_member(name: str, destination: Path) -> Path:
    normalized = name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(name)
    if not normalized or posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError(f"unsafe archive member: {name!r}")
    target = destination.joinpath(*posix.parts).resolve()
    try:
        target.relative_to(destination.resolve())
    except ValueError as exc:
        raise ValueError(f"unsafe archive member: {name!r}") from exc
    return target


def _extract_zip(
    source: Path,
    destination: Path,
    *,
    max_files: int = MAX_EXPANDED_FILES,
    max_bytes: int = MAX_EXPANDED_BYTES,
) -> None:
    with zipfile.ZipFile(source) as archive:
        checked: list[tuple[zipfile.ZipInfo, Path, bool]] = []
        seen: dict[str, bool] = {}
        file_count = 0
        total_bytes = 0
        for member in archive.infolist():
            target = _validated_member(member.filename, destination)
            unix_mode = (member.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(unix_mode)
            if file_type == stat.S_IFLNK:
                raise ValueError(f"archive links are not allowed: {member.filename!r}")
            is_dir = member.is_dir() or file_type == stat.S_IFDIR
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError(f"archive special files are not allowed: {member.filename!r}")
            key = os.path.normcase(str(target))
            if key in seen or target.exists():
                raise FileExistsError(f"archive member collision: {member.filename!r}")
            _reject_parent_file_collision(target, destination, seen, is_dir=is_dir)
            seen[key] = is_dir
            checked.append((member, target, is_dir))
            if not is_dir:
                file_count += 1
                total_bytes += member.file_size
                _check_limits(file_count, total_bytes, max_files, max_bytes)
        for member, target, is_dir in checked:
            if is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as input_file, target.open("wb") as output_file:
                _copy_limited(input_file, output_file, member.file_size)


def _extract_tar(
    source: Path,
    destination: Path,
    *,
    max_files: int = MAX_EXPANDED_FILES,
    max_bytes: int = MAX_EXPANDED_BYTES,
) -> None:
    with tarfile.open(source, mode="r:gz") as archive:
        members = archive.getmembers()
        checked: list[tuple[tarfile.TarInfo, Path]] = []
        seen: dict[str, bool] = {}
        file_count = 0
        total_bytes = 0
        for member in members:
            target = _validated_member(member.name, destination)
            if member.issym() or member.islnk():
                raise ValueError(f"archive links are not allowed: {member.name!r}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"archive special files are not allowed: {member.name!r}")
            key = os.path.normcase(str(target))
            if key in seen or target.exists():
                raise FileExistsError(f"archive member collision: {member.name!r}")
            _reject_parent_file_collision(
                target, destination, seen, is_dir=member.isdir()
            )
            seen[key] = member.isdir()
            checked.append((member, target))
            if member.isfile():
                file_count += 1
                total_bytes += member.size
                _check_limits(file_count, total_bytes, max_files, max_bytes)
        for member, target in checked:
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            input_file = archive.extractfile(member)
            if input_file is None:
                raise ValueError(f"cannot read archive member: {member.name!r}")
            with input_file, target.open("wb") as output_file:
                _copy_limited(input_file, output_file, member.size)


def _reject_parent_file_collision(
    target: Path,
    destination: Path,
    seen: dict[str, bool],
    *,
    is_dir: bool,
) -> None:
    target_key = os.path.normcase(str(target))
    prefix = target_key + os.sep
    if not is_dir and any(key.startswith(prefix) for key in seen):
        raise FileExistsError(f"archive member collides with an existing child: {target.name!r}")
    current = target.parent
    while current != destination:
        key = os.path.normcase(str(current))
        if key in seen and not seen[key]:
            raise FileExistsError(f"archive member parent is a file: {target.name!r}")
        current = current.parent


def _check_limits(file_count: int, byte_count: int, max_files: int, max_bytes: int) -> None:
    if file_count > max_files:
        raise ValueError(f"expanded file count exceeds {MAX_EXPANDED_FILES}")
    if byte_count > max_bytes:
        raise ValueError(f"expanded bytes exceed {MAX_EXPANDED_BYTES}")


def _copy_limited(source: object, target: object, limit: int) -> None:
    written = 0
    while True:
        chunk = source.read(min(1024 * 1024, limit - written + 1))  # type: ignore[attr-defined]
        if not chunk:
            return
        written += len(chunk)
        if written > limit:
            raise ValueError(f"expanded bytes exceed {MAX_EXPANDED_BYTES}")
        target.write(chunk)  # type: ignore[attr-defined]
