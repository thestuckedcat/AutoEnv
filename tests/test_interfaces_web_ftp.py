from __future__ import annotations

import hashlib
import io
import ftplib
import json
import socket
import subprocess
import sys
import threading
import time
import types
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from autoenv.ftp_host import FTPConnectionInfo, FTPHost
from autoenv.interface import LaunchRequest, bind_environments, merge_parameters
from autoenv.resources import (
    RESOURCE_LABELS_PATH,
    describe_resource_labels,
    load_resource_labels,
    validate_resource_label,
)
from autoenv.recorder import RunRecorder
from autoenv.results import RemoteDownloadResult
from autoenv.selectors import extra_file, match, package, resolve_local_file
from local_remote.ftp import LocalFTPServer
from autoenv.web_tools import describe_tools, run_web_tool


@pytest.fixture
def ftp_host_factory(tmp_path: Path):
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    recorder = RunRecorder(tmp_path / "ftp.log", console=io.StringIO())
    hosts: list[FTPHost] = []

    def make(remote: LocalFTPServer, **options):
        info = FTPConnectionInfo(
            remote.host, remote.port,
            username=options.pop("username", remote.username),
            password=options.pop("password", remote.password),
            timeout=0.5, **options,
        )
        host = FTPHost(
            name="ftp", info=info, run_id="local-ftp", package_dir=package_dir,
            recorder=recorder, image_pattern_for=lambda _: r"firmware.*\.bin",
        )
        hosts.append(host)
        return host, package_dir

    yield make
    for host in hosts:
        host.close()
    recorder.close()


def test_remote_download_result_is_directly_uploadable_selector(tmp_path: Path):
    package_dir=tmp_path/"packages";package_dir.mkdir();local=package_dir/"matched.zip";local.write_bytes(b"zip")
    from datetime import datetime, timezone
    now=datetime.now(timezone.utc)
    result=RemoteDownloadResult("run","1","sftp","dut","/logs",None,r".*zip",True,"success",True,now,now,0,"/logs/matched.zip",3,str(local),3,False,"abc",True)
    resolved=resolve_local_file(result,package_dir,lambda _:"")
    assert resolved.path==local.resolve() and resolved.selector_type=="remote_download"


@pytest.mark.parametrize("passive", [True, False])
def test_plain_ftp_uses_real_data_sockets_and_reuses_authenticated_control_session(
    tmp_path: Path, ftp_host_factory, passive: bool,
):
    payload = bytes(range(256)) * 4097 + b"\x00\xff\r\n"
    with LocalFTPServer(tmp_path / "remote") as remote:
        host, package_dir = ftp_host_factory(remote, passive=passive)
        (package_dir / "data.bin").write_bytes(payload)
        (package_dir / "empty.bin").write_bytes(b"")
        assert remote.sessions == 0

        result = host.upload(extra_file("data.bin"), "/incoming/nested")
        empty = host.upload(extra_file("empty.bin"), "/incoming/nested")

        assert result.success and result.protocol == "ftp" and result.size_verified
        assert result.remote_size == len(payload) and result.local_md5 == hashlib.md5(payload).hexdigest()
        assert remote.read_bytes("/incoming/nested/data.bin") == payload
        assert empty.success and empty.remote_size == 0
        assert remote.read_bytes("/incoming/nested/empty.bin") == b""
        assert remote.transfers == [("/incoming/nested/data.bin", payload), ("/incoming/nested/empty.bin", b"")]
        assert remote.data_modes == ["passive" if passive else "active"] * 2
        assert remote.sessions == 1
        assert remote.commands.count("USER user") == 1
        assert remote.commands.count("PASS password") == 1
        assert "MKD /incoming" in remote.commands and "MKD /incoming/nested" in remote.commands
        host.close()
        assert "QUIT" in remote.commands and not remote.errors


@pytest.mark.parametrize("selector", [extra_file("firmware-7.bin"), package("image"), match(r"firmware.*\.bin")])
@pytest.mark.parametrize("remote_dir, expected", [
    (".", "firmware-7.bin"),
    ("/", "/firmware-7.bin"),
    (r"relative\子目录\space dir", "relative/子目录/space dir/firmware-7.bin"),
])
def test_ftp_selectors_and_remote_directories_transfer_bytes(
    tmp_path: Path, ftp_host_factory, selector, remote_dir: str, expected: str,
):
    payload = b"image\x00\xff"
    with LocalFTPServer(tmp_path / "remote") as remote:
        host, package_dir = ftp_host_factory(remote)
        (package_dir / "firmware-7.bin").write_bytes(payload)
        result = host.upload(selector, remote_dir)
        assert result.success and result.remote_file == expected
        assert remote.read_bytes(expected) == payload
        assert remote.transfers == [(expected, payload)]


