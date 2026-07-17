from __future__ import annotations

import codecs
import math
import re
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from typing import Callable, Protocol

from .command_files import UploadedFileRegistry
from .recorder import RunRecorder
from .results import CommandPhase, CommandProtocol, CommandResult, CommandStatus


_SHELL_MODES = frozenset({"auto", "posix", "prompt_only"})
_ANSI_RE = re.compile(
    r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-_])"
)


@dataclass(frozen=True)
class TelnetDefaults:
    host: str = ""
    port: int = 23
    timeout: float = 30.0
    shell_mode: str = "auto"

    def __post_init__(self) -> None:
        if not isinstance(self.host, str):
            raise TypeError("Telnet host must be a string")
        object.__setattr__(self, "host", self.host.strip())
        object.__setattr__(self, "port", _validated_port(self.port))
        object.__setattr__(self, "timeout", _validated_timeout(self.timeout))
        object.__setattr__(self, "shell_mode", _validated_shell_mode(self.shell_mode))


@dataclass(frozen=True)
class TelnetConnectionInfo:
    host: str
    port: int = 23
    timeout: float = 30.0
    shell_mode: str = "auto"

    def __post_init__(self) -> None:
        if not isinstance(self.host, str):
            raise TypeError("Telnet host must be a string")
        host = self.host.strip()
        if not host:
            raise ValueError("Telnet host must not be empty")

        object.__setattr__(self, "host", host)
        object.__setattr__(self, "port", _validated_port(self.port))
        object.__setattr__(self, "timeout", _validated_timeout(self.timeout))
        object.__setattr__(self, "shell_mode", _validated_shell_mode(self.shell_mode))


def _validated_port(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Telnet port must be an integer")
    if not 1 <= value <= 65535:
        raise ValueError("Telnet port must be between 1 and 65535")
    return value


def _validated_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Telnet timeout must be a number")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Telnet timeout must be a finite number greater than zero")
    return timeout


