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
from autoenv.logs import LogCollection, TimestampPattern
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
    assert tool["resources"] == [{
        "name": "log_server",
        "alias": "日志服务器网口",
        "description": "通过 SCP 从当前目录批量收集 cpdt_* 日志。",
        "label": "1260网口",
        "protocol": "ssh",
    }]
    assert "log-collection" not in {item.name for item in list_scripts(root_dir=root)}
    assert "download_and_parse_logs" not in {item.name for item in list_scripts(root_dir=root)}
    with pytest.raises(ValueError, match="workflow"):
        run_web_tool(root, "log-collection", {})
