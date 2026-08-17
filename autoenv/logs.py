"""Safe, deterministic processing primitives for one collected log batch.

The module deliberately separates the pipeline into five phases:

1. download one or more remote directories into ``raw/``;
2. recursively expand supported archives into ``expanded/``;
3. select source files and establish their stable processing order;
4. apply line/block rules and retain structured records in memory;
5. write human-readable targets, a query index, and ``manifest.json``.

Keeping these phases explicit is important when product-specific log rules are
refined: rule changes belong in :class:`LogGroup` or the calling Web Tool, while
remote transfer and archive-safety behavior remain unchanged.
"""

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
class LogSource:
    """One script-owned remote directory and the basename glob collected from it.

    ``name`` is the stable key returned by :meth:`LogCollection.source_groups`.
    Keeping the path and glob in Python makes the collection scope reviewable in
    Git instead of accepting an arbitrary remote path from the Web request.
    """

    name: str
    remote_dir: str
    glob: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("log source name must be a string")
        name = self.name.strip()
        if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError(
                "log source name must use letters, digits, '.', '_' or '-'"
            )
        if not isinstance(self.remote_dir, str):
            raise TypeError("log source remote_dir must be a string")
        remote_dir = self.remote_dir.strip()
        if not remote_dir:
            raise ValueError("log source remote_dir must not be empty")
        if not isinstance(self.glob, str):
            raise TypeError("log source glob must be a string")
        glob = self.glob.strip()
        if not glob:
            raise ValueError("log source glob must not be empty")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "remote_dir", remote_dir)
        object.__setattr__(self, "glob", glob)


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
    """Parse timestamps from log lines through a small named-group contract.

    A caller owns the concrete regular expression because products format time
    differently.  The framework only understands the six names in
    ``_TIME_FIELDS`` and requires ``hour`` plus ``minute`` so that the Web query
    layer can always calculate a time-of-day value.
    """

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
    """Own the artifacts and state transitions of a single log collection run.

    A collection is intentionally single-use.  Download happens once (from one
    or many remote directories), followed by extraction, matching, and final
    output.  ``_failed`` prevents a partially processed batch from becoming
    queryable, while ``manifest.json`` exposes the current state to operators.
    """

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
        # Retain one result per remote directory.  Apart from making the
        # manifest auditable, this avoids flattening away the source directory
        # when two servers paths contain the same basename.
        self._downloads: list[RemoteBatchDownloadResult] = []
        self._sources: tuple[LogSource, ...] = ()
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
        """Download one remote directory while preserving the original API."""

        self._ensure_download_not_started()
        if protocol != "scp":
            raise ValueError("log collection currently supports protocol='scp' only")
        method = getattr(host, "scp_download_many", None)
        if method is None:
            raise TypeError("host must be a registered SSHHost")
        result = method(remote_dir, glob=glob, destination=self.raw_dir)
        if not isinstance(result, RemoteBatchDownloadResult):
            raise TypeError("scp_download_many returned an invalid result")
        self._downloads.append(result)
        self._remember_downloaded_files(result)
        if not result.success:
            self._failed = True
            self._write_manifest(
                "failed", error=result.error_message, download=self._download_manifest()
            )
        return result

    def download_many(
        self,
        host: object,
        *,
        remote_dirs: Sequence[str],
        glob: str = "cpdt_*",
        protocol: str = "scp",
    ) -> LogOperationResult:
        """Download matching files from several remote directories as one batch.

        Every directory receives a local ``source-NNN`` namespace.  This is not
        cosmetic: remote directories commonly reuse names such as
        ``cpdt_journal.log`` and flattening them into ``raw/`` would overwrite a
        source before analysis.  The namespace is carried into ``expanded/``
        and the SQLite ``source_file`` column, so a displayed line remains
        traceable to its collection source.

        The operation is all-or-nothing.  If any directory cannot be listed or
        downloaded, files obtained from earlier directories are removed and the
        manifest remains ``failed``.  Analysis therefore never presents a
        partial multi-directory batch as complete.
        """

        if isinstance(remote_dirs, (str, bytes)):
            raise TypeError("remote_dirs must be a sequence of directory strings")
        directories = tuple(self._normalize_remote_dirs(remote_dirs))
        sources = tuple(
            LogSource(
                name=f"source-{index:03d}",
                remote_dir=remote_dir,
                glob=glob,
            )
            for index, remote_dir in enumerate(directories, start=1)
        )
        return self._download_sources(
            host,
            sources=sources,
            protocol=protocol,
            recorder_name="LOG DOWNLOAD MANY",
        )

    def download_sources(
        self,
        host: object,
        *,
        sources: Sequence[LogSource],
        protocol: str = "scp",
    ) -> LogOperationResult:
        """Download script-declared path/glob pairs into isolated source trees.

        Every source receives ``raw/source-NNN`` and later one independent
        :class:`LogGroup`.  Source names must be unique, and repeating the same
        path/glob pair is rejected before any remote operation.  Different
        globs may intentionally read the same remote directory as separate
        groups.
        """

        normalized = self._normalize_sources(sources)
        return self._download_sources(
            host,
            sources=normalized,
            protocol=protocol,
            recorder_name="LOG DOWNLOAD SOURCES",
        )

    def _download_sources(
        self,
        host: object,
        *,
        sources: tuple[LogSource, ...],
        protocol: str,
        recorder_name: str,
    ) -> LogOperationResult:
        """Shared all-or-nothing transfer implementation for configured sources."""

        self._ensure_download_not_started()
        if protocol != "scp":
            raise ValueError("log collection currently supports protocol='scp' only")
        method = getattr(host, "scp_download_many", None)
        if method is None:
            raise TypeError("host must be a registered SSHHost")
        self._sources = sources

        started = datetime.now().astimezone()
        operation_id = self.recorder.next_operation_id()
        output_count = 0
        failure: RemoteBatchDownloadResult | None = None

        for index, source in enumerate(sources, start=1):
            # scp_download_many requires an empty destination.  A dedicated
            # child directory also guarantees that equal basenames from two
            # remote paths can coexist without renaming the original files.
            destination = self.raw_dir / f"source-{index:03d}"
            result = method(
                source.remote_dir,
                glob=source.glob,
                destination=destination,
            )
            if not isinstance(result, RemoteBatchDownloadResult):
                raise TypeError("scp_download_many returned an invalid result")
            self._downloads.append(result)
            if not result.success:
                failure = result
                break
            self._remember_downloaded_files(result)
            output_count += len(result.files)

        if failure is not None:
            self._failed = True
            # The per-directory transfer result is already recorded by SSHHost;
            # keep its diagnostics in the manifest before removing local data.
            self._write_manifest(
                "failed", error=failure.error_message, download=self._download_manifest()
            )
            self._clear_raw_downloads()
            result = self._operation_result(
                operation_id,
                started,
                False,
                failure.status,
                output_count,
                error_type=failure.error_type,
                error_message=failure.error_message,
            )
        else:
            result = self._operation_result(
                operation_id, started, True, "success", output_count
            )
        self.recorder.record_result(recorder_name, result)
        return result

    def extract_all(self) -> LogOperationResult:
        """Copy/expand every downloaded source into the analysis tree."""

        started = datetime.now().astimezone()
        operation_id = self.recorder.next_operation_id()
        try:
            if not self._downloads or any(not item.success for item in self._downloads):
                raise RuntimeError("a successful download is required before extraction")
            counter = [0, 0]
            for download in self._downloads:
                for item in download.files:
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
        """Select analyzable log files and freeze their deterministic order."""

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
        # Remote mtime reconstructs chronology across rotated archives.  The
        # relative-path tie breakers make repeated runs deterministic even when
        # several remote directories report the same mtime.
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

    def source_groups(
        self,
        *,
        timestamp: TimestampPattern,
        encoding: str | Sequence[str] = ("utf-8", "utf-8-sig", "gb18030", "latin-1"),
    ) -> dict[str, "LogGroup"]:
        """Build one group from every script-declared source after extraction.

        The remote glob decides which top-level files enter a source.  Archive
        containers remain in ``expanded`` for audit but are not decoded as log
        text; their recursively expanded non-archive descendants, together with
        directly downloaded plain files, become that source's group.  No second
        basename glob is applied after extraction.
        """

        if not self._extracted:
            raise RuntimeError("extract_all() must succeed before source_groups()")
        if not self._sources:
            raise RuntimeError("download_sources() or download_many() is required")
        encodings = (encoding,) if isinstance(encoding, str) else tuple(encoding)
        if not encodings or any(not isinstance(item, str) or not item for item in encodings):
            raise ValueError("encoding must contain at least one valid codec name")

        groups: dict[str, LogGroup] = {}
        file_order_start = 0
        for index, source in enumerate(self._sources, start=1):
            source_root = self.expanded_dir / f"source-{index:03d}"
            files = [
                path
                for path in source_root.rglob("*")
                if path.is_file() and _archive_type(path) is None
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
                message = f"log source {source.name!r} produced no analyzable files"
                self._write_manifest(
                    "failed", error=message, download=self._download_manifest()
                )
                raise FileNotFoundError(message)
            groups[source.name] = LogGroup(
                self,
                files,
                timestamp,
                encodings,
                file_order_start=file_order_start,
            )
            file_order_start += len(files)
        return groups

    def finalize(self) -> LogOperationResult:
        """Persist matched records and atomically mark the batch queryable."""

        started = datetime.now().astimezone()
        operation_id = self.recorder.next_operation_id()
        try:
            if self._finalized:
                raise RuntimeError("log collection is already finalized")
            if self._failed:
                raise RuntimeError("log collection contains a failed operation")
            # Rules may append to the same target in several passes.  Sorting by
            # source position first and declaration order last restores original
            # file order instead of grouping records by the rule that found them.
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
        # Preserve the raw source namespace for multi-directory collections.
        # A single-directory collection still has a one-component relative path
        # and therefore retains its historical expanded layout.
        relative_source = source.relative_to(self.raw_dir)
        target = self.expanded_dir / relative_source
        if target.exists():
            raise FileExistsError(f"expanded path already exists: {relative_source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self._source_mtimes[target.resolve()] = remote_mtime
        self._count_file(target, counter)
        self._expand_archive_recursive(target, remote_mtime, counter)

    def _expand_archive_recursive(self, archive: Path, inherited_mtime: float, counter: list[int]) -> None:
        # Archives are kept beside their expanded directory.  Keeping the
        # original file is useful for incident review and makes recursive
        # expansion explicit (``x.zip.expanded/y.log.gz.expanded``).
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
        # All descendants inherit the top-level remote mtime.  Archive member
        # mtimes are inconsistent between formats and would otherwise disturb
        # cross-file ordering.
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
        # Rebuild rather than mutate an old index.  A batch only becomes visible
        # after the ready manifest is atomically written, so readers never need
        # to observe a half-populated database.
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
        if not self._downloads:
            return None
        first = self._downloads[0]
        return {
            "protocol": first.protocol,
            "target_name": first.target_name,
            # remote_dir remains for consumers of schema v1.  New consumers
            # should use remote_dirs, which is complete for both one and many.
            "remote_dir": first.remote_dir if len(self._downloads) == 1 else None,
            "remote_dirs": [item.remote_dir for item in self._downloads],
            "glob": first.glob if all(item.glob == first.glob for item in self._downloads) else None,
            "sources": [
                {
                    "name": source.name,
                    "remote_dir": source.remote_dir,
                    "glob": source.glob,
                }
                for source in self._sources
            ],
            "directories": [
                {
                    "name": self._sources[index].name if index < len(self._sources) else None,
                    "remote_dir": download.remote_dir,
                    "glob": download.glob,
                    "success": download.success,
                    "status": download.status,
                    "error_type": download.error_type,
                    "error_message": download.error_message,
                    "matched_count": download.matched_count,
                    "completed_count": download.completed_count,
                    "file_count": len(download.files),
                }
                for index, download in enumerate(self._downloads)
            ],
            "files": [
                {
                    "name": item.name,
                    "remote_file": item.remote_file,
                    "remote_size": item.remote_size,
                    "remote_mtime": item.remote_mtime,
                }
                for download in self._downloads
                for item in download.files
            ],
        }

    def _ensure_download_not_started(self) -> None:
        if self._downloads:
            raise RuntimeError("log collection download has already started")
        if self._failed or self._extracted or self._finalized:
            raise RuntimeError("log collection is not ready for download")

    @staticmethod
    def _normalize_remote_dirs(remote_dirs: Sequence[str]) -> list[str]:
        directories: list[str] = []
        seen: set[str] = set()
        for value in remote_dirs:
            if not isinstance(value, str):
                raise TypeError("each remote directory must be a string")
            directory = value.strip()
            if not directory:
                raise ValueError("remote directories must not contain empty entries")
            if directory in seen:
                raise ValueError(f"duplicate remote directory: {directory}")
            seen.add(directory)
            directories.append(directory)
        if not directories:
            raise ValueError("at least one remote directory is required")
        return directories

    @staticmethod
    def _normalize_sources(sources: Sequence[LogSource]) -> tuple[LogSource, ...]:
        if isinstance(sources, (str, bytes)):
            raise TypeError("sources must be a sequence of LogSource values")
        values = tuple(sources)
        if not values:
            raise ValueError("at least one log source is required")
        if any(not isinstance(item, LogSource) for item in values):
            raise TypeError("each source must be a LogSource")
        names: set[str] = set()
        pairs: set[tuple[str, str]] = set()
        for source in values:
            if source.name in names:
                raise ValueError(f"duplicate log source name: {source.name}")
            pair = (source.remote_dir, source.glob)
            if pair in pairs:
                raise ValueError(
                    f"duplicate log source path/glob: {source.remote_dir} {source.glob}"
                )
            names.add(source.name)
            pairs.add(pair)
        return values

    def _remember_downloaded_files(self, result: RemoteBatchDownloadResult) -> None:
        for item in result.files:
            self._source_mtimes[Path(item.local_file).resolve()] = item.remote_mtime

    def _clear_raw_downloads(self) -> None:
        # Keep the collection directory shape stable for diagnostics while
        # ensuring a failed all-or-nothing transfer leaves no misleading files.
        shutil.rmtree(self.raw_dir, ignore_errors=True)
        self.raw_dir.mkdir()
        self._source_mtimes.clear()

    def _operation_result(
        self,
        operation_id: str,
        started: datetime,
        success: bool,
        status: str,
        output_count: int,
        error: Exception | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
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
            error_type=error_type or (type(error).__name__ if error else None),
            error_message=error_message or (str(error) if error else None),
        )


class LogGroup:
    """Apply product rules to an ordered set of decoded log files."""

    def __init__(
        self,
        collection: LogCollection,
        files: list[Path],
        timestamp: TimestampPattern,
        encodings: tuple[str, ...],
        *,
        file_order_start: int = 0,
    ) -> None:
        self.collection = collection
        self.files = files
        self.timestamp = timestamp
        self.encodings = encodings
        self.file_order_start = file_order_start
        self._rule_order = 0

    def match_line(self, regex: str, target_file: str) -> LogOperationResult:
        """Copy matching lines, inheriting the last timestamp in the same file."""

        pattern = re.compile(regex)
        target = _target_file(target_file)
        started = datetime.now().astimezone()
        operation_id = self.collection.recorder.next_operation_id()
        self._rule_order += 1
        count = 0
        try:
            for file_order, path in enumerate(self.files, start=self.file_order_start):
                # Reset at each file boundary: a rotated file may start a new
                # process/session and must not inherit time from its predecessor.
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

    def match_block(
        self,
        begin_regex: str,
        end_regex: str,
        target_file: str,
        *,
        exclude_regex: str | None = None,
    ) -> LogOperationResult:
        """Extract block bodies with explicit and implicit boundary handling.

        Begin/end marker lines are deliberately excluded.  Text before the
        first begin (or after an end) is an implicit block; this preserves
        useful payload emitted by truncated/rotated logs.  A repeated begin
        inside an explicit block is treated as content-free metadata rather
        than resetting the already collected body.  ``exclude_regex`` removes
        matching body lines after the block and its correlation timestamp have
        been selected; boundary recognition and time inheritance are unchanged.
        """

        begin = re.compile(begin_regex)
        end = re.compile(end_regex)
        if exclude_regex is not None and not isinstance(exclude_regex, str):
            raise TypeError("exclude_regex must be a string or None")
        if isinstance(exclude_regex, str) and not exclude_regex.strip():
            raise ValueError("exclude_regex must not be empty")
        exclude = re.compile(exclude_regex) if exclude_regex is not None else None
        target = _target_file(target_file)
        started = datetime.now().astimezone()
        operation_id = self.collection.recorder.next_operation_id()
        self._rule_order += 1
        count = 0
        try:
            for file_order, path in enumerate(self.files, start=self.file_order_start):
                previous: LogTimestamp | None = None
                segment_previous: LogTimestamp | None = None
                explicit = False
                anchor: LogTimestamp | None = None
                buffer: list[tuple[int, str, LogTimestamp | None]] = []

                def flush(*, incomplete: bool) -> None:
                    nonlocal count, buffer
                    if not buffer:
                        return
                    # One block has one correlation time.  Explicit blocks use
                    # the begin marker (or the timestamp just before it);
                    # implicit blocks prefer their first timestamp and finally
                    # fall back to the previous completed segment.
                    selected = anchor if explicit else next((stamp for _, _, stamp in buffer if stamp), segment_previous)
                    if explicit and selected:
                        source = "block_begin"
                    elif any(stamp for _, _, stamp in buffer):
                        source = "block_first"
                    elif selected:
                        source = "block_inherited"
                    else:
                        source = "unknown"
                    retained = [
                        item
                        for item in buffer
                        if exclude is None or exclude.search(item[1]) is None
                    ]
                    for line_number, text, _stamp in retained:
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
                            # A real begin supersedes any implicit preamble
                            # accumulated since the file start or previous end.
                            buffer = []
                            explicit = True
                            anchor = parsed or before
                        continue
                    if end.search(line):
                        # Consecutive end markers are valid: flush is a no-op
                        # when the implicit/explicit body is empty.
                        flush(incomplete=False)
                        buffer = []
                        explicit = False
                        anchor = None
                        segment_previous = previous
                        continue
                    buffer.append((line_number, line, parsed))
                if explicit:
                    # EOF before an end marker retains the block but marks every
                    # row incomplete in SQLite for downstream diagnostics.
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
        # Decode the whole file once per rule.  Strict decoding is intentional:
        # the first codec that can represent every byte becomes auditable in the
        # manifest; replacement characters would hide corruption.
        payload = path.read_bytes()
        last_error: UnicodeDecodeError | None = None
        for encoding in self.encodings:
            try:
                text = payload.decode(encoding)
                relative = str(path.relative_to(self.collection.expanded_dir))
                self.collection._source_encodings[relative] = encoding
                # A collection can decode hundreds of files and may read each
                # file once per rule.  Keep the auditable codec decision in
                # run.log without turning the Web event stream into a file list.
                self.collection.recorder.log(
                    f"LOG ENCODING path={path} encoding={encoding}",
                    console=False,
                )
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
