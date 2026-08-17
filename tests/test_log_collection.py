from __future__ import annotations

import gzip
import io
import json
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from autoenv import web_tools
from autoenv.log_query import list_log_batches, query_log_records
from autoenv.logs import LogCollection, LogSource, TimestampPattern
from autoenv.registry import list_scripts
from autoenv.results import RemoteBatchDownloadResult, RemoteDownloadedFile
from autoenv.web_tools import WebToolDefinition, describe_tools, run_web_tool, run_workflow_tool


class Recorder:
    def __init__(self) -> None:
        self.index = 0
        self.results: list[tuple[str, object]] = []
        self.messages: list[str] = []

    def next_operation_id(self) -> str:
        self.index += 1
        return f"{self.index:04d}"

    def record_result(self, name: str, result: object) -> None:
        self.results.append((name, result))

    def log(self, message: str, **_options: object) -> None:
        self.messages.append(message)


class FakeBatchHost:
    def __init__(self, files: list[tuple[Path, float]]) -> None:
        self.files = files

    def scp_download_many(self, remote_dir: str, *, glob: str, destination: Path):
        now = datetime.now().astimezone()
        downloaded = []
        for source, mtime in self.files:
            target = destination / source.name
            shutil.copy2(source, target)
            downloaded.append(
                RemoteDownloadedFile(
                    name=source.name,
                    remote_file=f"{remote_dir}/{source.name}",
                    local_file=str(target),
                    remote_size=target.stat().st_size,
                    remote_mtime=mtime,
                )
            )
        return RemoteBatchDownloadResult(
            run_id="sample-run",
            operation_id="download",
            protocol="scp",
            target_name="log_server",
            remote_dir=remote_dir,
            glob=glob,
            success=True,
            status="success",
            started_at=now,
            finished_at=now,
            duration_ms=0,
            destination=str(destination),
            files=tuple(downloaded),
        )


class FakeMultiDirectoryHost:
    """Serve a different local fixture set for each requested remote path."""

    def __init__(self, files_by_dir: dict[str, list[tuple[Path, float]]]) -> None:
        self.files_by_dir = files_by_dir
        self.calls: list[tuple[str, Path]] = []
        self.globs: list[str] = []

    def scp_download_many(self, remote_dir: str, *, glob: str, destination: Path):
        now = datetime.now().astimezone()
        destination.mkdir(parents=True, exist_ok=True)
        self.calls.append((remote_dir, destination))
        self.globs.append(glob)
        downloaded = []
        for source, mtime in self.files_by_dir[remote_dir]:
            target = destination / source.name
            shutil.copy2(source, target)
            downloaded.append(
                RemoteDownloadedFile(
                    name=source.name,
                    remote_file=f"{remote_dir}/{source.name}",
                    local_file=str(target),
                    remote_size=target.stat().st_size,
                    remote_mtime=mtime,
                )
            )
        return RemoteBatchDownloadResult(
            run_id="multi-run",
            operation_id=f"download-{len(self.calls)}",
            protocol="scp",
            target_name="log_server",
            remote_dir=remote_dir,
            glob=glob,
            success=True,
            status="success",
            started_at=now,
            finished_at=now,
            duration_ms=0,
            destination=str(destination),
            files=tuple(downloaded),
        )


TIMESTAMP = TimestampPattern(
    r"(?:(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\s+)?"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)


