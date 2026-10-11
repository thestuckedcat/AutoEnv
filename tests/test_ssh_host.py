from __future__ import annotations

import hashlib
import io
import threading
import time
from pathlib import Path

import paramiko
import pytest

from autoenv.recorder import RunRecorder
from autoenv.results import CommandPhase, CommandStatus
from autoenv.selectors import extra_file, match, package
from autoenv.ssh_host import SSHConnectionInfo, SSHDefaults, SSHHost
from local_remote.ssh import CommandReply, LocalSSHServer


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


@pytest.fixture
def remote(tmp_path: Path):
    with LocalSSHServer(tmp_path / "remote") as server:
        yield server
    assert not server.errors, server.errors


@pytest.fixture
def rig(tmp_path: Path, remote: LocalSSHServer):
    package_dir = tmp_path / "run" / "packages"
    package_dir.mkdir(parents=True)
    console = io.StringIO()
    with RunRecorder(tmp_path / "run" / "run.log", console=console) as recorder:
        host = SSHHost(
            name="device", info=SSHConnectionInfo(
                host=remote.host, port=remote.port, username=remote.username,
                password=remote.password, connect_timeout=2,
            ), run_id="local-run", package_dir=package_dir, recorder=recorder,
            image_pattern_for=lambda name: rf"^{name}-\d+\.tgz$",
        )
        try:
            yield host, package_dir, recorder, console
        finally:
            host.close()


def test_defaults_are_normalized_and_connection_requires_identity() -> None:
    assert SSHDefaults().host == ""
    defaults = SSHDefaults(host=" host ", username=" user ", password=" preserved ", connect_timeout=3)
    assert (defaults.host, defaults.username, defaults.password, defaults.connect_timeout) == (
        "host", "user", " preserved ", 3.0,
    )
    assert SSHConnectionInfo(host=" host ", username=" user ").host == "host"


@pytest.mark.parametrize("factory,kwargs,error", [
    (SSHDefaults, {"host": 1}, TypeError),
    (SSHDefaults, {"username": 1}, TypeError),
    (SSHDefaults, {"password": None}, TypeError),
    (SSHDefaults, {"port": True}, TypeError),
    (SSHDefaults, {"port": 0}, ValueError),
    (SSHDefaults, {"port": 65536}, ValueError),
    (SSHDefaults, {"connect_timeout": True}, TypeError),
    (SSHDefaults, {"connect_timeout": 0}, ValueError),
    (SSHDefaults, {"connect_timeout": float("inf")}, ValueError),
    (SSHDefaults, {"connect_timeout": float("nan")}, ValueError),
    (SSHConnectionInfo, {"host": ""}, ValueError),
    (SSHConnectionInfo, {"host": "host", "username": " "}, ValueError),
])
def test_defaults_validation(factory, kwargs, error):
    with pytest.raises(error):
        factory(**kwargs)


def test_commands_arrive_once_over_one_lazy_real_ssh_connection(rig, remote):
    host, _, recorder, _ = rig
    assert not host.connected and remote.connections == 0
    remote.replies["first"] = CommandReply(stdout=(b"hello \xe4\xb8", b"\x96\xe7\x95\x8c\n"))
    remote.replies["second"] = CommandReply(stdout=(b"next",))
    first = host.execute("first")
    second = host.execute("second")
    assert first.success and first.stdout == "hello 世界\n"
    assert second.success and second.stdout == "next"
    assert remote.commands == ["first", "second"] and remote.connections == 1
    assert host.connected and isinstance(host._client, paramiko.SSHClient)
    assert recorder.final_operation_id == "0002"


@pytest.mark.parametrize("exit_code,expected", [(0, CommandStatus.SUCCESS), (7, CommandStatus.COMMAND_FAILED)])
def test_execute_stdout_stderr_and_nonzero_exit(rig, remote, exit_code, expected):
    host, _, _, console = rig
    remote.replies["inspect"] = CommandReply(stdout=(b"accepted\n",), stderr=(b"diagnostic\n",), exit_code=exit_code)
    result = host.execute("inspect")
    assert result.status is expected and result.phase is CommandPhase.COMPLETE
    assert result.stdout == "accepted\n" and result.stderr == "diagnostic\n"
    assert result.exit_code == exit_code
    assert result.error_type == ("NON_ZERO_EXIT_CODE" if exit_code else None)
    assert remote.commands == ["inspect"]
    assert "accepted" in console.getvalue() and "diagnostic" in console.getvalue()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_execute_on_output_sends_exact_raw_bytes_and_reuses_connection(rig, remote, stream):
    host, _, _, _ = rig
    chunks = (b"Continue", b"? now")
    remote.replies["installer"] = CommandReply(**{stream: chunks}, wait_for_input=True)
    response = b"y\n\x00\xff"
    result = host.execute_on_output("installer", keyword="Continue?", send_data=response)
    assert result.success and result.exit_code is None
    assert remote.wait_for_input("installer") == response
    assert host.execute("follow-up").success
    assert remote.connections == 1 and remote.commands == ["installer", "follow-up"]


