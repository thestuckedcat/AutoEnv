from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from autoenv.errors import AutoEnvOperationError
from autoenv.file_handles import FileHandle
from autoenv.remote_environment import FTPRemote, SSHRemote, TelnetRemote, create_remote
from autoenv.results import CommandStatus
from autoenv.script_session import ExecutionSession
from local_remote.ftp import LocalFTPServer
from local_remote.ssh import CommandReply, LocalSSHServer
from local_remote.telnet import LocalTelnetServer, TelnetReply


@pytest.fixture
def session(tmp_path):
    value = ExecutionSession(tmp_path / "project")
    value.runtime_path.mkdir()
    try:
        yield value
    finally:
        value.close()


def ssh_config(server, **extra):
    return {"name": "dut", "topology": "loopback", "type": "ssh", "host": server.host,
            "port": server.port, "username": server.username, "password": server.password,
            "connect_timeout": 0.8, **extra}


def telnet_config(server, **extra):
    return {"name": "console", "topology": "loopback", "type": "telnet", "host": server.host,
            "port": server.port, "timeout": 0.4, "shell_mode": "posix", **extra}


def ftp_config(server, **extra):
    return {"name": "ftp", "topology": "loopback", "type": "ftp", "host": server.host,
            "port": server.port, "username": server.username, "password": server.password,
            "timeout": 0.8, **extra}


@pytest.mark.parametrize("upload_type", ["scp", "sftp"])
def test_ssh_selfcheck_commands_branch_and_real_transfers(session, tmp_path, upload_type):
    with LocalSSHServer(tmp_path / "ssh") as server:
        remote = create_remote(session, ssh_config(server, upload_type=upload_type))
        assert isinstance(remote, SSHRemote) and remote.available and remote.transport_ready
        assert remote.upload_type == upload_type
        with pytest.raises(TypeError):
            remote.config["host"] = "must not change the run snapshot"
        with pytest.raises(AttributeError):
            remote.upload_type = "sftp" if upload_type == "scp" else "scp"
        server.replies["inspect"] = CommandReply(stdout=(b"READY\n",), stderr=(b"diagnostic\n",))
        server.replies["reject"] = CommandReply(stdout=(b"reason\n",), exit_code=7)
        assert remote.run("inspect") == "READY\n\ndiagnostic\n"
        failure = remote.execute("reject")
        assert failure.status is CommandStatus.COMMAND_FAILED and failure.exit_code == 7
        assert not failure.success and "reason" in failure.output
        with pytest.raises(AutoEnvOperationError) as caught:
            remote.run("reject")
        assert caught.value.result.exit_code == 7
        source = tmp_path / "firmware.bin"
        payload = bytes(range(256)) * 1025
        source.write_bytes(payload)
        assert remote.upload(FileHandle(source), "/incoming") is None
        assert server.path("/incoming/firmware.bin").read_bytes() == payload
        first = remote.download("/incoming", "firmware.bin")
        second = remote.download("/incoming", "firmware.bin")
        assert first.path != second.path and first.path.read_bytes() == payload
        assert second.path.read_bytes() == payload
        assert (upload_type, "upload", "/incoming/firmware.bin") in server.transfers
        assert (upload_type, "download", "/incoming/firmware.bin") in server.transfers
        if upload_type == "scp":
            assert server.sftp_sessions == 0
        else:
            assert server.sftp_sessions == 1
        log = session.log_path.read_text(encoding="utf-8")
        assert '"exit_code": 7' in log and '"command": "reject"' in log
        assert "test_remote_environment.py" in log and '"line":' in log
        assert "READY" in log and "diagnostic" in log
        remote.close()
    assert not server.errors


def test_ssh_missing_exit_is_distinct_from_nonzero(session, tmp_path):
    with LocalSSHServer(tmp_path / "ssh") as server:
        remote = create_remote(session, ssh_config(server))
        server.replies["drop"] = CommandReply(stdout=(b"accepted",), exit_code=None, disconnect=True)
        result = remote.execute("drop")
        assert result.status is CommandStatus.DISCONNECTED and result.exit_code is None
        assert result.stdout == "accepted" and not result.success