def _sample_collection(tmp_path: Path) -> LogCollection:
    source = tmp_path / "source"
    source.mkdir()
    first = source / "cpdt_journal_01.log"
    first.write_text(
        """2026-08-16 08:19:58 [SYSTEM] service started
2026-08-16 08:20:10 [DB] BEGIN request=1001
SQL=select * from users
rows=2
2026-08-16 08:20:11 [DB] BEGIN nested-marker-is-ignored
retry=1
2026-08-16 08:20:15 [DB] END request=1001
2026-08-16 08:20:35 [AUTH] login success user=alice
[AUTH] session created id=abc
""",
        encoding="utf-8",
    )
    second = source / "cpdt_journal_02.log.gz"
    with gzip.open(second, "wb") as handle:
        handle.write(
            """[AUTH] early message without timestamp
orphan response code=503
2026-08-16 08:21:40 payload retry=1
2026-08-16 08:21:45 [DB] END request=1002
2026-08-16 08:22:00 reconnecting database
2026-08-16 08:22:03 [DB] END request=1003
08:22:10 [AUTH] login failed user=bob
[AUTH] reason=invalid-token
""".encode()
        )
    third = source / "cpdt_bundle.zip"
    with zipfile.ZipFile(third, "w") as archive:
        archive.writestr(
            "logs/cpdt_journal_03.log",
            """2026-08-16 08:24:00 [DB] BEGIN request=1004
phase=prepare
2026-08-16 08:24:01 [DB] BEGIN duplicate-begin
phase=commit
result=success
""",
        )
    run_dir = tmp_path / "logs" / "sample-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="sample-run", run_dir=run_dir, recorder=Recorder(), alias="样例批次"
    )
    result = collection.download(
        FakeBatchHost([(first, 1.0), (second, 2.0), (third, 3.0)]),
        remote_dir="/var/log/product",
        glob="cpdt_*",
    )
    assert result.success
    assert collection.extract_all().success
    group = collection.group(glob="cpdt*.log", timestamp=TIMESTAMP)
    assert group.match_line(r"\[AUTH\]", "auth.log").success
    assert group.match_block(r"\[DB\] BEGIN\b", r"\[DB\] END\b", "database.log").success
    assert collection.finalize().success
    return collection


def test_confirmed_log_samples_generate_expected_targets_and_index(tmp_path: Path):
    collection = _sample_collection(tmp_path)
    assert (collection.targets_dir / "auth.log").read_text(encoding="utf-8") == """[2026-08-16 08:20:35] 2026-08-16 08:20:35 [AUTH] login success user=alice
[2026-08-16 08:20:35] [AUTH] session created id=abc
[-] [AUTH] early message without timestamp
[08:22:10] 08:22:10 [AUTH] login failed user=bob
[08:22:10] [AUTH] reason=invalid-token
"""
    assert (collection.targets_dir / "database.log").read_text(encoding="utf-8") == """[2026-08-16 08:20:10] SQL=select * from users
[2026-08-16 08:20:10] rows=2
[2026-08-16 08:20:10] retry=1
[2026-08-16 08:21:40] [AUTH] early message without timestamp
[2026-08-16 08:21:40] orphan response code=503
[2026-08-16 08:21:40] 2026-08-16 08:21:40 payload retry=1
[2026-08-16 08:22:00] 2026-08-16 08:22:00 reconnecting database
[2026-08-16 08:24:00] phase=prepare
[2026-08-16 08:24:00] phase=commit
[2026-08-16 08:24:00] result=success
"""
    with sqlite3.connect(collection.index_path) as connection:
        assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 15
        assert connection.execute(
            "SELECT count(*) FROM records WHERE incomplete_block = 1"
        ).fetchone()[0] == 3
    manifest = json.loads(collection.manifest_path.read_text(encoding="utf-8"))
    assert manifest["alias"] == "样例批次"
    assert manifest["batch_id"] == "sample-run"
    assert manifest["download"]["glob"] == "cpdt_*"
    assert set(manifest["source_encodings"].values()) == {"utf-8"}
    assert "password" not in json.dumps(manifest).lower()