def test_execute_on_output_reports_exit_before_keyword(rig, remote):
    host, _, _, _ = rig
    remote.replies["short"] = CommandReply(stdout=(b"ordinary output",))
    result = host.execute_on_output("short", keyword="never", send_data=b"response")
    assert result.status is CommandStatus.COMMAND_FAILED and result.error_type == "KEYWORD_NOT_FOUND"
    assert result.exit_code == 0 and remote.received_input == []


def test_execute_on_output_reports_peer_disconnect_during_response_send(rig, remote):
    host, _, _, _ = rig
    # Exceed the genuine SSH channel receive window. The peer emits the
    # trigger, never drains input, then disconnects while sendall is waiting.
    remote.replies["input-peer-disappears"] = CommandReply(
        stdout=(b"Continue?",), disconnect=True, chunk_delay=0.1,
    )
    result = host.execute_on_output(
        "input-peer-disappears", keyword="Continue?", send_data=b"x" * (4 * 1024 * 1024),
        timeout=2,
    )
    assert result.status is CommandStatus.PROTOCOL_ERROR
    assert result.phase is CommandPhase.SEND_COMMAND and result.error_type == "RESPONSE_SEND_FAILED"
    assert result.stdout == "Continue?" and remote.commands == ["input-peer-disappears"]


def test_execute_on_output_disconnects_before_keyword(rig, remote):
    host, _, _, _ = rig
    remote.replies["closed-before-prompt"] = CommandReply(stdout=(b"partial output",), disconnect=True)
    result = host.execute_on_output("closed-before-prompt", keyword="Continue?", send_data=b"y")
    assert result.status is CommandStatus.DISCONNECTED and result.phase is CommandPhase.WAIT_OUTPUT
    assert result.error_type == "CONNECTION_LOST" and result.exit_code is None
    assert result.stdout == "partial output" and remote.received_input == []


@pytest.mark.parametrize("kwargs,error", [
    ({"keyword": 1, "send_data": b"x"}, TypeError),
    ({"keyword": "", "send_data": b"x"}, ValueError),
    ({"keyword": "ready", "send_data": "x"}, TypeError),
    ({"keyword": "ready", "send_data": b""}, ValueError),
    ({"keyword": "ready", "send_data": b"x", "timeout": 0}, ValueError),
])
def test_execute_on_output_validates_before_connect(rig, remote, kwargs, error):
    with pytest.raises(error):
        rig[0].execute_on_output("command", **kwargs)
    assert remote.connections == 0


def test_timeout_preserves_output_without_replay(rig, remote):
    host, _, _, _ = rig
    remote.replies["apply-once"] = CommandReply(stdout=(b"partial",), exit_code=None)
    result = host.execute("apply-once", timeout=0.2)
    assert result.status is CommandStatus.TIMEOUT and result.error_type == "COMMAND_TIMEOUT"
    assert result.stdout == "partial" and remote.commands == ["apply-once"]
    assert host.execute("next").success and remote.commands.count("apply-once") == 1


def test_keyword_timeout_preserves_output(rig, remote):
    host, _, _, _ = rig
    remote.replies["waiting"] = CommandReply(stdout=(b"not ready",), exit_code=None)
    result = host.execute_on_output("waiting", keyword="Continue?", send_data=b"y", timeout=0.2)
    assert result.status is CommandStatus.TIMEOUT and result.error_type == "KEYWORD_NOT_FOUND"
    assert result.stdout == "not ready" and remote.received_input == []