def test_ftp_overwrite_policy_queries_existing_file_and_preserves_bytes(tmp_path: Path, ftp_host_factory):
    with LocalFTPServer(tmp_path / "remote") as remote:
        destination = remote.root / "incoming" / "data.bin"
        destination.parent.mkdir()
        destination.write_bytes(b"old")
        host, package_dir = ftp_host_factory(remote)
        (package_dir / "data.bin").write_bytes(b"replacement")

        skipped = host.upload(extra_file("data.bin"), "/incoming", overwrite=False)
        assert not skipped.success and skipped.remote_existed
        assert skipped.status == "remote_file_exists" and skipped.error_type == "REMOTE_FILE_EXISTS"
        assert remote.transfers == [] and destination.read_bytes() == b"old"
        assert "SIZE /incoming/data.bin" in remote.commands

        replaced = host.upload(extra_file("data.bin"), "/incoming", overwrite=True)
        assert replaced.success and replaced.remote_existed
        assert destination.read_bytes() == b"replacement"
        assert remote.transfers == [("/incoming/data.bin", b"replacement")]


@pytest.mark.parametrize("credentials", [{"username": "wrong"}, {"password": "wrong"}])
def test_ftp_authentication_failure_crosses_real_control_socket(
    tmp_path: Path, ftp_host_factory, credentials,
):
    with LocalFTPServer(tmp_path / "remote") as remote:
        host, package_dir = ftp_host_factory(remote, **credentials)
        (package_dir / "data.bin").write_bytes(b"secret")
        result = host.upload(extra_file("data.bin"), "/incoming")
        assert not result.success and result.status == "ftp_rejected"
        assert result.error_type == "FTP_REJECTED" and "530" in result.error_message
        assert remote.sessions == 1 and remote.transfers == []
        assert not any(command.startswith("STOR ") for command in remote.commands)
        assert not list(remote.root.rglob("*.bin"))


def test_ftp_directory_rejection_does_not_open_data_connection(tmp_path: Path, ftp_host_factory):
    with LocalFTPServer(tmp_path / "remote") as remote:
        remote.rejected_directories.add("/denied")
        host, package_dir = ftp_host_factory(remote)
        (package_dir / "data.bin").write_bytes(b"data")
        result = host.upload(extra_file("data.bin"), "/denied")
        assert not result.success and result.status == "ftp_rejected"
        assert "553" in result.error_message
        assert "MKD /denied" in remote.commands and not remote.data_modes
        assert remote.transfers == []


def test_ftp_size_mismatch_preserves_real_transferred_file(tmp_path: Path, ftp_host_factory):
    with LocalFTPServer(tmp_path / "remote") as remote:
        remote.size_overrides["/incoming/data.bin"] = 999
        host, package_dir = ftp_host_factory(remote)
        (package_dir / "data.bin").write_bytes(b"intact bytes")
        result = host.upload(extra_file("data.bin"), "/incoming")
        assert not result.success and not result.size_verified
        assert result.status == "size_verification_failed"
        assert result.error_type == "SIZE_VERIFICATION_FAILED" and result.remote_size == 999
        assert remote.read_bytes("/incoming/data.bin") == b"intact bytes"
        assert remote.transfers == [("/incoming/data.bin", b"intact bytes")]


def test_ftp_missing_selector_and_closed_host_do_not_connect(tmp_path: Path, ftp_host_factory):
    with LocalFTPServer(tmp_path / "remote") as remote:
        host, package_dir = ftp_host_factory(remote)
        missing = host.upload(extra_file("missing.bin"), "/incoming")
        assert missing.status == "local_file_not_found" and not missing.success
        assert remote.sessions == 0
        (package_dir / "data.bin").write_bytes(b"data")
        host.close()
        host.close()
        closed = host.upload(extra_file("data.bin"), "/incoming")
        assert closed.status == "upload_failed" and closed.error_type == "RuntimeError"
        assert remote.sessions == 0 and remote.transfers == []


