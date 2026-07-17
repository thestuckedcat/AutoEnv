from __future__ import annotations

import socket
from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest

from autoenv import telnet_client as telnet_module
from autoenv.command_files import UploadedFileRegistry
from autoenv.results import CommandPhase, CommandStatus
from autoenv.telnet_client import (
    TelnetClient,
    TelnetConnectionInfo,
    TelnetDefaults,
    _TelnetByteFilter,
)


PROMPT = "root@box:~#"
PROBE_MARKER = "__AUTOENV_PROBE_fixed__"
RESULT_MARKER = "__AUTOENV_RC_fixed__"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSocket:
    def __init__(
        self,
        *recv_events: bytes | BaseException,
        send_error_at: int | None = None,
    ) -> None:
        self.recv_events = deque(recv_events)
        self.sent: list[bytes] = []
        self.send_error_at = send_error_at
        self.timeouts: list[float | None] = []
        self.close_calls = 0

    @property
    def closed(self) -> bool:
        return self.close_calls > 0

    def settimeout(self, value: float | None) -> None:
        self.timeouts.append(value)

    def sendall(self, data: bytes) -> None:
        if self.closed:
            raise OSError("socket is closed")
        if self.send_error_at == len(self.sent) + 1:
            raise OSError("scripted send failure")
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        assert size == 4096
        if not self.recv_events:
            raise socket.timeout("scripted receive timeout")
        event = self.recv_events.popleft()
        if isinstance(event, BaseException):
            raise event
        return event

    def close(self) -> None:
        self.close_calls += 1


class FakeSocketFactory:
    def __init__(self, *sockets: FakeSocket) -> None:
        self.sockets = deque(sockets)
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> FakeSocket:
        self.calls.append((args, kwargs))
        if not self.sockets:
            raise AssertionError("unexpected real/repeated connection attempt")
        return self.sockets.popleft()


class FakeRecorder:
    def __init__(self) -> None:
        self.operation = 0
        self.streamed: list[tuple[str, str]] = []
        self.recorded: list[tuple[str, Any]] = []

    def next_operation_id(self) -> str:
        self.operation += 1
        return f"{self.operation:04d}"

    def stream(self, prefix: str, text: str) -> None:
        self.streamed.append((prefix, text))

    def record_result(self, operation: str, result: Any) -> None:
        self.recorded.append((operation, result))


@pytest.fixture(autouse=True)
def deterministic_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        telnet_module.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )


def make_client(
    factory: FakeSocketFactory,
    *,
    shell_mode: str = "posix",
    clock: FakeClock | None = None,
    uploaded_files: UploadedFileRegistry | None = None,
    uploaded_files_from: str | None = None,
) -> tuple[TelnetClient, FakeRecorder, FakeClock]:
    fake_clock = clock or FakeClock()
    recorder = FakeRecorder()
    client = TelnetClient(
        name="dut",
        info=TelnetConnectionInfo(
            host="example.invalid",
            port=2323,
            timeout=1.0,
            shell_mode=shell_mode,
        ),
        run_id="run-1",
        recorder=recorder,  # type: ignore[arg-type]
        uploaded_files=uploaded_files,
        uploaded_files_from=uploaded_files_from,
        socket_factory=factory,
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )
    return client, recorder, fake_clock


def initial_prompt(prompt: str = PROMPT) -> bytes:
    return f"Welcome\r\n{prompt} ".encode()


def command_output(
    command: str,
    output: str,
    *,
    exit_code: int = 0,
    prompt: str = PROMPT,
) -> bytes:
    return (
        f"{command}\r\n{output}\r\n{RESULT_MARKER}:{exit_code}\r\n{prompt} "
    ).encode()


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


def test_auto_detects_posix_filters_iac_and_accepts_a_dynamic_prompt() -> None:
    sock = FakeSocket(
        b"\xff\xfb\x01Welcome\r\nroot@box:~# ",
        f"{PROBE_MARKER}:0\r\n{PROMPT} ".encode(),
        command_output("pwd", "/tmp", prompt="root@box:/tmp#"),
    )
    factory = FakeSocketFactory(sock)
    client, recorder, _ = make_client(factory, shell_mode="auto")

    assert client.shell_mode == "auto"
    result = client.execute("pwd")

    assert result.status is CommandStatus.SUCCESS
    assert result.exit_code == 0
    assert result.stdout == "/tmp"
    assert client.shell_mode == "posix"
    assert client.prompt == "root@box:/tmp#"
    assert bytes([255, 254, 1]) in sock.sent
    assert any(PROBE_MARKER.encode() in sent for sent in sock.sent)
    assert factory.calls == [((("example.invalid", 2323),), {"timeout": 1.0})]
    assert recorder.recorded == [("TELNET EXECUTE", result)]