def test_authentication_failure_uses_real_server_handshake(rig, remote):
    host, _, _, _ = rig
    host.info = SSHConnectionInfo(host=remote.host, port=remote.port, username=remote.username, password="wrong", connect_timeout=2)
    result = host.execute("not-delivered")
    assert result.status is CommandStatus.AUTH_FAILED and result.phase is CommandPhase.AUTHENTICATE
    assert result.error_type == "AUTHENTICATION_FAILED" and remote.commands == []
    assert remote.auth_attempts == [(remote.username, "wrong")]


def test_connection_refusal_is_reported_before_command_send(rig, remote):
    host, _, _, _ = rig
    remote.close()
    result = host.execute("not-delivered")
    assert result.status is CommandStatus.CONNECTION_FAILED
    assert result.phase is CommandPhase.CONNECT and result.exit_code is None
    assert remote.commands == []


def test_banner_handshake_timeout_prevents_command_delivery_and_closes_threads(tmp_path):
    with LocalSSHServer(tmp_path / "remote", banner_delay=0.3) as remote:
        package_dir = tmp_path / "run" / "packages"
        package_dir.mkdir(parents=True)
        with RunRecorder(tmp_path / "run" / "run.log", console=io.StringIO()) as recorder:
            with SSHHost(
                name="device", info=SSHConnectionInfo(
                    host=remote.host, port=remote.port, username=remote.username,
                    password=remote.password, connect_timeout=0.05,
                ), run_id="run", package_dir=package_dir, recorder=recorder,
                image_pattern_for=lambda _: ".*",
            ) as host:
                result = host.execute("not-delivered", timeout=1)
                assert result.phase is CommandPhase.CONNECT
                assert not result.success and result.exit_code is None
                assert remote.connections == 1 and remote.commands == []
                assert not host.connected
                # Paramiko 3 wraps its banner/negotiation deadline failure in
                # SSHException (often "No existing session"), so the public
                # result is a CONNECT-stage protocol error rather than the
                # socket.timeout classification tested by the old fake client.
                assert result.status is CommandStatus.PROTOCOL_ERROR
                assert result.error_type == "SSH_PROTOCOL_ERROR"
    assert all(not worker.is_alive() for worker in remote._threads)


@pytest.mark.parametrize("expect_disconnect,expected", [(False, CommandStatus.DISCONNECTED), (True, CommandStatus.SUCCESS)])
def test_real_disconnect_preserves_output_and_expectation(rig, remote, expect_disconnect, expected):
    host, _, _, _ = rig
    remote.replies["reboot"] = CommandReply(stdout=(b"started\n",), stderr=(b"rebooting\n",), disconnect=True)
    result = host.execute("reboot", expect_disconnect=expect_disconnect)
    assert result.status is expected and result.disconnected
    assert result.exit_code is None
    assert result.phase is (CommandPhase.COMPLETE if expect_disconnect else CommandPhase.WAIT_OUTPUT)
    assert result.stdout == "started\n" and result.stderr == "rebooting\n"
    assert result.expected_disconnect is expect_disconnect
    assert result.error_type == (None if expect_disconnect else "CONNECTION_LOST")
    assert not host.connected
    assert host.execute("health-check").success
    assert remote.commands == ["reboot", "health-check"] and remote.connections == 2


def test_inactive_connection_reconnects_without_replaying(rig, remote):
    host, _, _, _ = rig
    assert host.execute("old").success
    remote.disconnect_clients()
    deadline = time.monotonic() + 2
    while host.connected and time.monotonic() < deadline:
        time.sleep(0.01)
    assert host.execute("new").success
    assert remote.commands == ["old", "new"] and remote.connections == 2


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_binary_upload_uses_real_protocol_and_verifies_digest(rig, remote, protocol):
    host, package_dir, _, _ = rig
    payload = bytes(range(256)) * 1025
    (package_dir / "release.bin").write_bytes(payload)
    result = getattr(host, f"{protocol}_upload")(extra_file("release.bin"), r"\opt\apps\v1")
    assert result.success and result.md5_verified and result.md5_changed
    assert result.remote_dir == "/opt/apps/v1" and result.remote_file == "/opt/apps/v1/release.bin"
    assert result.local_md5 == result.remote_md5_after == md5(payload)
    assert result.remote_md5_before is None
    assert remote.path(result.remote_file).read_bytes() == payload
    assert (protocol, "upload", result.remote_file) in remote.transfers
    assert remote.sftp_sessions == (1 if protocol == "sftp" else 0)
    assert ("SFTP UPLOAD" if protocol == "sftp" else "SCP UPLOAD") in (package_dir.parent / "run.log").read_text(encoding="utf-8")


