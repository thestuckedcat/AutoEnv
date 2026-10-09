from __future__ import annotations

import io
import socket
from pathlib import Path
from typing import Any

import pytest

from autoenv.command_files import UploadedFileRegistry
from autoenv.recorder import RunRecorder
from autoenv.results import CommandPhase, CommandStatus
from autoenv.telnet_client import (
    TelnetClient, TelnetConnectionInfo, TelnetDefaults, _TelnetByteFilter,
)
from local_remote.telnet import LocalTelnetServer, TelnetReply


@pytest.fixture
def client_factory(tmp_path: Path):
    recorder = RunRecorder(tmp_path / "run.log", console=io.StringIO())
    clients: list[TelnetClient] = []

    def make(remote: LocalTelnetServer, *, shell_mode="auto", timeout=0.5, **options):
        client = TelnetClient(
            name="console", info=TelnetConnectionInfo(
                remote.host, remote.port, timeout, shell_mode
            ), run_id="local-telnet", recorder=recorder, **options,
        )
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()
    recorder.close()


def test_defaults_normalize_values_and_allow_an_unspecified_host() -> None:
    assert TelnetDefaults() == TelnetDefaults(
        host="", port=23, timeout=30.0, shell_mode="auto"
    )
    assert TelnetDefaults(
        host="  host.example  ", port=2323, timeout=4, shell_mode=" POSIX "
    ) == TelnetDefaults(
        host="host.example", port=2323, timeout=4.0, shell_mode="posix"
    )


def test_connection_info_normalizes_values_and_requires_a_host() -> None:
    info = TelnetConnectionInfo(
        host="  192.0.2.10  ", port=2023, timeout=2, shell_mode=" PROMPT_ONLY "
    )

    assert info.host == "192.0.2.10"
    assert info.port == 2023
    assert info.timeout == 2.0
    assert info.shell_mode == "prompt_only"

    with pytest.raises(ValueError, match="must not be empty"):
        TelnetConnectionInfo(host=" \t ")


@pytest.mark.parametrize("model", [TelnetDefaults, TelnetConnectionInfo])
def test_defaults_and_connection_info_reject_non_string_hosts(model: type[Any]) -> None:
    with pytest.raises(TypeError, match="host must be a string"):
        model(host=123)


@pytest.mark.parametrize("port", [True, 23.0, "23", None])
def test_connection_info_rejects_non_integer_ports(port: object) -> None:
    with pytest.raises(TypeError, match="port must be an integer"):
        TelnetConnectionInfo(host="host", port=port)  # type: ignore[arg-type]


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_connection_info_rejects_out_of_range_ports(port: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 65535"):
        TelnetConnectionInfo(host="host", port=port)


@pytest.mark.parametrize("timeout", [True, "1", None])
def test_defaults_reject_non_numeric_timeouts(timeout: object) -> None:
    with pytest.raises(TypeError, match="timeout must be a number"):
        TelnetDefaults(timeout=timeout)  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout", [0, -0.1, float("inf"), float("-inf"), float("nan")])
def test_defaults_reject_non_positive_or_non_finite_timeouts(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite number greater than zero"):
        TelnetDefaults(timeout=timeout)


@pytest.mark.parametrize("shell_mode", [None, 1, True])
def test_connection_info_rejects_non_string_shell_modes(shell_mode: object) -> None:
    with pytest.raises(TypeError, match="shell_mode must be a string"):
        TelnetConnectionInfo(host="host", shell_mode=shell_mode)  # type: ignore[arg-type]


@pytest.mark.parametrize("shell_mode", ["", "ssh", "posix-ish"])
def test_connection_info_rejects_unknown_shell_modes(shell_mode: str) -> None:
    with pytest.raises(ValueError, match="auto, posix, prompt_only"):
        TelnetConnectionInfo(host="host", shell_mode=shell_mode)


def test_telnet_byte_filter_handles_fragmented_iac_and_rejects_options() -> None:
    telnet_filter = _TelnetByteFilter()
    iac = bytes([_TelnetByteFilter.IAC])

    assert telnet_filter.feed(b"hel" + iac) == (b"hel", b"")
    assert telnet_filter.feed(bytes([_TelnetByteFilter.WILL])) == (b"", b"")
    assert telnet_filter.feed(b"\x01lo") == (
        b"lo",
        bytes(
            [
                _TelnetByteFilter.IAC,
                _TelnetByteFilter.DONT,
                1,
            ]
        ),
    )
    assert telnet_filter.feed(
        iac + bytes([_TelnetByteFilter.DO, 3]) + b"!"
    ) == (
        b"!",
        bytes(
            [
                _TelnetByteFilter.IAC,
                _TelnetByteFilter.WONT,
                3,
            ]
        ),
    )
    assert telnet_filter.feed(
        iac
        + bytes([_TelnetByteFilter.DONT, 4])
        + iac
        + bytes([_TelnetByteFilter.WONT, 5])
    ) == (b"", b"")
    assert telnet_filter.feed(b"A" + iac + iac + b"B") == (b"A\xffB", b"")

    assert telnet_filter.feed(iac + bytes([_TelnetByteFilter.SB, 24]) + b"ignored") == (
        b"",
        b"",
    )
    assert telnet_filter.feed(
        iac + iac + b"still ignored" + iac + bytes([_TelnetByteFilter.SE]) + b"tail"
    ) == (b"tail", b"")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\r\n$ ", "$"),
        ("banner\r\nroot@box:~# ", "root@box:~#"),
        ("\x1b[32mrouter>\x1b[0m ", "router>"),
        ("output\r\n#root", "#root"),
        ("output\r\n[prompt]$", "[prompt]$"),
    ],
)
def test_prompt_recognition_accepts_common_prompts(text: str, expected: str) -> None:
    assert TelnetClient._find_prompt(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ordinary output",
        "login: ",
        "PASSWORD: ",
        "x" * 256 + "#",
    ],
)
def test_prompt_recognition_rejects_non_prompts(text: str) -> None:
    assert TelnetClient._find_prompt(text) is None