def test_ftp_connection_refused_is_a_real_socket_failure(tmp_path: Path, ftp_host_factory):
    with LocalFTPServer(tmp_path / "remote") as remote:
        host, package_dir = ftp_host_factory(remote)
        (package_dir / "data.bin").write_bytes(b"data")
    result = host.upload(extra_file("data.bin"), "/incoming")
    assert not result.success and result.status == "upload_failed"
    # Windows may exhaust the configured short timeout before reporting the
    # refusal. Both outcomes come from ftplib's real connection attempt.
    assert result.error_type in {"ConnectionRefusedError", "TimeoutError"}
    assert remote.sessions == 0 and not remote.transfers


@pytest.mark.parametrize("passive", [True, False])
def test_ftp_service_close_interrupts_unfinished_data_stream_and_joins_workers(
    tmp_path: Path, passive: bool,
):
    # Keep the real STOR stream open without EOF to exercise teardown while the
    # server owns an accepted data socket and an open destination file.
    with LocalFTPServer(tmp_path / "remote") as remote:
        client = ftplib.FTP()
        client.connect(remote.host, remote.port, timeout=1)
        client.login(remote.username, remote.password)
        client.set_pasv(passive)
        client.voidcmd("TYPE I")
        data = client.transfercmd("STOR held.bin")
        try:
            data.sendall(bytes(range(256)))
            assert remote.wait_for_data_connection()
            remote.close()
            assert remote.worker_threads
            assert all(not worker.is_alive() for worker in remote.worker_threads)
            assert not remote._connections and remote.transfers == []
            data.settimeout(1)
            try:
                assert data.recv(1) == b""
            except ConnectionResetError:
                pass
        finally:
            data.close()
            client.close()


def test_ftp_service_close_releases_unused_passive_listener(tmp_path: Path):
    with LocalFTPServer(tmp_path / "remote") as remote:
        client = ftplib.FTP()
        try:
            client.connect(remote.host, remote.port, timeout=1)
            client.login(remote.username, remote.password)
            address = client.makepasv()
            remote.close()
            assert not remote._connections
            assert all(not worker.is_alive() for worker in remote.worker_threads)
            with pytest.raises(OSError):
                socket.create_connection(address, timeout=0.1)
        finally:
            client.close()


def test_launch_request_and_environment_override_merge():
    request=LaunchRequest.from_dict({"script":"demo","environment":"lab","parameters":{"ssh_hosts":{"dut":{"host":"new"}}}})
    merged=merge_parameters({"ssh_hosts":{"dut":{"host":"old","port":22}}},request.parameters or {})
    assert merged["ssh_hosts"]=={"dut":{"host":"new","port":22}}
    with pytest.raises(ValueError): LaunchRequest.from_dict({"script":"demo","mode":"bad"})


def test_multi_environment_resource_bindings_are_independent_from_packages(tmp_path: Path):
    environments = tmp_path / "environments"
    environments.mkdir()
    (environments / "rack-a.json").write_text(
        '{"ssh_hosts":{"host":{"host":"192.0.2.10","port":22,"resource_label":"1260网口"}}}',
        encoding="utf-8",
    )
    (environments / "rack-b.json").write_text(
        '{"telnet_connections":{"console":{"host":"192.0.2.20","port":23,"resource_label":"1712串口"}}}',
        encoding="utf-8",
    )
    resources = (
        {"name": "primary", "label": "1260网口", "protocol": "ssh"},
        {"name": "secondary_console", "label": "1712串口", "protocol": "telnet"},
    )
    bound = bind_environments(
        tmp_path,
        {
            "primary": {"environment": "rack-a"},
            "secondary_console": {"environment": "rack-b"},
        },
        resources,
    )
    merged = merge_parameters(
        bound,
        {
            "packages": {
                "A1": {"path_override": "/hdfs/build/a1"},
                "A2": {"path_override": "/hdfs/build/a2"},
            }
        },
    )
    assert merged["ssh_hosts"]["primary"]["host"] == "192.0.2.10"
    assert merged["telnet_connections"]["secondary_console"]["host"] == "192.0.2.20"
    assert set(merged["packages"]) == {"A1", "A2"}