def test_scp_transfer_succeeds_with_sftp_disabled(tmp_path):
    with LocalSSHServer(tmp_path / "remote", sftp_enabled=False) as remote:
        package_dir = tmp_path / "run" / "packages"
        package_dir.mkdir(parents=True)
        (package_dir / "a.bin").write_bytes(b"payload\x00\xff")
        with RunRecorder(tmp_path / "run" / "run.log", console=io.StringIO()) as recorder:
            with SSHHost(name="device", info=SSHConnectionInfo(host=remote.host, port=remote.port, username=remote.username, password=remote.password, connect_timeout=2), run_id="run", package_dir=package_dir, recorder=recorder, image_pattern_for=lambda _: ".*") as host:
                assert host.scp_upload(extra_file("a.bin"), "/release").success
                (package_dir / "a.bin").unlink()
                assert host.scp_download("/release", remote_file="a.bin").success
                assert (package_dir / "a.bin").read_bytes() == b"payload\x00\xff"
        assert remote.sftp_sessions == 0 and not remote.errors


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
@pytest.mark.parametrize("payload", [b"", b"\x00\xff\x80quoted filename"])
def test_round_trip_empty_files_and_quoted_unicode_paths(rig, remote, protocol, payload):
    host, package_dir, _, _ = rig
    filename = "it's 中文 ;$.bin"
    local = package_dir / filename
    local.write_bytes(payload)
    uploaded = getattr(host, f"{protocol}_upload")(extra_file(filename), "/release with spaces")
    assert uploaded.success and uploaded.remote_md5_after == md5(payload)
    local.unlink()
    downloaded = getattr(host, f"{protocol}_download")("/release with spaces", remote_file=filename)
    assert downloaded.success and downloaded.local_md5 == md5(payload)
    assert local.read_bytes() == remote.path(uploaded.remote_file).read_bytes() == payload
    assert (protocol, "upload", uploaded.remote_file) in remote.transfers
    assert (protocol, "download", uploaded.remote_file) in remote.transfers


def test_arbitrary_shell_commands_are_only_recorded(rig, remote):
    host, _, _, _ = rig
    sentinel = remote.seed("/keep/sentinel", b"kept")
    command = "rm -rf /keep; reboot; exit 9"
    assert host.execute(command).success
    assert remote.commands == [command] and sentinel.read_bytes() == b"kept"


@pytest.mark.parametrize("selector,expected_type,expected_value,filename", [
    (package("api"), "package", "api", "api-24.tgz"),
    (match(r"\.cfg$"), "match", r"\.cfg$", "settings.cfg"),
])
def test_selectors_and_uploaded_filename_placeholder(rig, remote, selector, expected_type, expected_value, filename):
    host, package_dir, _, _ = rig
    (package_dir / "api-24.tgz").write_bytes(b"package")
    (package_dir / "settings.cfg").write_bytes(b"config")
    result = host.sftp_upload(selector, "/incoming")
    assert result.success and result.selector_type == expected_type and result.selector == expected_value
    assert result.resolved_local_file == str((package_dir / filename).resolve())
    assert host.execute(f"inspect /incoming/S{{{expected_value}}}").success
    assert remote.commands[-1] == f"inspect /incoming/{filename}"


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_overwrite_records_before_and_after_hashes(rig, remote, protocol):
    host, package_dir, _, _ = rig
    remote.seed("/releases/artifact.bin", b"old content")
    (package_dir / "artifact.bin").write_bytes(b"new content")
    result = getattr(host, f"{protocol}_upload")(extra_file("artifact.bin"), "/releases")
    assert result.success and result.remote_existed
    assert result.remote_md5_before == md5(b"old content")
    assert result.remote_md5_after == md5(b"new content") and result.md5_changed
    assert remote.path("/releases/artifact.bin").read_bytes() == b"new content"


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_overwrite_false_preserves_remote_file_without_transferring(rig, remote, protocol):
    host, package_dir, _, _ = rig
    remote.seed("/releases/artifact.bin", b"do not replace")
    (package_dir / "artifact.bin").write_bytes(b"replacement")
    result = getattr(host, f"{protocol}_upload")(extra_file("artifact.bin"), "/releases", overwrite=False)
    assert result.status == "remote_file_exists" and result.error_type == "REMOTE_FILE_EXISTS"
    assert result.remote_md5_before == md5(b"do not replace") and result.remote_md5_after is None
    assert remote.path("/releases/artifact.bin").read_bytes() == b"do not replace"
    assert not [entry for entry in remote.transfers if entry[:2] == (protocol, "upload")]


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_peer_storage_corruption_fails_hash_and_disables_placeholder(rig, remote, protocol):
    host, package_dir, _, _ = rig
    payload = b"correct payload"
    remote.upload_transforms[protocol] = lambda data: b"X" * len(data)
    (package_dir / "artifact.bin").write_bytes(payload)
    result = getattr(host, f"{protocol}_upload")(extra_file("artifact.bin"), "/release")
    assert result.status == "md5_verification_failed" and result.error_type == "MD5_VERIFICATION_FAILED"
    assert result.local_md5 == md5(payload) and result.remote_md5_after == md5(b"X" * len(payload))
    assert not result.md5_verified
    assert (protocol, "upload", "/release/artifact.bin") in remote.transfers
    with pytest.raises(ValueError, match="successfully uploaded file"):
        host.execute("inspect S{artifact.bin}")


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_missing_local_file_fails_before_connect(rig, remote, protocol):
    result = getattr(rig[0], f"{protocol}_upload")(extra_file("missing.bin"), "/release")
    assert not result.success and result.status == "local_file_not_found"
    assert remote.connections == 0