def test_multiple_remote_directories_keep_equal_basenames_isolated(tmp_path: Path):
    first_fixture = tmp_path / "first-fixture"
    second_fixture = tmp_path / "second-fixture"
    first_fixture.mkdir()
    second_fixture.mkdir()
    first = first_fixture / "cpdt_same.log"
    second = second_fixture / "cpdt_same.log"
    first.write_text("09:00:00 [AUTH] from first\n", encoding="utf-8")
    second.write_text("08:00:00 [AUTH] from second\n", encoding="utf-8")
    host = FakeMultiDirectoryHost(
        {
            "/logs/first": [(first, 20.0)],
            "/logs/second": [(second, 10.0)],
        }
    )
    run_dir = tmp_path / "logs" / "multi-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="multi-run", run_dir=run_dir, recorder=Recorder(), alias="多目录"
    )

    downloaded = collection.download_many(
        host,
        remote_dirs=["/logs/first", "/logs/second"],
        glob="cpdt_*",
    )
    assert downloaded.success and downloaded.output_count == 2
    assert [path.name for _, path in host.calls] == ["source-001", "source-002"]
    assert (collection.raw_dir / "source-001/cpdt_same.log").is_file()
    assert (collection.raw_dir / "source-002/cpdt_same.log").is_file()

    assert collection.extract_all().success
    group = collection.group(glob="cpdt*.log", timestamp=TIMESTAMP)
    assert [str(path.relative_to(collection.expanded_dir)) for path in group.files] == [
        "source-002/cpdt_same.log",
        "source-001/cpdt_same.log",
    ]
    assert group.match_line(r"\[AUTH\]", "auth.log").success
    assert collection.finalize().success
    assert (collection.targets_dir / "auth.log").read_text(encoding="utf-8") == (
        "[08:00:00] 08:00:00 [AUTH] from second\n"
        "[09:00:00] 09:00:00 [AUTH] from first\n"
    )

    manifest = json.loads(collection.manifest_path.read_text(encoding="utf-8"))
    assert manifest["download"]["remote_dir"] is None
    assert manifest["download"]["remote_dirs"] == ["/logs/first", "/logs/second"]
    assert [item["file_count"] for item in manifest["download"]["directories"]] == [1, 1]


def test_scripted_sources_use_per_path_globs_and_form_groups_after_extraction(
    tmp_path: Path,
):
    auth_fixtures = tmp_path / "auth-fixtures"
    database_fixtures = tmp_path / "database-fixtures"
    auth_fixtures.mkdir()
    database_fixtures.mkdir()
    auth_plain = auth_fixtures / "auth_current.log"
    auth_plain.write_text("08:00:00 [AUTH] current\n", encoding="utf-8")
    auth_archive = auth_fixtures / "auth_bundle.zip"
    with zipfile.ZipFile(auth_archive, "w") as archive:
        archive.writestr("nested/auth_archived.log", "08:01:00 [AUTH] archived\n")
    database_archive = database_fixtures / "db_bundle.zip"
    with zipfile.ZipFile(database_archive, "w") as archive:
        archive.writestr(
            "database.log",
            "08:02:00 [DB] BEGIN\n"
            "debug=remove this line\n"
            "SQL=select 1\n"
            "08:02:01 [DB] END\n",
        )

    host = FakeMultiDirectoryHost(
        {
            "/logs/auth": [(auth_plain, 1.0), (auth_archive, 2.0)],
            "/logs/database": [(database_archive, 3.0)],
        }
    )
    run_dir = tmp_path / "logs" / "scripted-source-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="scripted-source-run", run_dir=run_dir, recorder=Recorder()
    )
    sources = (
        LogSource(name="auth", remote_dir="/logs/auth", glob="auth_*"),
        LogSource(name="database", remote_dir="/logs/database", glob="db_*"),
    )

    downloaded = collection.download_sources(host, sources=sources)
    assert downloaded.success and downloaded.output_count == 3
    assert [item[0] for item in host.calls] == ["/logs/auth", "/logs/database"]
    assert host.globs == ["auth_*", "db_*"]
    assert collection.extract_all().success

    groups = collection.source_groups(timestamp=TIMESTAMP)
    assert list(groups) == ["auth", "database"]
    assert [path.name for path in groups["auth"].files] == [
        "auth_current.log",
        "auth_archived.log",
    ]
    assert [path.name for path in groups["database"].files] == ["database.log"]
    assert all(path.suffix not in {".zip", ".gz"} for group in groups.values() for path in group.files)

    assert groups["auth"].match_line(r"\[AUTH\]", "auth.log").success
    assert groups["database"].match_block(
        r"\[DB\] BEGIN\b",
        r"\[DB\] END\b",
        "database.log",
        exclude_regex=r"^debug=",
    ).success
    assert collection.finalize().success
    assert (collection.targets_dir / "auth.log").read_text(encoding="utf-8") == (
        "[08:00:00] 08:00:00 [AUTH] current\n"
        "[08:01:00] 08:01:00 [AUTH] archived\n"
    )
    assert (collection.targets_dir / "database.log").read_text(encoding="utf-8") == (
        "[08:02:00] SQL=select 1\n"
    )
    manifest = json.loads(collection.manifest_path.read_text(encoding="utf-8"))
    assert manifest["download"]["glob"] is None
    assert manifest["download"]["sources"] == [
        {"name": "auth", "remote_dir": "/logs/auth", "glob": "auth_*"},
        {"name": "database", "remote_dir": "/logs/database", "glob": "db_*"},
    ]