def test_resource_labels_are_loaded_from_json_and_protocol_checked():
    labels = {item["label"] for item in describe_resource_labels()}
    assert labels == {"1260网口", "1260串口", "1712网口", "1712串口", "udie1网口", "udie1串口"}
    assert RESOURCE_LABELS_PATH.name == "resource_labels.json"
    assert validate_resource_label("1260网口", protocol="ssh") == "1260网口"
    with pytest.raises(ValueError, match="not valid for telnet"):
        validate_resource_label("1260网口", protocol="telnet")


@pytest.mark.parametrize(
    ("catalog", "message"),
    [
        ({"schema_version": 2, "labels": [{"label": "dut", "kind": "network"}]}, "schema_version"),
        ({"schema_version": 1, "labels": [{"label": "dut", "kind": "other"}]}, "network or serial"),
        (
            {
                "schema_version": 1,
                "labels": [
                    {"label": "dut", "kind": "network"},
                    {"label": "dut", "kind": "serial"},
                ],
            },
            "duplicate label",
        ),
    ],
)
def test_resource_label_catalog_rejects_invalid_data(
    tmp_path: Path, catalog: dict[str, object], message: str
):
    path = tmp_path / "resource_labels.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_resource_labels(path)


def test_resource_label_api_returns_json_catalog():
    from webPage.server import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/api/resource-labels",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.load(response)
        assert status == 200
        assert len(payload["resource_labels"]) == 6
        assert {item["kind"] for item in payload["resource_labels"]} == {
            "network",
            "serial",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)










def test_agent_terminal_uses_pty_chunks_and_preserves_control_sequences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from webPage.server import TerminalSession

    class FakePty:
        def __init__(self):
            self.chunks = iter(["loading 1%\rloading 100%", "\x1b[2Jready", None])
            self.writes = []
            self.exitstatus = 0

        def read(self, _size):
            value = next(self.chunks)
            if value is None:
                raise EOFError
            return value

        def wait(self): return 0
        def isalive(self): return True
        def write(self, value): self.writes.append(value)
        def setwinsize(self, rows, cols): self.size = (rows, cols)
        def terminate(self, force=False): self.terminated = force

    process = FakePty()
    class FakeProcessFactory:
        @staticmethod
        def spawn(command, **kwargs):
            process.command = command
            process.options = kwargs
            return process

    monkeypatch.setitem(sys.modules, "winpty", types.SimpleNamespace(PtyProcess=FakeProcessFactory))
    session = TerminalSession()
    generation = session.start(["agent.exe"], cwd=tmp_path, rows=40, cols=120)
    for _ in range(100):
        with session.lock:
            if any(event["type"] == "complete" for event in session.events):
                break
        time.sleep(0.01)
    with session.lock:
        terminal_data = "".join(
            str(event["data"]) for event in session.events if event["type"] == "terminal"
        )
    assert terminal_data == "loading 1%\rloading 100%\x1b[2Jready"
    assert generation == 1
    assert process.options["dimensions"] == (40, 120)
    assert process.options["cwd"] == str(tmp_path)
    session.input("hello\r")
    assert process.writes == ["hello\r"]


def test_agent_startup_directory_must_be_an_existing_directory(tmp_path: Path):
    from webPage.server import _resolve_agent_cwd

    assert _resolve_agent_cwd(tmp_path) == tmp_path.resolve()
    with pytest.raises(ValueError, match="startup directory does not exist"):
        _resolve_agent_cwd(tmp_path / "missing")


def test_agent_command_is_typed_into_cmd_instead_of_prevalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from webPage.server import _start_agent_shell

    class FakeSession:
        def start(self, command, **options):
            self.command = command
            self.options = options
            return 7

        def input(self, value):
            self.value = value

    session = FakeSession()
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    generation = _start_agent_shell(
        session, "missing-agent --verbose", cwd=tmp_path, rows=24, cols=115
    )

    assert session.command == [r"C:\Windows\System32\cmd.exe", "/d"]
    assert session.options == {"cwd": tmp_path, "rows": 24, "cols": 115}
    assert session.value == "missing-agent --verbose\r"
    assert generation == 7


def test_empty_agent_command_opens_cmd_without_typing(tmp_path: Path):
    from webPage.server import _start_agent_shell

    class FakeSession:
        def start(self, command, **options):
            self.command = command
            return 8

        def input(self, _value):
            raise AssertionError("an empty startup command must not write terminal input")

    session = FakeSession()
    _start_agent_shell(session, "", cwd=tmp_path, rows=30, cols=100)
    assert session.command[-1] == "/d"