def test_execute_resolves_uploaded_file_placeholder() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record("firmware", "/incoming/firmware-7.bin")
    sock = FakeSocket(
        initial_prompt(),
        command_output("flash firmware-7.bin", "done"),
    )
    client, _, _ = make_client(
        FakeSocketFactory(sock), uploaded_files=uploaded_files
    )

    result = client.execute("flash S{firmware}")

    assert result.success
    assert result.command == "flash firmware-7.bin"
    assert b"flash firmware-7.bin" in sock.sent[-1]


def test_execute_uses_the_explicit_ssh_upload_source() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record(
        "firmware", "/incoming/firmware-a.bin", target_name="host_a"
    )
    uploaded_files.record(
        "firmware", "/incoming/firmware-b.bin", target_name="host_b"
    )
    sock = FakeSocket(
        initial_prompt(),
        command_output("flash firmware-a.bin", "done"),
    )
    client, _, _ = make_client(
        FakeSocketFactory(sock),
        uploaded_files=uploaded_files,
        uploaded_files_from="host_a",
    )

    result = client.execute("flash S{firmware}")

    assert result.success
    assert result.command == "flash firmware-a.bin"


def test_auto_falls_back_to_prompt_only_and_returns_result_unknown() -> None:
    sock = FakeSocket(
        initial_prompt("router>"),
        b"printf: not found\r\nrouter> ",
        b"show version\r\nVersion 1.2\r\nrouter> ",
    )
    client, _, _ = make_client(FakeSocketFactory(sock), shell_mode="auto")

    result = client.execute("show version")

    assert client.shell_mode == "prompt_only"
    assert result.status is CommandStatus.RESULT_UNKNOWN
    assert result.phase is CommandPhase.COMPLETE
    assert result.exit_code is None
    assert result.stdout == "Version 1.2"
    assert result.error_type == "EXIT_STATUS_UNAVAILABLE"
    assert client.connected


@pytest.mark.parametrize(
    ("exit_code", "status", "error_type"),
    [
        (0, CommandStatus.SUCCESS, None),
        (7, CommandStatus.COMMAND_FAILED, "NON_ZERO_EXIT_CODE"),
    ],
)
def test_posix_reports_zero_and_nonzero_exit_codes(
    exit_code: int,
    status: CommandStatus,
    error_type: str | None,
) -> None:
    sock = FakeSocket(
        initial_prompt(),
        command_output("do-work", "finished", exit_code=exit_code),
    )
    client, recorder, _ = make_client(FakeSocketFactory(sock))

    result = client.execute("do-work")

    assert result.status is status
    assert result.phase is CommandPhase.COMPLETE
    assert result.exit_code == exit_code
    assert result.stdout == "finished"
    assert result.error_type == error_type
    if exit_code:
        assert result.error_message == f"remote command exited with code {exit_code}"
    else:
        assert result.error_message is None
    assert RESULT_MARKER.encode() in sock.sent[-1]
    assert recorder.streamed


def test_execute_on_output_matches_across_chunks_and_sends_ctrl_b() -> None:
    sock = FakeSocket(
        initial_prompt(),
        b"reboot\r\nPress Ctrl",
        b"+B to enter menu",
    )
    client, recorder, _ = make_client(
        FakeSocketFactory(sock), shell_mode="prompt_only"
    )

    result = client.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
        timeout=1,
    )

    assert result.status is CommandStatus.SUCCESS
    assert result.phase is CommandPhase.COMPLETE
    assert result.stdout == "Press Ctrl+B to enter menu"
    assert sock.sent == [b"\r\n", b"reboot\r\n", b"\x02"]
    assert sock.closed
    assert not client.connected
    assert recorder.recorded == [("TELNET EXECUTE", result)]


def test_execute_on_output_reconnects_before_the_next_telnet_command() -> None:
    first = FakeSocket(
        initial_prompt(),
        b"reboot\r\nPress Ctrl+B",
    )
    second = FakeSocket(
        initial_prompt("boot>"),
        b"help\r\ncommands\r\nboot> ",
    )
    factory = FakeSocketFactory(first, second)
    client, _, _ = make_client(factory, shell_mode="prompt_only")

    triggered = client.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
    )
    follow_up = client.execute("help")

    assert triggered.success
    assert follow_up.status is CommandStatus.RESULT_UNKNOWN
    assert follow_up.stdout == "commands"
    assert len(factory.calls) == 2
    assert first.closed
    assert client.connected


def test_execute_on_output_timeout_keeps_partial_output() -> None:
    clock = FakeClock()
    sock = FakeSocket(
        initial_prompt(),
        b"reboot\r\nbooting without requested marker",
    )
    client, _, _ = make_client(
        FakeSocketFactory(sock), shell_mode="prompt_only", clock=clock
    )

    result = client.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
        timeout=0.025,
    )

    assert result.status is CommandStatus.TIMEOUT
    assert result.phase is CommandPhase.WAIT_OUTPUT
    assert result.error_type == "KEYWORD_NOT_FOUND"
    assert result.stdout == "booting without requested marker"
    assert result.duration_ms == 25
    assert sock.sent == [b"\r\n", b"reboot\r\n"]
    assert sock.closed