def test_match_block_exclusion_keeps_block_time_from_removed_line(tmp_path: Path):
    fixture = tmp_path / "cpdt_implicit.log"
    fixture.write_text(
        "08:05:00 debug=remove timestamp carrier\n"
        "payload retained\n"
        "08:05:10 [DB] END\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "logs" / "block-exclusion-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="block-exclusion-run", run_dir=run_dir, recorder=Recorder()
    )
    assert collection.download(
        FakeBatchHost([(fixture, 1.0)]), remote_dir="/logs", glob="cpdt_*"
    ).success
    assert collection.extract_all().success
    group = collection.group(glob="cpdt*.log", timestamp=TIMESTAMP)

    matched = group.match_block(
        r"\[DB\] BEGIN\b",
        r"\[DB\] END\b",
        "database.log",
        exclude_regex=r"^\d{2}:\d{2}:\d{2} debug=",
    )

    assert matched.success and matched.output_count == 1
    assert collection.finalize().success
    assert (collection.targets_dir / "database.log").read_text(encoding="utf-8") == (
        "[08:05:00] payload retained\n"
    )


def test_scripted_sources_reject_duplicate_names_before_transfer(tmp_path: Path):
    run_dir = tmp_path / "logs" / "duplicate-source-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="duplicate-source-run", run_dir=run_dir, recorder=Recorder()
    )
    host = FakeMultiDirectoryHost({"/logs/one": [], "/logs/two": []})

    with pytest.raises(ValueError, match="duplicate log source name"):
        collection.download_sources(
            host,
            sources=[
                LogSource("same", "/logs/one", "one_*"),
                LogSource("same", "/logs/two", "two_*"),
            ],
        )
    assert host.calls == []


def test_multiple_remote_directories_reject_duplicates_before_transfer(tmp_path: Path):
    run_dir = tmp_path / "logs" / "duplicate-dir-run"
    run_dir.mkdir(parents=True)
    host = FakeMultiDirectoryHost({"/logs": []})
    collection = LogCollection(
        run_id="duplicate-dir-run", run_dir=run_dir, recorder=Recorder()
    )

    with pytest.raises(ValueError, match="duplicate remote directory"):
        collection.download_many(host, remote_dirs=["/logs", " /logs "])
    assert host.calls == []