def test_auto_detects_posix_over_real_tcp_and_reuses_the_session(client_factory):
    with LocalTelnetServer(replies={
        "pwd": TelnetReply(output=b"/tmp", prompt="root@loopback:/tmp#"),
        "version": TelnetReply(output=b"1.2"),
    }, negotiation=(
        b"\xff", b"\xfb", b"\x01", b"\xff\xfd\x03",
        b"\xff\xfa\x18ignored", b"\xff", b"\xf0",
    )) as remote:
        client = client_factory(remote)
        assert not client.connected and remote.sessions == 0
        assert client.shell_mode == "auto"

        result = client.execute("pwd")
        following = client.execute("version")

        assert result.status is CommandStatus.SUCCESS
        assert result.exit_code == 0 and result.stdout == "/tmp"
        assert following.success and following.stdout == "1.2"
        assert client.shell_mode == "posix"
        assert client.prompt == "root@loopback:/tmp#"
        assert remote.commands == ["pwd", "version"]
        assert len(remote.probes) == 1 and remote.sessions == 1
        assert b"\xff\xfe\x01" in remote.negotiation_replies
        assert b"\xff\xfc\x03" in remote.negotiation_replies
        assert not remote.errors


@pytest.mark.parametrize("source", [None, "ssh_a"])
def test_uploaded_placeholder_is_resolved_before_command_reaches_the_peer(client_factory, source):
    uploaded = UploadedFileRegistry()
    uploaded.record("firmware", "/incoming/firmware-a.bin", target_name=source)
    if source is not None:
        uploaded.record("firmware", "/incoming/firmware-b.bin", target_name="ssh_b")
    with LocalTelnetServer() as remote:
        client = client_factory(remote, uploaded_files=uploaded, uploaded_files_from=source)
        result = client.execute("flash S{firmware}")
        assert result.success and result.command == "flash firmware-a.bin"
        assert remote.commands == ["flash firmware-a.bin"]


@pytest.mark.parametrize("shell_mode", ["auto", "prompt_only"])
def test_appliance_prompt_reports_unknown_exit_status_after_command_delivery(client_factory, shell_mode):
    with LocalTelnetServer(shell_mode="prompt_only", prompt="router>", replies={
        "show version": TelnetReply(output=b"Version 1.2"),
    }) as remote:
        client = client_factory(remote, shell_mode=shell_mode)
        result = client.execute("show version")
        assert result.status is CommandStatus.RESULT_UNKNOWN
        assert result.phase is CommandPhase.COMPLETE
        assert result.exit_code is None and result.stdout == "Version 1.2"
        assert result.error_type == "EXIT_STATUS_UNAVAILABLE"
        assert client.shell_mode == "prompt_only" and client.connected
        assert remote.commands == ["show version"]
        assert len(remote.probes) == (1 if shell_mode == "auto" else 0)