def test_disabled_sftp_capability_blocks_transfer_without_falling_back_to_scp(session, tmp_path):
    with LocalSSHServer(tmp_path / "ssh", sftp_enabled=False) as server:
        remote = create_remote(session, ssh_config(server, upload_type="sftp"))
        assert not remote.available and remote.transport_ready
        source = tmp_path / "data.bin"
        source.write_bytes(b"must not be transferred")
        with pytest.raises(AutoEnvOperationError):
            remote.upload(FileHandle(source), "/incoming")
        with pytest.raises(AutoEnvOperationError):
            remote.download("/incoming", "data.bin")
        assert not server.transfers and not any(command.startswith("scp ") for command in server.commands)


def test_retry_reconnects_selfchecks_without_replaying_commands(session, tmp_path):
    with LocalSSHServer(tmp_path / "ssh") as server:
        remote = create_remote(session, ssh_config(server))
        remote.run("upgrade")
        server.disconnect_clients()
        remote.retry(0, 2)
        assert remote.available and remote.transport_ready
        assert server.commands.count("upgrade") == 1 and server.connections >= 2
        remote.close()
        with pytest.raises(RuntimeError, match="permanently closed"):
            remote.retry(0, 1)


def test_unavailable_selfcheck_can_be_repaired_by_retry(session, tmp_path):
    with LocalSSHServer(tmp_path / "ssh") as server:
        remote = create_remote(session, ssh_config(server, password="future-password"))
        assert not remote.available and not remote.transport_ready
        with pytest.raises(AutoEnvOperationError) as caught:
            remote.execute("must not send")
        assert caught.value.result is remote.self_check_result
        assert "must not send" not in server.commands
        server.password = "future-password"
        remote.retry(0, 2)
        assert remote.available and remote.self_check_result.success


@pytest.mark.parametrize("passive", [True, False])
def test_ftp_upload_and_download_use_real_binary_data_channels(session, tmp_path, passive):
    with LocalFTPServer(tmp_path / "ftp") as server:
        remote = create_remote(session, ftp_config(server, passive=passive))
        assert isinstance(remote, FTPRemote) and remote.available and remote.transport_ready
        source = tmp_path / "payload.bin"
        payload = b"\x00\xff\r\n" * 40001
        source.write_bytes(payload)
        remote.upload(FileHandle(source), "/images")
        downloaded = remote.download("/images", "payload.bin")
        assert downloaded.path.read_bytes() == payload
        assert server.transfers == [("/images/payload.bin", payload)]
        assert server.downloads == [("/images/payload.bin", payload)]
        assert server.data_modes == ["passive" if passive else "active"] * 2
        assert "RETR /images/payload.bin" in server.commands and "PWD" in server.commands
        remote.retry(0, 1)
        assert remote.available and server.sessions == 2
        assert not hasattr(remote, "execute")
        remote.close()
    assert not server.errors


def test_ftp_download_failure_has_evidence_and_no_handler_or_partial_file(session, tmp_path):
    with LocalFTPServer(tmp_path / "ftp") as server:
        remote = create_remote(session, ftp_config(server))
        path = server._path("/bad.bin")
        path.write_bytes(b"download bytes")
        server.size_overrides["/bad.bin"] = 99
        with pytest.raises(AutoEnvOperationError) as caught:
            remote.download("/", "bad.bin")
        assert caught.value.result.status == "size_verification_failed"
        assert not list(session.runtime_path.rglob("*.part"))
        assert not list(session.runtime_path.rglob("bad.bin"))
        with pytest.raises(AutoEnvOperationError) as caught:
            remote.download("/", "missing.bin")
        assert caught.value.result.error_type == "FTP_REJECTED"