def _validated_shell_mode(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Telnet shell_mode must be a string")
    shell_mode = value.strip().lower()
    if shell_mode not in _SHELL_MODES:
        choices = ", ".join(sorted(_SHELL_MODES))
        raise ValueError(f"Telnet shell_mode must be one of: {choices}")
    return shell_mode


class _SocketLike(Protocol):
    def settimeout(self, value: float | None) -> None: ...

    def sendall(self, data: bytes) -> None: ...

    def recv(self, size: int) -> bytes: ...

    def close(self) -> None: ...


SocketFactory = Callable[..., _SocketLike]


class _ReadTimedOut(Exception):
    def __init__(self, partial: str) -> None:
        super().__init__("read timed out")
        self.partial = partial


class _ConnectionClosed(Exception):
    def __init__(self, partial: str, cause: BaseException | None = None) -> None:
        super().__init__("connection closed")
        self.partial = partial
        self.cause = cause


class _PromptNotFound(Exception):
    def __init__(self, partial: str) -> None:
        super().__init__("Telnet prompt was not found")
        self.partial = partial


class _TelnetByteFilter:
    """Remove Telnet commands from application data and reject all options."""

    IAC = 255
    DONT = 254
    DO = 253
    WONT = 252
    WILL = 251
    SB = 250
    SE = 240

    def __init__(self) -> None:
        self._state = "data"
        self._negotiation_command: int | None = None

    def feed(self, chunk: bytes) -> tuple[bytes, bytes]:
        data = bytearray()
        replies = bytearray()

        for value in chunk:
            if self._state == "data":
                if value == self.IAC:
                    self._state = "iac"
                else:
                    data.append(value)
                continue

            if self._state == "iac":
                if value == self.IAC:
                    data.append(value)
                    self._state = "data"
                elif value in (self.DO, self.DONT, self.WILL, self.WONT):
                    self._negotiation_command = value
                    self._state = "option"
                elif value == self.SB:
                    self._state = "subnegotiation"
                else:
                    # NOP, GA, SE and other one-byte Telnet commands are not text.
                    self._state = "data"
                continue

            if self._state == "option":
                if self._negotiation_command == self.DO:
                    replies.extend((self.IAC, self.WONT, value))
                elif self._negotiation_command == self.WILL:
                    replies.extend((self.IAC, self.DONT, value))
                self._negotiation_command = None
                self._state = "data"
                continue

            if self._state == "subnegotiation":
                if value == self.IAC:
                    self._state = "subnegotiation_iac"
                continue

            # Inside SB, IAC SE terminates the block. IAC IAC is escaped data in
            # that block, but the entire subnegotiation is deliberately isolated.
            if value == self.SE:
                self._state = "data"
            elif value != self.IAC:
                self._state = "subnegotiation"

        return bytes(data), bytes(replies)


class TelnetClient:
    """A lazy, reusable Telnet command session with prompt-based framing."""

    def __init__(
        self,
        *,
        name: str,
        info: TelnetConnectionInfo,
        run_id: str,
        recorder: RunRecorder,
        uploaded_files: UploadedFileRegistry | None = None,
        uploaded_files_from: str | None = None,
        socket_factory: SocketFactory = socket.create_connection,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Telnet client name must not be empty")
        if not isinstance(info, TelnetConnectionInfo):
            raise TypeError("info must be TelnetConnectionInfo")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must not be empty")
        if not callable(socket_factory):
            raise TypeError("socket_factory must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(sleep):
            raise TypeError("sleep must be callable")

        self.name = name.strip()
        self.info = info
        self.run_id = run_id.strip()
        self.recorder = recorder
        if uploaded_files is not None and not isinstance(uploaded_files, UploadedFileRegistry):
            raise TypeError("uploaded_files must be UploadedFileRegistry")
        self.uploaded_files = uploaded_files or UploadedFileRegistry()
        if uploaded_files_from is not None:
            if not isinstance(uploaded_files_from, str) or not uploaded_files_from.strip():
                raise ValueError("uploaded_files_from must be a non-empty SSH host name")
            uploaded_files_from = uploaded_files_from.strip()
        self.uploaded_files_from = uploaded_files_from
        self._socket_factory = socket_factory
        self._clock = clock
        self._sleep = sleep

        self._sock: _SocketLike | None = None
        self._prompt: str | None = None
        self._active_shell_mode: str | None = None
        self._telnet_filter = _TelnetByteFilter()
        self._lock = RLock()
        self._closed = False

    @property
    def connected(self) -> bool:
        return self._sock is not None

    @property
    def prompt(self) -> str | None:
        return self._prompt

    @property
    def shell_mode(self) -> str:
        return self._active_shell_mode or self.info.shell_mode

    def __enter__(self) -> "TelnetClient":
        # Construction and entering a context are both intentionally lazy.
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._drop_connection()

    def execute(
        self,
        command: str,
        timeout: float | None = None,
        expect_disconnect: bool = False,
    ) -> CommandResult:
        command = self.uploaded_files.resolve(
            command,
            target_name=self.uploaded_files_from,
        )
        if timeout is None:
            command_timeout = self.info.timeout
        else:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a number")
            command_timeout = float(timeout)
            if not math.isfinite(command_timeout) or command_timeout <= 0:
                raise ValueError("timeout must be a finite number greater than zero")
        if not isinstance(expect_disconnect, bool):
            raise TypeError("expect_disconnect must be a boolean")

        with self._lock:
            return self._execute_locked(command, command_timeout, expect_disconnect)

    def execute_on_output(
        self,
        command: str,
        *,
        keyword: str,
        send_data: bytes,
        timeout: float | None = None,
    ) -> CommandResult:
        """Run a command, then block until output matches and send raw bytes."""
        command = self.uploaded_files.resolve(
            command,
            target_name=self.uploaded_files_from,
        )
        if not isinstance(keyword, str):
            raise TypeError("keyword must be a string")
        if not keyword:
            raise ValueError("keyword must not be empty")
        if not isinstance(send_data, bytes):
            raise TypeError("send_data must be bytes")
        if not send_data:
            raise ValueError("send_data must not be empty")
        if timeout is None:
            command_timeout = self.info.timeout
        else:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a number")
            command_timeout = float(timeout)
            if not math.isfinite(command_timeout) or command_timeout <= 0:
                raise ValueError("timeout must be a finite number greater than zero")

        with self._lock:
            return self._execute_locked(
                command,
                command_timeout,
                False,
                keyword=keyword,
                send_data=send_data,
            )

    def _execute_locked(
        self,
        command: str,
        timeout: float,
        expect_disconnect: bool,
        *,
        keyword: str | None = None,
        send_data: bytes | None = None,
    ) -> CommandResult:
        operation_id = self.recorder.next_operation_id()
        started_at = datetime.now().astimezone()
        started_clock = self._clock()
        raw_output = ""

        if self._closed:
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.CONNECTION_FAILED,
                phase=CommandPhase.CONNECT,
                started_at=started_at,
                started_clock=started_clock,
                error_type="TELNET_CLIENT_CLOSED",
                error_message=f"Telnet client {self.name!r} is closed",
                expected_disconnect=expect_disconnect,
            )

        try:
            if self._sock is None:
                raw_output = self._open_session(self.info.timeout)
        except OSError as exc:
            self._drop_connection()
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.CONNECTION_FAILED,
                phase=CommandPhase.CONNECT,
                started_at=started_at,
                started_clock=started_clock,
                raw_output=raw_output,
                error_type="CONNECTION_FAILED",
                error_message=str(exc) or exc.__class__.__name__,
                expected_disconnect=expect_disconnect,
            )
        except _PromptNotFound as exc:
            self._drop_connection()
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.PROTOCOL_ERROR,
                phase=CommandPhase.DETECT_PROMPT,
                started_at=started_at,
                started_clock=started_clock,
                stdout=self._clean_terminal_text(exc.partial),
                raw_output=exc.partial,
                error_type="PROMPT_NOT_FOUND",
                error_message="could not identify a Telnet command prompt",
                expected_disconnect=expect_disconnect,
            )
        except _ReadTimedOut as exc:
            self._drop_connection()
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.TIMEOUT,
                phase=CommandPhase.DETECT_PROMPT,
                started_at=started_at,
                started_clock=started_clock,
                stdout=self._clean_terminal_text(exc.partial),
                raw_output=exc.partial,
                error_type="SHELL_DETECTION_TIMEOUT",
                error_message="timed out while detecting Telnet shell capabilities",
                expected_disconnect=expect_disconnect,
            )
        except _ConnectionClosed as exc:
            self._drop_connection()
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.CONNECTION_FAILED,
                phase=CommandPhase.DETECT_PROMPT,
                started_at=started_at,
                started_clock=started_clock,
                stdout=self._clean_terminal_text(exc.partial),
                raw_output=exc.partial,
                error_type="CONNECTION_FAILED",
                error_message=self._connection_error_message(exc),
                expected_disconnect=expect_disconnect,
                disconnected=True,
            )

        marker = uuid.uuid4().hex
        result_marker = f"__AUTOENV_RC_{marker}__"
        mode = self._active_shell_mode
        if keyword is not None:
            wire_command = command
        elif mode == "posix":
            marker_command = f"printf '\\n{result_marker}:%d\\n' $?"
            wire_command = f"{command}\n{marker_command}"
        else:
            wire_command = command

        try:
            self._send_text(wire_command + "\r\n")
        except (OSError, _ConnectionClosed) as exc:
            self._drop_connection()
            cause = exc if isinstance(exc, OSError) else exc.cause
            message = str(cause) if cause else "connection closed while sending command"
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.DISCONNECTED,
                phase=CommandPhase.SEND_COMMAND,
                started_at=started_at,
                started_clock=started_clock,
                raw_output="",
                error_type="SEND_FAILED",
                error_message=message,
                expected_disconnect=expect_disconnect,
                disconnected=True,
            )

        if keyword is not None:
            try:
                raw_output = self._receive_until(
                    timeout,
                    lambda text: keyword in self._clean_terminal_text(text),
                    stream=False,
                )
            except _ReadTimedOut as exc:
                raw_output = exc.partial
                stdout = self._clean_command_output(raw_output, wire_command, "")
                self._drop_connection()
                return self._result(
                    operation_id=operation_id,
                    command=command,
                    status=CommandStatus.TIMEOUT,
                    phase=CommandPhase.WAIT_OUTPUT,
                    started_at=started_at,
                    started_clock=started_clock,
                    stdout=stdout,
                    raw_output=raw_output,
                    error_type="KEYWORD_NOT_FOUND",
                    error_message=(
                        f"Telnet output keyword {keyword!r} was not found within "
                        f"{timeout:g} seconds"
                    ),
                )
            except _ConnectionClosed as exc:
                raw_output = exc.partial
                stdout = self._clean_command_output(raw_output, wire_command, "")
                self._drop_connection()
                return self._result(
                    operation_id=operation_id,
                    command=command,
                    status=CommandStatus.DISCONNECTED,
                    phase=CommandPhase.WAIT_OUTPUT,
                    started_at=started_at,
                    started_clock=started_clock,
                    stdout=stdout,
                    raw_output=raw_output,
                    error_type="CONNECTION_LOST",
                    error_message=self._connection_error_message(exc),
                    disconnected=True,
                )

            try:
                assert self._sock is not None and send_data is not None
                self._sock.sendall(send_data)
            except (OSError, EOFError) as exc:
                stdout = self._clean_command_output(raw_output, wire_command, "")
                self._drop_connection()
                return self._result(
                    operation_id=operation_id,
                    command=command,
                    status=CommandStatus.PROTOCOL_ERROR,
                    phase=CommandPhase.SEND_COMMAND,
                    started_at=started_at,
                    started_clock=started_clock,
                    stdout=stdout,
                    raw_output=raw_output,
                    error_type="RESPONSE_SEND_FAILED",
                    error_message=str(exc) or "Telnet response bytes could not be sent",
                )

            stdout = self._clean_command_output(raw_output, wire_command, "")
            # The response often changes from a Linux shell to a bootloader or
            # another prompt. Discard stale prompt/mode state; the object remains
            # reusable and reconnects lazily on its next operation.
            self._drop_connection()
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.SUCCESS,
                phase=CommandPhase.COMPLETE,
                started_at=started_at,
                started_clock=started_clock,
                stdout=stdout,
                raw_output=raw_output,
            )

        try:
            raw_output = self._read_to_prompt(
                timeout,
                marker=result_marker if mode == "posix" else None,
                stream=False,
            )
        except _ReadTimedOut as exc:
            raw_output = exc.partial
            stdout = self._clean_command_output(raw_output, wire_command, result_marker)
            self._drop_connection()
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.TIMEOUT,
                phase=CommandPhase.WAIT_OUTPUT,
                started_at=started_at,
                started_clock=started_clock,
                stdout=stdout,
                raw_output=raw_output,
                error_type="COMMAND_TIMEOUT",
                error_message="timed out while waiting for the Telnet command to finish",
                expected_disconnect=expect_disconnect,
            )
        except _ConnectionClosed as exc:
            raw_output = exc.partial
            stdout = self._clean_command_output(raw_output, wire_command, result_marker)
            exit_code = self._extract_exit_code(raw_output, result_marker) if mode == "posix" else None
            self._drop_connection()

            if exit_code is not None:
                status = (
                    CommandStatus.SUCCESS
                    if exit_code == 0
                    else CommandStatus.COMMAND_FAILED
                )
                error_type = None if exit_code == 0 else "NON_ZERO_EXIT_CODE"
                error_message = (
                    None
                    if exit_code == 0
                    else f"remote command exited with code {exit_code}"
                )
                phase = CommandPhase.COMPLETE
            elif expect_disconnect:
                status = CommandStatus.SUCCESS
                error_type = None
                error_message = None
                phase = CommandPhase.COMPLETE
            else:
                status = CommandStatus.DISCONNECTED
                error_type = "CONNECTION_LOST"
                error_message = self._connection_error_message(exc)
                phase = CommandPhase.WAIT_OUTPUT

            return self._result(
                operation_id=operation_id,
                command=command,
                status=status,
                phase=phase,
                started_at=started_at,
                started_clock=started_clock,
                exit_code=exit_code,
                stdout=stdout,
                raw_output=raw_output,
                error_type=error_type,
                error_message=error_message,
                expected_disconnect=expect_disconnect,
                disconnected=True,
            )

        stdout = self._clean_command_output(raw_output, wire_command, result_marker)
        if mode != "posix":
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.RESULT_UNKNOWN,
                phase=CommandPhase.COMPLETE,
                started_at=started_at,
                started_clock=started_clock,
                stdout=stdout,
                raw_output=raw_output,
                error_type="EXIT_STATUS_UNAVAILABLE",
                error_message="prompt-only Telnet mode cannot determine the exit status",
                expected_disconnect=expect_disconnect,
            )

        exit_code = self._extract_exit_code(raw_output, result_marker)
        if exit_code is None:
            self._drop_connection()
            return self._result(
                operation_id=operation_id,
                command=command,
                status=CommandStatus.PROTOCOL_ERROR,
                phase=CommandPhase.PARSE_RESULT,
                started_at=started_at,
                started_clock=started_clock,
                stdout=stdout,
                raw_output=raw_output,
                error_type="EXIT_MARKER_NOT_FOUND",
                error_message="Telnet command prompt returned without a valid exit marker",
                expected_disconnect=expect_disconnect,
            )

        status = CommandStatus.SUCCESS if exit_code == 0 else CommandStatus.COMMAND_FAILED
        return self._result(
            operation_id=operation_id,
            command=command,
            status=status,
            phase=CommandPhase.COMPLETE,
            started_at=started_at,
            started_clock=started_clock,
            exit_code=exit_code,
            stdout=stdout,
            raw_output=raw_output,
            error_type=None if exit_code == 0 else "NON_ZERO_EXIT_CODE",
            error_message=(
                None if exit_code == 0 else f"remote command exited with code {exit_code}"
            ),
            expected_disconnect=expect_disconnect,
        )

    def _open_session(self, timeout: float) -> str:
        try:
            sock = self._socket_factory((self.info.host, self.info.port), timeout=timeout)
        except TypeError:
            # Small injected factories often model create_connection's timeout as
            # a positional argument. Keep both forms testable without changing the
            # production default.
            sock = self._socket_factory((self.info.host, self.info.port), timeout)

        self._sock = sock
        self._telnet_filter = _TelnetByteFilter()
        self._prompt = None
        self._active_shell_mode = None
        self._set_socket_timeout(min(timeout, 0.2))

        try:
            self._send_text("\r\n")
            initial = self._read_until_new_prompt(timeout)
            prompt = self._find_prompt(initial)
            if prompt is None:
                raise _PromptNotFound(initial)
            self._prompt = prompt

            if self.info.shell_mode == "auto":
                probe_marker = f"__AUTOENV_PROBE_{uuid.uuid4().hex}__"
                probe = f"printf '\\n{probe_marker}:%d\\n' $?\r\n"
                self._send_text(probe)
                probe_output = self._read_to_prompt(timeout, marker=None, stream=False)
                if self._extract_exit_code(probe_output, probe_marker) is not None:
                    self._active_shell_mode = "posix"
                else:
                    self._active_shell_mode = "prompt_only"
                return initial + probe_output

            self._active_shell_mode = self.info.shell_mode
            return initial
        except BaseException:
            # The caller needs the original error for status mapping; it will also
            # call _drop_connection, so do not translate it here.
            raise

    def _read_until_new_prompt(self, timeout: float) -> str:
        try:
            return self._receive_until(timeout, lambda text: self._find_prompt(text) is not None)
        except _ReadTimedOut as exc:
            raise _PromptNotFound(exc.partial) from exc

    def _read_to_prompt(
        self, timeout: float, *, marker: str | None, stream: bool
    ) -> str:
        # Framing ends at the prompt. Marker validation is deliberately a
        # separate parse phase so a shell that returns a prompt but omits or
        # mangles the marker is reported as PROTOCOL_ERROR/PARSE_RESULT rather
        # than as a command timeout.
        del marker

        def complete(text: str) -> bool:
            return self._prompt_at_end(text) is not None

        return self._receive_until(timeout, complete, stream=stream)

    def _receive_until(
        self,
        timeout: float,
        complete: Callable[[str], bool],
        *,
        stream: bool = False,
    ) -> str:
        if self._sock is None:
            raise _ConnectionClosed("")

        deadline = self._clock() + timeout
        chunks: list[str] = []
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                partial = "".join(chunks) + decoder.decode(b"", final=True)
                raise _ReadTimedOut(partial)

            self._set_socket_timeout(min(remaining, 0.2))
            try:
                packet = self._sock.recv(4096)
            except socket.timeout:
                self._sleep(min(0.01, max(remaining, 0.0)))
                continue
            except (OSError, EOFError) as exc:
                partial = "".join(chunks) + decoder.decode(b"", final=True)
                raise _ConnectionClosed(partial, exc) from exc

            if not packet:
                partial = "".join(chunks) + decoder.decode(b"", final=True)
                raise _ConnectionClosed(partial)

            application_data, replies = self._telnet_filter.feed(packet)
            if replies:
                try:
                    self._sock.sendall(replies)
                except OSError as exc:
                    partial = "".join(chunks) + decoder.decode(b"", final=True)
                    raise _ConnectionClosed(partial, exc) from exc

            text = decoder.decode(application_data, final=False)
            if text:
                chunks.append(text)
                if stream:
                    self.recorder.stream(f"TELNET[{self.name}]", self._clean_terminal_text(text))

            combined = "".join(chunks)
            if complete(combined):
                return combined

    def _send_text(self, text: str) -> None:
        if self._sock is None:
            raise _ConnectionClosed("")
        self._sock.sendall(text.encode("utf-8"))

    def _set_socket_timeout(self, value: float) -> None:
        if self._sock is not None:
            self._sock.settimeout(value)

    def _drop_connection(self) -> None:
        sock = self._sock
        self._sock = None
        self._prompt = None
        self._active_shell_mode = None
        self._telnet_filter = _TelnetByteFilter()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    @classmethod
    def _clean_terminal_text(cls, text: str) -> str:
        cleaned = _ANSI_RE.sub("", text).replace("\x00", "")
        while "\b" in cleaned:
            cleaned = re.sub(r"[^\r\n]\b", "", cleaned)
            cleaned = cleaned.replace("\b", "")
        return cleaned

    @classmethod
    def _find_prompt(cls, text: str) -> str | None:
        normalized = cls._clean_terminal_text(text).replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in normalized.split("\n") if line.strip()]
        if not lines:
            return None
        candidate = lines[-1]
        lowered = candidate.lower()
        if len(candidate) > 256 or lowered.startswith(("login:", "password:")):
            return None
        if candidate == "#root" or candidate[0] in "#$>" or candidate[-1] in "#$>":
            return candidate
        return None

    def _prompt_at_end(self, text: str) -> str | None:
        candidate = self._find_prompt(text)
        if candidate is None or self._prompt is None:
            return None
        if candidate == self._prompt:
            return candidate

        # Common POSIX prompts embed the current directory. Permit that portion
        # to change while retaining the same prompt terminator.
        dynamic = any(token in self._prompt for token in ("@", ":", "/", "~"))
        if dynamic and candidate[-1:] == self._prompt[-1:] and candidate[-1:] in "#$>":
            self._prompt = candidate
            return candidate
        return None

    @classmethod
    def _extract_exit_code(cls, text: str, marker: str) -> int | None:
        if not marker:
            return None
        match = re.search(rf"{re.escape(marker)}:([+-]?\d+)(?=\s|$)", cls._clean_terminal_text(text))
        if match is None:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _clean_command_output(self, raw: str, wire_command: str, marker: str) -> str:
        text = self._clean_terminal_text(raw).replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")

        prompt = self._prompt_at_end(text)
        while lines and not lines[-1].strip():
            lines.pop()
        if prompt is not None and lines and lines[-1].strip() == prompt:
            lines.pop()

        if marker:
            marker_line = re.compile(rf"^\s*{re.escape(marker)}:[+-]?\d+\s*$")
            lines = [line for line in lines if marker_line.match(line) is None]

        sent_lines = [line.strip() for line in wire_command.splitlines() if line.strip()]
        while lines and not lines[0].strip():
            lines.pop(0)
        for sent in sent_lines:
            if not lines:
                break
            received = lines[0].strip()
            if received == sent:
                lines.pop(0)
                continue
            if self._prompt and received == f"{self._prompt}{sent}":
                lines.pop(0)
                continue
            break

        return "\n".join(lines).strip("\n")

    @staticmethod
    def _connection_error_message(exc: _ConnectionClosed) -> str:
        if exc.cause is not None and str(exc.cause):
            return str(exc.cause)
        return "Telnet connection closed before the command result was complete"

    def _result(
        self,
        *,
        operation_id: str,
        command: str,
        status: CommandStatus,
        phase: CommandPhase,
        started_at: datetime,
        started_clock: float,
        exit_code: int | None = None,
        stdout: str = "",
        raw_output: str = "",
        error_type: str | None = None,
        error_message: str | None = None,
        expected_disconnect: bool = False,
        disconnected: bool = False,
    ) -> CommandResult:
        finished_at = datetime.now().astimezone()
        duration_ms = max(0, int(round((self._clock() - started_clock) * 1000)))
        result = CommandResult(
            run_id=self.run_id,
            operation_id=operation_id,
            protocol=CommandProtocol.TELNET,
            target_name=self.name,
            command=command,
            status=status,
            phase=phase,
            exit_code=exit_code,
            stdout=stdout,
            stderr="",
            raw_output=raw_output,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_type=error_type,
            error_message=error_message,
            expected_disconnect=expected_disconnect,
            disconnected=disconnected,
        )
        self.recorder.record_result("TELNET EXECUTE", result)
        return result