def test_execute_on_output_reports_response_send_failure() -> None:
    sock = FakeSocket(
        initial_prompt(),
        b"reboot\r\nPress Ctrl+B",
        send_error_at=3,
    )
    client, _, _ = make_client(
        FakeSocketFactory(sock), shell_mode="prompt_only"
    )

    result = client.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
    )

    assert result.status is CommandStatus.PROTOCOL_ERROR
    assert result.phase is CommandPhase.SEND_COMMAND
    assert result.error_type == "RESPONSE_SEND_FAILED"
    assert "scripted send failure" in (result.error_message or "")
    assert sock.closed


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"keyword": 1, "send_data": b"x"}, TypeError),
        ({"keyword": "", "send_data": b"x"}, ValueError),
        ({"keyword": "ready", "send_data": "x"}, TypeError),
        ({"keyword": "ready", "send_data": b""}, ValueError),
        ({"keyword": "ready", "send_data": b"x", "timeout": 0}, ValueError),
    ],
)
def test_execute_on_output_validates_trigger_inputs(
    kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    factory = FakeSocketFactory()
    client, _, _ = make_client(factory, shell_mode="prompt_only")

    with pytest.raises(error):
        client.execute_on_output("command", **kwargs)  # type: ignore[arg-type]

    assert factory.calls == []


def test_timeout_preserves_partial_output_and_drops_the_connection() -> None:
    clock = FakeClock()
    sock = FakeSocket(
        initial_prompt(),
        b"long-command\r\nfirst line\r\npartial second line",
    )
    client, _, _ = make_client(FakeSocketFactory(sock), clock=clock)

    result = client.execute("long-command", timeout=0.025)

    assert result.status is CommandStatus.TIMEOUT
    assert result.phase is CommandPhase.WAIT_OUTPUT
    assert result.error_type == "COMMAND_TIMEOUT"
    assert result.stdout == "first line\npartial second line"
    assert "partial second line" in result.raw_output
    assert result.duration_ms == 25
    assert clock.sleeps
    assert sock.closed
    assert not client.connected


def test_unexpected_disconnect_reports_partial_output() -> None:
    sock = FakeSocket(
        initial_prompt(),
        b"collect\r\nline before disconnect\r\n",
        b"",
    )
    client, _, _ = make_client(FakeSocketFactory(sock))

    result = client.execute("collect")

    assert result.status is CommandStatus.DISCONNECTED
    assert result.phase is CommandPhase.WAIT_OUTPUT
    assert result.error_type == "CONNECTION_LOST"
    assert result.stdout == "line before disconnect"
    assert result.disconnected is True
    assert result.expected_disconnect is False
    assert sock.closed
    assert not client.connected


def test_expected_disconnect_is_success_and_the_next_command_reconnects() -> None:
    first = FakeSocket(
        initial_prompt(),
        b"reboot\r\nConnection closing\r\n",
        b"",
    )
    second = FakeSocket(
        initial_prompt(),
        command_output("uptime", "up 1 minute"),
    )
    factory = FakeSocketFactory(first, second)
    client, _, _ = make_client(factory)

    reboot = client.execute("reboot", expect_disconnect=True)
    uptime = client.execute("uptime")

    assert reboot.status is CommandStatus.SUCCESS
    assert reboot.phase is CommandPhase.COMPLETE
    assert reboot.stdout == "Connection closing"
    assert reboot.expected_disconnect is True
    assert reboot.disconnected is True
    assert uptime.status is CommandStatus.SUCCESS
    assert uptime.stdout == "up 1 minute"
    assert uptime.operation_id == "0002"
    assert len(factory.calls) == 2
    assert first.closed
    assert client.connected


def test_close_is_idempotent_and_prevents_future_connections() -> None:
    sock = FakeSocket(
        initial_prompt(),
        command_output("true", ""),
    )
    factory = FakeSocketFactory(sock)
    client, recorder, _ = make_client(factory)

    successful = client.execute("true")
    client.close()
    client.close()
    after_close = client.execute("echo never sent")

    assert successful.status is CommandStatus.SUCCESS
    assert sock.close_calls == 1
    assert not client.connected
    assert client.prompt is None
    assert after_close.status is CommandStatus.CONNECTION_FAILED
    assert after_close.phase is CommandPhase.CONNECT
    assert after_close.error_type == "TELNET_CLIENT_CLOSED"
    assert len(factory.calls) == 1
    assert [result.operation_id for _, result in recorder.recorded] == ["0001", "0002"]
