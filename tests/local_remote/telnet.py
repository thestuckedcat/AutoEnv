"""Loopback Telnet peer: record commands without executing a host shell."""

from __future__ import annotations

import re
import socket
import socketserver
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class TelnetReply:
    output: bytes | tuple[bytes, ...] = b""
    exit_code: int = 0
    prompt: str | None = None
    complete: bool = True
    disconnect: bool = False
    omit_marker: bool = False
    expected_input: bytes | None = None


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True


class LocalTelnetServer:
    """Serve real TCP sessions with POSIX framing or an appliance prompt.

    ``replies`` specifies output only; commands are never executed. Commands,
    shell probes, option refusals and raw interactive responses are recorded
    independently so tests can prove what crossed the socket.
    """

    def __init__(
        self,
        *,
        replies: dict[str, TelnetReply] | None = None,
        shell_mode: str = "posix",
        prompt: str = "root@loopback:~#",
        banner: bytes = b"Local Telnet test peer\r\n",
        negotiation: tuple[bytes, ...] = (),
        login_required: bool = False,
        passive: bool = False,
        raw_reply: bytes = b"\r\nbootloader> ",
        username: str = "user",
        password: str = "password",
        login_prompt: str = "login:",
        password_prompt: str = "Password:",
    ) -> None:
        self.host = "127.0.0.1"
        self.replies = replies or {}
        self.shell_mode = shell_mode
        self.prompt = prompt
        self.banner = banner
        self.negotiation = negotiation
        self.login_required = login_required
        self.passive = passive
        self.raw_reply = raw_reply
        self.username = username
        self.password = password
        self.login_prompt = login_prompt
        self.password_prompt = password_prompt
        self.login_attempts: list[tuple[str, str]] = []
        self.commands: list[str] = []
        self.probes: list[str] = []
        self.raw_inputs: list[bytes] = []
        self.negotiation_replies: list[bytes] = []
        self.sessions = 0
        self.errors: list[BaseException] = []
        self._condition = threading.Condition()
        self._connections: set[socket.socket] = set()
        self.worker_threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._closed = False
        remote = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                remote._handle(self.request)

        self._server = _Server((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02),
            daemon=False,
        )

    def __enter__(self) -> "LocalTelnetServer":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread.is_alive():
            # Stop accepting before snapshotting live connections. Any accepted
            # worker that has not started yet sees _stop and returns immediately.
            self._server.shutdown()
        with self._condition:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        # ThreadingMixIn joins every non-daemon worker after recv is interrupted.
        self._server.server_close()
        if self._thread.ident is not None:
            self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise RuntimeError("Telnet service did not stop")

    def wait_for_raw(self, data: bytes, timeout: float = 2, *, count: int = 1) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self.raw_inputs.count(data) >= count, timeout=timeout
            )

    def push_output(self, data: bytes | tuple[bytes, ...]) -> None:
        """Emit unsolicited serial output to every active TCP gateway session."""
        with self._condition:
            connections = tuple(self._connections)
        for connection in connections:
            self._send(connection, data)

    def _send(self, connection: socket.socket, data: bytes | tuple[bytes, ...]) -> None:
        chunks = (data,) if isinstance(data, bytes) else data
        for index, chunk in enumerate(chunks):
            if index:
                self._stop.wait(0.01)
            connection.sendall(chunk)

    def _handle(self, connection: socket.socket) -> None:
        connection.settimeout(0.1)
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self._condition:
            if self._stop.is_set():
                return
            self.sessions += 1
            self.worker_threads.append(threading.current_thread())
            self._connections.add(connection)
        line = bytearray()
        option = bytearray()
        pending: list[str] = []
        expecting: bytes | None = None
        response = bytearray()
        opened = False
        prompt = self.prompt
        auth_stage = 0 if self.login_required else 2
        supplied_username = ""
        try:
            if self.passive:
                opened = True
                self._send(connection, self.negotiation)
                self._send(connection, self.banner)
                self._send(connection, (self.login_prompt + " ").encode() if self.login_required else f"{prompt} ".encode())
            while not self._stop.is_set():
                try:
                    packet = connection.recv(4096)
                except socket.timeout:
                    continue
                if not packet:
                    break
                if expecting is not None:
                    response.extend(packet)
                    if len(response) >= len(expecting):
                        with self._condition:
                            self.raw_inputs.append(bytes(response))
                            self._condition.notify_all()
                        expecting = None
                        response.clear()
                    continue
                for value in packet:
                    if option or value == 255:
                        option.append(value)
                        if len(option) == 3:
                            with self._condition:
                                self.negotiation_replies.append(bytes(option))
                            option.clear()
                        continue
                    if value == 2 and not line:
                        with self._condition:
                            self.raw_inputs.append(b"\x02")
                            self._condition.notify_all()
                        self._send(connection, self.raw_reply)
                        continue
                    line.append(value)
                    if value != 10:
                        continue
                    crlf = line.endswith(b"\r\n")
                    text = bytes(line).rstrip(b"\r\n").decode("utf-8")
                    line.clear()
                    if not opened:
                        opened = True
                        self._send(connection, self.negotiation)
                        self._send(connection, self.banner)
                        self._send(connection, (self.login_prompt + " ").encode() if self.login_required else f"{prompt} ".encode())
                        continue
                    if not text:
                        continue
                    if auth_stage == 0:
                        supplied_username = text
                        auth_stage = 1
                        self._send(connection, (self.password_prompt + " ").encode())
                        continue
                    if auth_stage == 1:
                        self.login_attempts.append((supplied_username, text))
                        if (supplied_username, text) != (self.username, self.password):
                            auth_stage = 0
                            self._send(connection, ("Login incorrect\r\n" + self.login_prompt + " ").encode())
                        else:
                            auth_stage = 2
                            self._send(connection, ("\r\n" + prompt + " ").encode())
                        continue
                    probe = re.search(r"(__AUTOENV_PROBE_[0-9a-f]+__)", text)
                    if probe:
                        with self._condition:
                            self.probes.append(text)
                        output = f"{probe.group(1)}:0\r\n" if self.shell_mode == "posix" else "printf: not found\r\n"
                        self._send(connection, (text + "\r\n" + output + prompt + " ").encode())
                        continue
                    marker = re.search(r"(__AUTOENV_RC_[0-9a-f]+__)", text)
                    if marker:
                        command = "\n".join(pending)
                        pending.clear()
                    elif not crlf:
                        pending.append(text)
                        continue
                    else:
                        command = "\n".join([*pending, text])
                        pending.clear()
                    with self._condition:
                        self.commands.append(command)
                        self._condition.notify_all()
                    check = re.search(r"(__AUTOENV_SELF_CHECK_[0-9a-f]+__)", command)
                    default = TelnetReply(output=(check.group(1) + "\r\n").encode()) if check else TelnetReply()
                    reply = self.replies.get(command, default)
                    wire = command + ("\r\n" + text if marker else "")
                    self._send(connection, (wire + "\r\n").encode())
                    self._send(connection, reply.output)
                    if reply.disconnect:
                        return
                    if reply.expected_input is not None:
                        expecting = reply.expected_input
                    elif reply.complete:
                        if marker and not reply.omit_marker:
                            self._send(connection, f"\r\n{marker.group(1)}:{reply.exit_code}\r\n".encode())
                        prompt = reply.prompt or prompt
                        self._send(connection, ("\r\n" + prompt + " ").encode())
        except (ConnectionError, OSError):
            # A client dropping a timed-out or completed interactive session is
            # ordinary protocol behavior, not a peer failure.
            pass
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            with self._condition:
                self._connections.discard(connection)