def test_multiple_remote_directories_fail_as_one_batch_and_clean_raw_files(tmp_path: Path):
    fixture = tmp_path / "cpdt_first.log"
    fixture.write_text("08:00:00 [AUTH] first\n", encoding="utf-8")

    class SecondDirectoryFails(FakeMultiDirectoryHost):
        def scp_download_many(
            self, remote_dir: str, *, glob: str, destination: Path
        ):
            if remote_dir == "/logs/missing":
                now = datetime.now().astimezone()
                destination.mkdir(parents=True, exist_ok=True)
                self.calls.append((remote_dir, destination))
                return RemoteBatchDownloadResult(
                    run_id="failed-multi-run",
                    operation_id="download-2",
                    protocol="scp",
                    target_name="log_server",
                    remote_dir=remote_dir,
                    glob=glob,
                    success=False,
                    status="remote_file_not_found",
                    started_at=now,
                    finished_at=now,
                    duration_ms=0,
                    destination=str(destination),
                    error_type="REMOTE_FILE_NOT_FOUND",
                    error_message="no matching files",
                )
            return super().scp_download_many(
                remote_dir, glob=glob, destination=destination
            )

    host = SecondDirectoryFails({"/logs/first": [(fixture, 1.0)]})
    run_dir = tmp_path / "logs" / "failed-multi-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="failed-multi-run", run_dir=run_dir, recorder=Recorder()
    )

    result = collection.download_many(
        host, remote_dirs=["/logs/first", "/logs/missing"]
    )

    assert not result.success
    assert result.status == "remote_file_not_found"
    assert result.error_type == "REMOTE_FILE_NOT_FOUND"
    assert list(collection.raw_dir.iterdir()) == []
    manifest = json.loads(collection.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["download"]["remote_dirs"] == [
        "/logs/first",
        "/logs/missing",
    ]
    assert [entry["success"] for entry in manifest["download"]["directories"]] == [
        True,
        False,
    ]


def test_log_batch_query_supports_clock_windows_unknown_rows_and_paging(tmp_path: Path):
    collection = _sample_collection(tmp_path)
    root = tmp_path
    assert list_log_batches(root)[0]["batch_id"] == "sample-run"
    around = query_log_records(
        root,
        "sample-run",
        "auth.log",
        query_time="08:22:00",
        window_minutes=2,
    )
    assert [item["timestamp"] for item in around["records"]] == ["08:22:10", "08:22:10"]
    all_rows = query_log_records(root, "sample-run", "auth.log", limit=2)
    assert all_rows["total"] == 5 and all_rows["has_more"] is True
    assert any(item["timestamp"] == "-" for item in query_log_records(root, "sample-run", "auth.log")["records"])


def test_log_batch_find_filters_whole_target_case_insensitively_and_combines_with_time(
    tmp_path: Path,
):
    _sample_collection(tmp_path)
    found = query_log_records(
        tmp_path, "sample-run", "auth.log", keyword="  LOGIN  ", limit=1
    )
    assert found["keyword"] == "LOGIN"
    assert found["total"] == 2 and found["has_more"] is True
    assert "login success" in found["records"][0]["text"]

    around = query_log_records(
        tmp_path,
        "sample-run",
        "auth.log",
        keyword="login",
        query_time="08:22:00",
        window_minutes=2,
    )
    assert [row["text"] for row in around["records"]] == [
        "08:22:10 [AUTH] login failed user=bob"
    ]
    with pytest.raises(ValueError, match="200"):
        query_log_records(
            tmp_path, "sample-run", "auth.log", keyword="x" * 201
        )


@pytest.mark.parametrize("member", ["../outside.log", "C:/outside.log", "/outside.log"])
def test_recursive_log_extraction_rejects_unsafe_zip_paths(tmp_path: Path, member: str):
    source = tmp_path / "bad.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(member, "bad")
    run_dir = tmp_path / "logs" / "bad-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(run_id="bad-run", run_dir=run_dir, recorder=Recorder())
    assert collection.download(FakeBatchHost([(source, 1.0)]), remote_dir="/logs", glob="*").success
    result = collection.extract_all()
    assert not result.success and "unsafe archive member" in str(result.error_message)


def test_recursive_log_extraction_rejects_zip_links(tmp_path: Path):
    source = tmp_path / "link.zip"
    member = zipfile.ZipInfo("linked.log")
    member.create_system = 3
    member.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(member, "target.log")
    run_dir = tmp_path / "logs" / "link-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(run_id="link-run", run_dir=run_dir, recorder=Recorder())
    collection.download(FakeBatchHost([(source, 1.0)]), remote_dir="/logs", glob="*")
    result = collection.extract_all()
    assert not result.success and "links are not allowed" in str(result.error_message)


def test_recursive_log_extraction_handles_tar_tgz_and_nested_archives(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    tar_gz = source / "cpdt_tar.tar.gz"
    tgz = source / "cpdt_more.tgz"
    for archive_path, member_name in (
        (tar_gz, "inside/cpdt_tar.log"),
        (tgz, "cpdt_tgz.log"),
    ):
        payload = b"08:00:00 [AUTH] from tar\n"
        member = tarfile.TarInfo(member_name)
        member.size = len(payload)
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.addfile(member, io.BytesIO(payload))

    nested_gzip = io.BytesIO()
    with gzip.GzipFile(fileobj=nested_gzip, mode="wb") as handle:
        handle.write(b"08:00:01 [AUTH] nested\n")
    nested_zip = source / "cpdt_nested.zip"
    with zipfile.ZipFile(nested_zip, "w") as archive:
        archive.writestr("deep/cpdt_nested.log.gz", nested_gzip.getvalue())

    run_dir = tmp_path / "logs" / "archive-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="archive-run", run_dir=run_dir, recorder=Recorder()
    )
    collection.download(
        FakeBatchHost([(tar_gz, 1.0), (tgz, 2.0), (nested_zip, 3.0)]),
        remote_dir="/logs",
        glob="cpdt_*",
    )
    assert collection.extract_all().success
    group = collection.group(glob="cpdt*.log", timestamp=TIMESTAMP)
    assert [path.name for path in group.files] == [
        "cpdt_tar.log",
        "cpdt_tgz.log",
        "cpdt_nested.log",
    ]


def test_log_extraction_rejects_duplicate_members_and_cleans_partial_output(tmp_path: Path):
    source = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("cpdt.log", "first")
        archive.writestr("cpdt.log", "second")
    run_dir = tmp_path / "logs" / "duplicate-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="duplicate-run", run_dir=run_dir, recorder=Recorder()
    )
    collection.download(FakeBatchHost([(source, 1.0)]), remote_dir="/logs", glob="*")
    result = collection.extract_all()
    assert not result.success and "collision" in str(result.error_message)
    assert not (collection.expanded_dir / "duplicate.zip.expanded").exists()
    assert list_log_batches(tmp_path) == []