def test_scp_progress_lines_become_workflow_progress_bar_events():
    from webPage.server import _parse_scp_progress

    event = _parse_scp_progress(
        "[2026-08-17T12:00:00.000+00:00] "
        "SCP BATCH PROGRESS operation_id=0003 completed=40 total=300"
    )
    assert event == {
        "type": "progress",
        "operation_id": "0003",
        "completed": 40,
        "total": 300,
    }
    assert _parse_scp_progress("ordinary output") is None
    assert _parse_scp_progress(
        "[2026-08-17T12:00:00.000+00:00] "
        "SCP BATCH PROGRESS operation_id=0003 completed=301 total=300"
    ) is None
    assert _parse_scp_progress(
        "[2026-08-17T12:00:00.000+00:00] SSH host STDOUT "
        "SCP BATCH PROGRESS operation_id=0003 completed=1 total=2"
    ) is None



def test_web_has_one_launcher_fixed_endpoint_and_no_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    root = Path(__file__).resolve().parents[1]
    start_source = (root / "startWeb.py").read_text(encoding="utf-8")
    server_source = (root / "webPage" / "server.py").read_text(encoding="utf-8")

    assert not (root / "frontend").exists()
    assert "from webPage.server import WEB_URL, main" in start_source
    assert "len(sys.argv) != 1" in start_source
    assert "--host" not in server_source
    assert "--port" not in server_source
    assert "--no-browser" not in server_source
    assert 'if __name__ == "__main__"' not in server_source
    assert server_source.count("8765") == 1

    server_implementations = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        # Runtime snapshots and build outputs may contain complete older source
        # trees. They are evidence from previous runs, not active launchers.
        if relative.parts[0] in {".git", ".agents", ".test-deps", "build", "dist", "logs", "runtime_path", "tests"}:
            continue
        if "serve_forever(" in path.read_text(encoding="utf-8", errors="ignore"):
            server_implementations.append(relative.as_posix())
    assert server_implementations == ["webPage/server.py"]

    rejected = subprocess.run(
        [sys.executable, "-X", "utf8", str(root / "startWeb.py"), "--port", "9999"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert rejected.returncode == 2
    assert "one fixed startup endpoint" in rejected.stderr

    from webPage import server

    observed: dict[str, object] = {}

    class FakeServer:
        def __init__(self, address, handler):
            observed["address"] = address
            observed["handler"] = handler

        def serve_forever(self):
            observed["served"] = True

        def server_close(self):
            observed["closed"] = True

    class FakeTimer:
        def __init__(self, delay, callback):
            observed["timer_delay"] = delay
            observed["browser_callback"] = callback

        def start(self):
            observed["timer_started"] = True

    monkeypatch.setattr(server, "ExclusiveThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(server.threading, "Timer", FakeTimer)

    assert server.main() == 0
    assert observed["address"] == ("127.0.0.1", 8765)
    assert observed["served"] is True
    assert observed["closed"] is True
    assert observed["timer_started"] is True

    attempts: list[tuple[str, int]] = []

    def fail_to_bind(address, _handler):
        attempts.append(address)
        raise OSError("address already in use")

    monkeypatch.setattr(server, "ExclusiveThreadingHTTPServer", fail_to_bind)
    assert server.main() == 2
    assert attempts == [("127.0.0.1", 8765)]
    assert "fixed endpoint" in capsys.readouterr().err


def test_web_tool_discovery_and_execution():
    root=Path(__file__).resolve().parents[1]
    tools=describe_tools(root)
    assert any(item["name"]=="tool-contract-preview" for item in tools)
    result=run_web_tool(root,"tool-contract-preview",{"value":"0x1"})
    assert result["status"]=="contract_ready"


def test_import_skill_safe_extract_rejects_traversal(tmp_path: Path):
    archive=tmp_path/"bad.zip"
    with zipfile.ZipFile(archive,"w") as value: value.writestr("../bad.py","x=1")
    script=Path(__file__).resolve().parents[1]/".agents/skills/import-python-web-tool/scripts/safe_extract.py"
    import subprocess, sys
    completed=subprocess.run([sys.executable,str(script),str(archive),str(tmp_path/"out")],capture_output=True,text=True)
    assert completed.returncode != 0 and "unsafe archive path" in completed.stderr