def test_local_digest_permission_failure_is_not_a_connection_failure(rig, remote, monkeypatch):
    host, package_dir, _, _ = rig
    artifact = package_dir / "artifact.bin"
    artifact.write_bytes(b"payload")
    original_open = Path.open

    def open_with_local_permission_failure(path, *args, **kwargs):
        if path == artifact:
            raise PermissionError("local artifact read denied")
        return original_open(path, *args, **kwargs)

    # Inject only the local filesystem fault before a connection is created.
    # Neither an SSH client nor any SCP/SFTP transfer method is replaced.
    monkeypatch.setattr(Path, "open", open_with_local_permission_failure)
    result = host.sftp_upload(extra_file("artifact.bin"), "/release")
    assert result.status == "local_file_read_failed" and result.error_type == "LOCAL_FILE_READ_FAILED"
    assert remote.connections == 0 and remote.transfers == []


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
@pytest.mark.parametrize("selection", ["exact", "pattern"])
def test_download_binary_bytes_size_hash_and_atomic_file(rig, remote, protocol, selection):
    host, package_dir, _, _ = rig
    payload = bytes(range(256)) * 1025
    remote.seed("/logs/device file.zip", payload)
    remote.seed("/logs/ignore.txt", b"ignored")
    kwargs = {"remote_file": "device file.zip"} if selection == "exact" else {"pattern": r"\.zip$"}
    result = getattr(host, f"{protocol}_download")("/logs", **kwargs)
    assert result.success and result.size_verified
    assert result.remote_file == "/logs/device file.zip" and result.local_md5 == md5(payload)
    assert result.remote_size == result.local_size == len(payload)
    assert Path(result.local_file).read_bytes() == payload
    assert not list(package_dir.glob("*.part"))
    assert (protocol, "download", "/logs/device file.zip") in remote.transfers


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_download_unique_match_missing_and_ambiguous(rig, remote, protocol):
    host, package_dir, _, _ = rig
    remote.seed("/logs/a.zip", b"a")
    remote.seed("/logs/b.zip", b"b")
    remote.path("/logs/directory.zip").mkdir()
    operation = getattr(host, f"{protocol}_download")
    ambiguous = operation("/logs", pattern=r"\.zip$")
    missing = operation("/logs", pattern=r"\.log$")
    exact_missing = operation("/logs", remote_file="missing.zip")
    assert (ambiguous.status, ambiguous.error_type) == ("ambiguous_remote_file", "AMBIGUOUS_REMOTE_FILE")
    for result in (missing, exact_missing):
        assert (result.status, result.error_type) == ("remote_file_not_found", "REMOTE_FILE_NOT_FOUND")
    assert not remote.transfers and list(package_dir.iterdir()) == []


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_download_overwrite_false_preserves_existing_local_file(rig, remote, protocol):
    host, package_dir, _, _ = rig
    remote.seed("/logs/a.zip", b"remote")
    (package_dir / "a.zip").write_bytes(b"keep local")
    result = getattr(host, f"{protocol}_download")("/logs", remote_file="a.zip", overwrite=False)
    assert result.status == "local_file_exists" and result.local_existed
    assert (package_dir / "a.zip").read_bytes() == b"keep local" and not remote.transfers


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_download_size_mismatch_cleans_pending_and_preserves_existing_file(rig, remote, protocol):
    host, package_dir, _, _ = rig
    remote.seed("/logs/a.zip", b"remote")
    remote.reported_sizes["/logs/a.zip"] = 999
    (package_dir / "a.zip").write_bytes(b"keep local")
    result = getattr(host, f"{protocol}_download")("/logs", remote_file="a.zip")
    assert result.status == "size_verification_failed"
    assert (package_dir / "a.zip").read_bytes() == b"keep local" and not list(package_dir.glob("*.part"))
    assert (protocol, "download", "/logs/a.zip") in remote.transfers


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_remote_permission_failure_is_real_protocol_response(rig, remote, protocol):
    host, package_dir, _, _ = rig
    remote.seed("/logs/a.zip", b"secret")
    remote.denied_reads.add("/logs/a.zip")
    result = getattr(host, f"{protocol}_download")("/logs", remote_file="a.zip")
    assert not result.success and result.status == "download_failed"
    assert list(package_dir.iterdir()) == []