@pytest.mark.parametrize(
    ("member_type", "expected"),
    [(tarfile.SYMTYPE, "links are not allowed"), (tarfile.FIFOTYPE, "special files are not allowed")],
)
def test_log_extraction_rejects_tar_links_and_special_files(
    tmp_path: Path, member_type: bytes, expected: str
):
    source = tmp_path / "unsafe.tgz"
    member = tarfile.TarInfo("unsafe-entry")
    member.type = member_type
    if member_type == tarfile.SYMTYPE:
        member.linkname = "target.log"
    with tarfile.open(source, "w:gz") as archive:
        archive.addfile(member)
    run_id = f"tar-{member_type.hex()}"
    run_dir = tmp_path / "logs" / run_id
    run_dir.mkdir(parents=True)
    collection = LogCollection(run_id=run_id, run_dir=run_dir, recorder=Recorder())
    collection.download(FakeBatchHost([(source, 1.0)]), remote_dir="/logs", glob="*")
    result = collection.extract_all()
    assert not result.success and expected in str(result.error_message)


def test_gzip_expansion_enforces_limit_before_retaining_large_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "large.log.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(b"x" * 4096)
    run_dir = tmp_path / "logs" / "limit-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(run_id="limit-run", run_dir=run_dir, recorder=Recorder())
    collection.download(FakeBatchHost([(source, 1.0)]), remote_dir="/logs", glob="*")
    monkeypatch.setattr("autoenv.logs.MAX_EXPANDED_BYTES", source.stat().st_size + 100)
    result = collection.extract_all()
    assert not result.success and "expanded bytes exceed" in str(result.error_message)
    assert not (collection.expanded_dir / "large.log.gz.expanded").exists()