def test_telnet_posix_exit_status_and_keyword_branches(session):
    with LocalTelnetServer(replies={
        "inspect": TelnetReply(output=b"READY\r\n"),
        "reject": TelnetReply(output=b"FAILED\r\n", exit_code=9),
    }) as server:
        remote = create_remote(session, telnet_config(server))
        assert isinstance(remote, TelnetRemote) and remote.available
        good = remote.execute("inspect")
        assert good.success and good.exit_code == 0 and "READY" in good.output
        bad = remote.execute("reject")
        assert bad.status is CommandStatus.COMMAND_FAILED and bad.exit_code == 9
        with pytest.raises(AutoEnvOperationError):
            remote.run("reject")
        assert not hasattr(remote, "upload") and not hasattr(remote, "download")
        remote.retry(0, 1)
        assert remote.available and server.commands.count("inspect") == 1
        remote.close()
    assert not server.errors


def test_telnet_console_selfcheck_and_prompt_result_remains_unknown(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only", prompt="device>", replies={
        "check": TelnetReply(output=b"CHECK_OK\r\n"),
        "inspect": TelnetReply(output=b"READY\r\n"),
    }) as server:
        remote = create_remote(session, telnet_config(server, shell_mode="prompt_only", probe_command="check", probe_keyword="CHECK_OK"))
        assert remote.available and remote.transport_ready
        result = remote.execute("inspect")
        assert result.status is CommandStatus.RESULT_UNKNOWN and result.exit_code is None
        assert not result.success and "READY" in result.output
        with pytest.raises(AutoEnvOperationError) as caught:
            remote.run("inspect")
        assert caught.value.result.status is CommandStatus.RESULT_UNKNOWN
        assert server.commands == ["check", "inspect", "inspect"] and not server.probes