@pytest.mark.parametrize("kwargs", [{}, {"remote_file": "a", "pattern": ".*"}, {"remote_file": "../a"}, {"pattern": ""}])
def test_download_validates_before_connect(rig, remote, kwargs):
    with pytest.raises(ValueError):
        rig[0].scp_download("/logs", **kwargs)
    assert remote.connections == 0


def test_batch_matches_all_nonrecursive_files_and_preserves_mtime(rig, remote):
    host, package_dir, _, _ = rig
    remote.seed("/logs/cpdt_b.log.gz", b"bb", mtime=20)
    remote.seed("/logs/cpdt_a.log", b"a", mtime=10)
    remote.seed("/logs/other.txt", b"other", mtime=30)
    remote.seed("/logs/nested/cpdt_c.log", b"excluded", mtime=1)
    destination = package_dir.parent / "log_collection" / "raw"
    result = host.scp_download_many("/logs", glob="cpdt_*", destination=destination)
    assert result.success and (result.matched_count, result.completed_count) == (2, 2)
    assert [item.name for item in result.files] == ["cpdt_a.log", "cpdt_b.log.gz"]
    assert [Path(item.local_file).read_bytes() for item in result.files] == [b"a", b"bb"]
    assert [int(Path(item.local_file).stat().st_mtime) for item in result.files] == [10, 20]
    assert all(item.remote_size is None for item in result.files)
    assert not any("wc -c" in command for command in remote.commands)
    assert remote.sftp_sessions == 0 and len(remote.transfers) == 2


def test_batch_four_real_scp_channels_run_concurrently_in_stable_order(rig, remote):
    host, package_dir, _, _ = rig
    for index in range(8):
        remote.seed(f"/logs/diag-{index:03d}.zip", bytes([index]) * 8192, mtime=index)
    remote.transfer_barrier = threading.Barrier(4)
    result = host.scp_download_many("/logs", glob="diag-*", destination=package_dir.parent / "batch")
    assert result.success and remote.peak_transfers == 4
    assert [item.name for item in result.files] == [f"diag-{index:03d}.zip" for index in range(8)]
    assert [Path(item.local_file).read_bytes() for item in result.files] == [bytes([index]) * 8192 for index in range(8)]
    assert remote.connections == 1 and len(remote.transfers) == 8


def test_batch_progress_is_visible_and_per_file_details_are_in_run_log(rig, remote):
    host, package_dir, _, console = rig
    for index in range(25):
        remote.seed(f"/logs/diag-{index:03d}.zip", f"zip-{index}".encode(), mtime=index)
    result = host.scp_download_many("/logs", glob="diag-*", destination=package_dir.parent / "batch")
    assert result.success and (result.matched_count, result.completed_count) == (25, 25)
    visible = console.getvalue()
    for value in (0, 10, 20, 25):
        assert f"completed={value} total=25" in visible
    assert "SCP BATCH START " in visible and "SCP BATCH MATCHED " in visible
    assert "diag-000.zip" not in visible and "SCP BATCH FILE " not in visible
    log = (package_dir.parent / "run.log").read_text(encoding="utf-8")
    assert log.count("SCP BATCH FILE ") == 50