def test_time_query_with_date_crosses_midnight_and_requires_time(tmp_path: Path):
    source = tmp_path / "cpdt_midnight.log"
    source.write_text(
        "2026-08-15 23:50:00 [AUTH] previous day\n"
        "23:55:00 [AUTH] partial date\n"
        "2026-08-16 00:10:00 [AUTH] current day\n"
        "2026-08-17 00:05:00 [AUTH] next day too far\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "logs" / "midnight-run"
    run_dir.mkdir(parents=True)
    collection = LogCollection(
        run_id="midnight-run", run_dir=run_dir, recorder=Recorder()
    )
    collection.download(FakeBatchHost([(source, 1.0)]), remote_dir="/logs", glob="*")
    assert collection.extract_all().success
    group = collection.group(glob="cpdt*.log", timestamp=TIMESTAMP)
    assert group.match_line(r"\[AUTH\]", "auth.log").success
    assert collection.finalize().success
    result = query_log_records(
        tmp_path,
        "midnight-run",
        "auth.log",
        query_date="2026-08-16",
        query_time="00:10",
        window_minutes=60,
    )
    assert [row["text"] for row in result["records"]] == [
        "2026-08-15 23:50:00 [AUTH] previous day",
        "23:55:00 [AUTH] partial date",
        "2026-08-16 00:10:00 [AUTH] current day",
    ]
    with pytest.raises(ValueError, match="time is required"):
        query_log_records(
            tmp_path, "midnight-run", "auth.log", query_date="2026-08-16"
        )


def test_workflow_tool_runner_uses_run_context_and_writes_unified_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    observed: list[str] = []

    def body(ctx):
        observed.append(str(ctx.argument("value", required=True)))

    definition = WebToolDefinition(
        name="offline-workflow-test",
        title="Offline workflow",
        description="",
        fields=(),
        body=body,
        source=__file__,
        kind="workflow",
    )
    monkeypatch.setitem(web_tools._TOOLS, definition.name, definition)
    result = run_workflow_tool(
        tmp_path,
        definition.name,
        parameters={"arguments": {"value": "ok"}},
    )
    assert result.success and result.script_name == definition.name
    assert observed == ["ok"]
    assert (Path(result.run_dir) / "result.json").is_file()


def test_web_tool_scaffold_and_validator_support_workflow_kind(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    scaffold = root / ".agents/skills/autoenv-web-tool/scripts/scaffold_tool.py"
    validator = root / ".agents/skills/autoenv-web-tool/scripts/validate_tool.py"
    generated = subprocess.run(
        [
            sys.executable,
            str(scaffold),
            "sample-workflow",
            "--title",
            "Sample",
            "--kind",
            "workflow",
            "--renderer",
            "log_collection",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr
    target = tmp_path / "webPage/tools/sample_workflow.py"
    validated = subprocess.run(
        [sys.executable, str(validator), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert validated.returncode == 0 and validated.stdout.strip() == "OK"


def test_web_tool_template_is_valid_but_not_auto_discovered():
    root = Path(__file__).resolve().parents[1]
    template = root / "webPage/tools/_template.py"
    validator = root / ".agents/skills/autoenv-web-tool/scripts/validate_tool.py"

    validated = subprocess.run(
        [sys.executable, str(validator), str(template)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert validated.returncode == 0 and validated.stdout.strip() == "OK"
    source = template.read_text(encoding="utf-8")
    assert "@register_web_tool(" in source
    assert 'kind="local"' in source
    assert 'renderer="json"' in source
    assert {"text", "select", "checkbox"} <= {
        field_type for field_type in ("text", "select", "checkbox") if f'"type": "{field_type}"' in source
    }
    assert all(item["name"] != "replace-with-tool-name" for item in describe_tools(root))


def test_log_collection_is_a_workflow_tool_not_an_environment_script():
    root = Path(__file__).resolve().parents[1]
    tool = next(item for item in describe_tools(root) if item["name"] == "log-collection")
    assert tool["kind"] == "workflow"
    assert tool["renderer"] == "log_collection"
    assert tool["fields"] == []
    assert tool["resources"] == [{
        "name": "log_server",
        "alias": "日志服务器网口",
        "description": "通过 SCP 按脚本固化的路径和通配符收集日志。",
        "label": "1260网口",
        "protocol": "ssh",
    }]
    assert "log-collection" not in {item.name for item in list_scripts(root_dir=root)}
    assert "download_and_parse_logs" not in {item.name for item in list_scripts(root_dir=root)}
    source = (root / "webPage/tools/log_collection.py").read_text(encoding="utf-8")
    assert 'remote_dir="/var/log/product"' in source
    assert 'glob="cpdt_*"' in source
    assert 'ctx.argument("remote_dirs"' not in source
    assert "collection.download_sources(" in source
    assert "collection.source_groups(" in source
    with pytest.raises(ValueError, match="workflow"):
        run_web_tool(root, "log-collection", {})