@pytest.mark.parametrize("exit_code, status", [
    (0, CommandStatus.SUCCESS), (7, CommandStatus.COMMAND_FAILED),
])
def test_posix_marker_exit_status_crosses_real_tcp(client_factory, exit_code, status):
    with LocalTelnetServer(replies={
        "do-work": TelnetReply(output=b"finished", exit_code=exit_code),
    }) as remote:
        result = client_factory(remote, shell_mode="posix").execute("do-work")
        assert result.status is status and result.phase is CommandPhase.COMPLETE
        assert result.exit_code == exit_code and result.stdout == "finished"
        assert result.error_type == ("NON_ZERO_EXIT_CODE" if exit_code else None)
        assert "__AUTOENV_RC_" in result.raw_output
        assert remote.commands == ["do-work"]
        assert remote.probes == []


@pytest.mark.parametrize("raw", [b"\x02", b"\x03\x00\x1b[B", b"yes\r\n", b"\xff\x00\xfe"])
def test_execute_on_output_delivers_unmodified_bytes_and_reconnects(client_factory, raw):
    with LocalTelnetServer(replies={
        "reboot": TelnetReply(output=(b"Press Ctrl", b"+B to enter menu"), expected_input=raw),
        "help": TelnetReply(output=b"commands"),
    }) as remote:
        client = client_factory(remote)
        triggered = client.execute_on_output("reboot", keyword="Press Ctrl+B", send_data=raw)
        assert triggered.success and triggered.phase is CommandPhase.COMPLETE
        assert triggered.stdout == "Press Ctrl+B to enter menu"
        assert remote.wait_for_raw(raw), remote.raw_inputs
        assert remote.raw_inputs == [raw] and not client.connected

        following = client.execute("help")
        assert following.success and following.stdout == "commands"
        assert remote.sessions == 2 and remote.commands == ["reboot", "help"]


def test_incremental_utf8_and_terminal_controls_are_read_from_real_socket(client_factory):
    text = "状态完成".encode("utf-8")
    with LocalTelnetServer(replies={
        "status": TelnetReply(output=(b"\x1b[32m" + text[:2], text[2:5], text[5:] + b"\x1b[0m\x00X\b")),
    }) as remote:
        result = client_factory(remote).execute("status")
        assert result.success and result.stdout == "状态完成"
        assert "\x1b[32m" in result.raw_output and "\x00" in result.raw_output
        assert remote.commands == ["status"]


def test_missing_posix_marker_reports_protocol_error_after_command_delivery(client_factory):
    with LocalTelnetServer(replies={"work": TelnetReply(output=b"done", omit_marker=True)}) as remote:
        result = client_factory(remote, shell_mode="posix").execute("work")
        assert result.status is CommandStatus.PROTOCOL_ERROR
        assert result.phase is CommandPhase.PARSE_RESULT
        assert result.error_type == "EXIT_MARKER_NOT_FOUND"
        assert result.stdout == "done" and remote.commands == ["work"]


def test_real_read_timeout_preserves_output_and_reconnects(client_factory):
    with LocalTelnetServer(replies={
        "long-command": TelnetReply(output=b"first line\r\npartial second line", complete=False),
    }) as remote:
        client = client_factory(remote)
        result = client.execute("long-command", timeout=0.08)
        assert result.status is CommandStatus.TIMEOUT
        assert result.phase is CommandPhase.WAIT_OUTPUT
        assert result.error_type == "COMMAND_TIMEOUT"
        assert result.stdout == "first line\npartial second line"
        assert result.duration_ms >= 70 and not client.connected
        following = client.execute("next-command")
        assert following.success and remote.sessions == 2
        assert remote.commands == ["long-command", "next-command"]


def test_keyword_timeout_never_sends_raw_response(client_factory):
    with LocalTelnetServer(replies={
        "reboot": TelnetReply(output=b"booting without marker", complete=False),
    }) as remote:
        result = client_factory(remote).execute_on_output(
            "reboot", keyword="Press Ctrl+B", send_data=b"\x02", timeout=0.08,
        )
        assert result.status is CommandStatus.TIMEOUT
        assert result.phase is CommandPhase.WAIT_OUTPUT
        assert result.error_type == "KEYWORD_NOT_FOUND"
        assert result.stdout == "booting without marker"
        assert remote.commands == ["reboot"] and remote.raw_inputs == []