def test_busybox_metadata_and_nul_name_listing_uses_actual_command_channel(rig, remote):
    host, _, _, console = rig
    remote.seed("/logs with spaces/cpdt one.log", b"one", mtime=1700000000)
    remote.seed("/logs with spaces/.hidden.log", b"hidden", mtime=1700000001)
    assert host._list_remote_file_metadata("/logs with spaces") == [
        (".hidden.log", 1700000001.0), ("cpdt one.log", 1700000000.0),
    ]
    command = remote.commands[0]
    assert "find " not in command and "stat -c %Y" in command and "wc -c" not in command
    assert "remote_dir='/logs with spaces'" in command
    # Windows cannot create filenames with a newline. The simulated shell may
    # emit them; the real SSH channel still transports the NUL-delimited bytes.
    list_command = host._busybox_list_command("/logs", metadata=False)
    remote.replies[list_command] = CommandReply(stdout=(b"normal.log\0line\nbreak.log\0",))
    assert host._list_remote_files("/logs", "scp") == ["normal.log", "line\nbreak.log"]
    assert console.getvalue() == ""


@pytest.mark.parametrize("payload", [b"unsafe/name\0", b"name\0not-a-time\0", b"name\0inf\0", b"odd-fields\0"])
def test_bad_remote_metadata_is_rejected_without_file_transfer(rig, remote, payload):
    host, package_dir, _, _ = rig
    command = host._busybox_list_command("/logs", metadata=True)
    remote.replies[command] = CommandReply(stdout=(payload,))
    destination = package_dir.parent / "batch"
    result = host.scp_download_many("/logs", glob="*", destination=destination)
    assert result.status == "remote_list_failed" and result.error_type == "REMOTE_LIST_FAILED"
    assert list(destination.iterdir()) == [] and remote.transfers == []


def test_batch_zero_matches_has_no_partial_files(rig, remote):
    host, package_dir, _, _ = rig
    remote.path("/logs").mkdir()
    destination = package_dir.parent / "batch"
    result = host.scp_download_many("/logs", glob="cpdt_*", destination=destination)
    assert result.status == "remote_file_not_found"
    assert list(destination.iterdir()) == [] and remote.transfers == []


def test_batch_failed_scp_stream_removes_completed_and_partial_files(rig, remote):
    host, package_dir, _, _ = rig
    remote.seed("/logs/cpdt_a.log", b"complete", mtime=10)
    remote.seed("/logs/cpdt_b.log", b"payload that gets truncated", mtime=20)
    remote.truncated_downloads.add("/logs/cpdt_b.log")
    destination = package_dir.parent / "batch"
    result = host.scp_download_many("/logs", glob="cpdt_*", destination=destination)
    assert result.status == "download_failed" and not result.success
    assert (result.matched_count, result.completed_count) == (2, 1)
    assert result.files == () and list(destination.iterdir()) == []
    assert len(remote.transfers) == 2
    log = (package_dir.parent / "run.log").read_text(encoding="utf-8")
    assert '"event": "failed"' in log and '"event": "complete"' in log


def test_batch_destination_must_be_empty_child_of_run(rig, remote, tmp_path):
    host, package_dir, _, _ = rig
    for destination in (tmp_path / "outside", package_dir.parent):
        with pytest.raises(ValueError):
            host.scp_download_many("/logs", glob="*", destination=destination)
    destination = package_dir.parent / "batch"
    destination.mkdir()
    (destination / "keep").write_bytes(b"existing")
    with pytest.raises(ValueError, match="empty"):
        host.scp_download_many("/logs", glob="*", destination=destination)
    assert remote.connections == 0


def test_close_closes_real_sftp_and_ssh_and_prevents_reconnect(rig, remote):
    host, package_dir, _, _ = rig
    (package_dir / "artifact.bin").write_bytes(b"payload")
    assert host.sftp_upload(extra_file("artifact.bin"), "/release").success
    assert host.scp_upload(extra_file("artifact.bin"), "/release").success
    transport, sftp = host._transport, host._sftp
    assert isinstance(sftp, paramiko.SFTPClient)
    host.close()
    host.close()
    assert not host.connected and not transport.is_active() and sftp.get_channel().closed
    result = host.execute("must not reconnect")
    assert result.status is CommandStatus.CONNECTION_FAILED and result.error_type == "SSH_HOST_CLOSED"
    assert remote.connections == 1 and "must not reconnect" not in remote.commands