def test_telnet_missing_probe_does_not_send_bytes_but_allows_read_and_ctrl_b(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only", prompt="loader>", negotiation=(b"\xff\xfb\x01",)) as server:
        remote = create_remote(session, telnet_config(server, shell_mode="prompt_only"))
        assert not remote.available and remote.transport_ready
        initial = remote.read()
        assert "Local Telnet test peer" in initial and server.commands == [] and not server.probes
        remote.mark_output()
        server.push_output((b"boot \xe4\xb8", b"\xad\xe6\x96\x87 STOP", b"_BOOT\r\n"))
        output = remote.telnet_wait_exec("STOP_BOOT", b"\x02")
        assert "中文 STOP_BOOT" in output and server.wait_for_raw(b"\x02")
        assert server.raw_inputs == [b"\x02"]
        assert remote.transport_ready and not remote.available
        assert "bootloader>" in remote.read()
        assert server.negotiation_replies == [b"\xff\xfe\x01"]


def test_telnet_wait_reboot_is_passive_and_string_response_has_crlf(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only") as server:
        remote = create_remote(session, telnet_config(server, shell_mode="prompt_only"))
        remote.read()
        remote.mark_output()
        server.push_output(b"stale READY\r\n")
        assert "stale READY" in remote.read()
        remote.mark_output()
        producer = threading.Thread(target=lambda: (time.sleep(0.04), server.push_output((b"new RE", b"ADY\r\n"))))
        producer.start()
        try:
            assert "new READY" in remote.wait_reboot("READY")
        finally:
            producer.join()
        assert server.commands == []
        server.push_output(b"SEND_NEXT\r\n")
        assert "SEND_NEXT" in remote.telnet_wait_exec("SEND_NEXT", "enter")
        deadline = time.monotonic() + 0.3
        while "enter" not in server.commands and time.monotonic() < deadline:
            time.sleep(0.005)
        assert server.commands == ["enter"]


def test_telnet_wait_timeout_keeps_connection_and_partial_evidence(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only") as server:
        remote = create_remote(session, telnet_config(server, shell_mode="prompt_only", timeout=0.06))
        remote.read()
        server.push_output(b"partial boot\r\n")
        with pytest.raises(AutoEnvOperationError) as caught:
            remote.wait_reboot("never")
        assert caught.value.result.status is CommandStatus.TIMEOUT
        assert "partial boot" in caught.value.result.output
        assert remote.transport_ready and server.commands == []


def test_telnet_wait_preserves_same_packet_tail_and_raw_receive_evidence(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only") as server:
        remote = create_remote(session, telnet_config(server, shell_mode="prompt_only"))
        remote.read()
        remote.mark_output()
        packet = b"prefix \x1b[32mREA\x1b[0mDY\x00 TAIL_A\r\nTAIL_B\r\n"
        server.push_output(packet)
        assert remote.wait_reboot("READY") == "prefix READY"
        wait_result = session.operations[-1]["result"]
        assert wait_result["stdout"] == "prefix READY"
        assert "\x1b[32m" in wait_result["raw_output"]
        assert remote.wait_reboot("TAIL_A") == " TAIL_A"
        assert remote.read() == "\r\nTAIL_B\r\n"
        log = session.log_path.read_text(encoding="utf-8")
        assert "RAW RECEIVE hex=" + packet.hex() in log


def test_telnet_selfcheck_does_not_accept_only_echoed_keyword(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only") as server:
        remote = create_remote(session, telnet_config(server, shell_mode="prompt_only", probe_command="READY", probe_keyword="READY", timeout=0.04))
        assert not remote.available and remote.transport_ready


@pytest.mark.parametrize("shell_mode", ["posix", "auto", "prompt_only"])
def test_telnet_optional_login_uses_real_prompts_before_selfcheck(session, shell_mode):
    with LocalTelnetServer(passive=True, login_required=True, shell_mode="posix" if shell_mode != "prompt_only" else "prompt_only", replies={
        "check": TelnetReply(output=b"CHECK_OK\r\n"), "inspect": TelnetReply(output=b"READY\r\n"),
    }, login_prompt="Username:", password_prompt="Secret:") as server:
        remote = create_remote(session, telnet_config(
            server, shell_mode=shell_mode, username=server.username, password=server.password,
            login_prompt="Username:", password_prompt="Secret:", probe_command="check", probe_keyword="CHECK_OK",
        ))
        assert remote.available and server.login_attempts == [(server.username, server.password)]
        result = remote.execute("inspect")
        assert "READY" in result.output
        assert result.status is (CommandStatus.RESULT_UNKNOWN if shell_mode == "prompt_only" else CommandStatus.SUCCESS)
        assert server.password not in session.log_path.read_text(encoding="utf-8")


def test_telnet_wrong_password_is_unavailable_and_business_command_is_blocked(session):
    with LocalTelnetServer(passive=True, login_required=True) as server:
        remote = create_remote(session, telnet_config(server, username=server.username, password="bad"))
        assert not remote.available and remote.self_check_result.error_type == "AUTH_FAILED"
        with pytest.raises(AutoEnvOperationError):
            remote.execute("blocked")
        assert server.commands == [] and server.login_attempts == [(server.username, "bad")]


def test_changed_file_handle_rejects_before_network_transfer(session, tmp_path):
    with LocalFTPServer(tmp_path / "ftp") as server:
        remote = create_remote(session, ftp_config(server))
        source = tmp_path / "file.txt"
        source.write_text("before")
        handle = FileHandle(source)
        source.write_text("after")
        with pytest.raises(ValueError, match="changed"):
            remote.upload(handle, "/files")
        assert not server.transfers


@pytest.mark.parametrize("interval,max_try_times", [(True, 1), (-1, 1), (float("nan"), 1), (0, True), (0, 0)])
def test_retry_parameters_reject_invalid_values_without_reconnect(session, tmp_path, interval, max_try_times):
    with LocalFTPServer(tmp_path / "ftp") as server:
        remote = create_remote(session, ftp_config(server))
        with pytest.raises((TypeError, ValueError)):
            remote.retry(interval, max_try_times)
        assert server.sessions == 1