@pytest.mark.parametrize("expected", [False, True])
def test_socket_disconnect_is_classified_and_the_next_command_reconnects(client_factory, expected):
    with LocalTelnetServer(replies={
        "reboot": TelnetReply(output=b"Connection closing\r\n", disconnect=True),
        "uptime": TelnetReply(output=b"up 1 minute"),
    }) as remote:
        client = client_factory(remote)
        result = client.execute("reboot", expect_disconnect=expected)
        assert result.status is (CommandStatus.SUCCESS if expected else CommandStatus.DISCONNECTED)
        assert result.disconnected and result.expected_disconnect is expected
        assert result.stdout == "Connection closing" and not client.connected
        assert result.error_type == (None if expected else "CONNECTION_LOST")
        assert client.execute("uptime").stdout == "up 1 minute"
        assert remote.commands == ["reboot", "uptime"] and remote.sessions == 2


def test_keyword_wait_reports_real_connection_loss(client_factory):
    with LocalTelnetServer(replies={
        "reboot": TelnetReply(output=b"booting", disconnect=True),
    }) as remote:
        result = client_factory(remote).execute_on_output("reboot", keyword="READY", send_data=b"x")
        assert result.status is CommandStatus.DISCONNECTED
        assert result.phase is CommandPhase.WAIT_OUTPUT
        assert result.error_type == "CONNECTION_LOST" and result.stdout == "booting"
        assert remote.commands == ["reboot"] and not remote.raw_inputs


def test_login_prompt_is_explicitly_unsupported_and_no_command_is_sent(client_factory):
    # The public Telnet contract has no username/password or login exchange.
    with LocalTelnetServer(login_required=True) as remote:
        result = client_factory(remote, timeout=0.08).execute("must-not-run")
        assert result.status is CommandStatus.PROTOCOL_ERROR
        assert result.phase is CommandPhase.DETECT_PROMPT
        assert result.error_type == "PROMPT_NOT_FOUND"
        assert "login:" in result.raw_output
        assert remote.commands == [] and remote.probes == []


def test_connection_refused_uses_actual_socket_failure(client_factory):
    with LocalTelnetServer() as remote:
        client = client_factory(remote, timeout=0.08)
    result = client.execute("never-delivered")
    assert result.status is CommandStatus.CONNECTION_FAILED
    assert result.phase is CommandPhase.CONNECT
    assert result.error_type == "CONNECTION_FAILED"
    assert remote.sessions == 0 and remote.commands == []


def test_close_is_idempotent_and_prevents_future_connections(client_factory):
    with LocalTelnetServer() as remote:
        client = client_factory(remote)
        with client:
            assert remote.sessions == 0
            assert client.execute("first").success
        client.close()
        result = client.execute("must-not-arrive")
        assert result.status is CommandStatus.CONNECTION_FAILED
        assert result.error_type == "TELNET_CLIENT_CLOSED"
        assert client.prompt is None and not client.connected
        assert remote.sessions == 1 and remote.commands == ["first"]


@pytest.mark.parametrize("kwargs, error", [
    ({"keyword": 1, "send_data": b"x"}, TypeError),
    ({"keyword": "", "send_data": b"x"}, ValueError),
    ({"keyword": "ready", "send_data": "x"}, TypeError),
    ({"keyword": "ready", "send_data": b""}, ValueError),
    ({"keyword": "ready", "send_data": b"x", "timeout": 0}, ValueError),
])
def test_invalid_interaction_is_rejected_before_connecting(client_factory, kwargs, error):
    with LocalTelnetServer() as remote:
        client = client_factory(remote)
        with pytest.raises(error):
            client.execute_on_output("command", **kwargs)
        assert remote.sessions == 0 and remote.commands == []


def test_service_close_interrupts_raw_input_wait_and_joins_workers():
    with LocalTelnetServer(replies={
        "interactive": TelnetReply(output=b"READY", expected_input=b"x"),
    }) as remote:
        with socket.create_connection((remote.host, remote.port), timeout=1) as peer:
            peer.sendall(b"\r\n")
            received = b""
            while remote.prompt.encode() not in received:
                received += peer.recv(4096)
            peer.sendall(b"interactive\r\n")
            received = b""
            while b"READY" not in received:
                received += peer.recv(4096)
            remote.close()
            assert remote.commands == ["interactive"] and remote.raw_inputs == []
            assert remote.worker_threads
            assert all(not worker.is_alive() for worker in remote.worker_threads)
            assert not remote._connections and not remote._thread.is_alive()
            assert peer.recv(1) == b""
