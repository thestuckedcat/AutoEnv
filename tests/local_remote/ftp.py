"""Small loopback FTP server with real control and data connections."""

from __future__ import annotations

import posixpath
import socket
import socketserver
import threading
from pathlib import Path


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True


class LocalFTPServer:
    """Persist STOR bytes on disk through passive or active FTP sockets."""

    def __init__(
        self,
        root: Path,
        *,
        username: str = "user",
        password: str = "password",
    ) -> None:
        self.host = "127.0.0.1"
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.username = username
        self.password = password
        self.commands: list[str] = []
        self.transfers: list[tuple[str, bytes]] = []
        self.downloads: list[tuple[str, bytes]] = []
        self.data_modes: list[str] = []
        self.size_overrides: dict[str, int] = {}
        self.rejected_directories: set[str] = set()
        self.sessions = 0
        self.errors: list[BaseException] = []
        self._lock = threading.Lock()
        self._connections: set[socket.socket] = set()
        self.worker_threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._data_ready = threading.Event()
        self._closed = False
        remote = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                remote._handle(self)

        self._server = _Server((self.host, 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.02),
            daemon=False,
        )

    def __enter__(self) -> "LocalFTPServer":
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
            self._server.shutdown()
        with self._lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        # Workers own open destination handles. Join them before tmp_path can
        # be removed, after interrupting both control and data socket reads.
        self._server.server_close()
        if self._thread.ident is not None:
            self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise RuntimeError("FTP service did not stop")

    def wait_for_data_connection(self, timeout: float = 2) -> bool:
        return self._data_ready.wait(timeout)

    def _track_socket(self, connection: socket.socket) -> None:
        with self._lock:
            if self._stop.is_set():
                connection.close()
                raise ConnectionAbortedError("FTP test service is stopping")
            self._connections.add(connection)

    def _release_socket(self, connection: socket.socket) -> None:
        connection.close()
        with self._lock:
            self._connections.discard(connection)

    def _path(self, remote_path: str) -> Path:
        relative = posixpath.normpath("/" + remote_path).lstrip("/")
        path = (self.root / relative).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("remote path escapes the test root")
        return path

    def read_bytes(self, remote_path: str) -> bytes:
        return self._path(remote_path).read_bytes()

    def _handle(self, handler: socketserver.StreamRequestHandler) -> None:
        control = handler.request
        control.settimeout(0.1)
        passive: socket.socket | None = None
        active: tuple[str, int] | None = None
        authenticated = False
        supplied_user = ""
        control_buffer = bytearray()

        def read_line() -> bytes:
            # A buffered socket.makefile cannot be reused after a timeout.
            # Read control bytes directly so shutdown can poll _stop even on
            # Windows, where shutdown does not always wake a socket select.
            while not self._stop.is_set():
                end = control_buffer.find(b"\n")
                if end >= 0:
                    line = bytes(control_buffer[: end + 1])
                    del control_buffer[: end + 1]
                    return line
                if len(control_buffer) > 65536:
                    raise ValueError("FTP control line is too long")
                try:
                    chunk = control.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    return b""
                control_buffer.extend(chunk)
            return b""

        def send(message: str) -> None:
            handler.wfile.write((message + "\r\n").encode("utf-8"))
            handler.wfile.flush()

        with self._lock:
            if self._stop.is_set():
                return
            self.sessions += 1
            self.worker_threads.append(threading.current_thread())
            self._connections.add(control)
        try:
            send("220 Local FTP integration peer")
            while not self._stop.is_set():
                raw = read_line()
                if not raw:
                    return
                line = raw.decode("utf-8").rstrip("\r\n")
                with self._lock:
                    self.commands.append(line)
                verb, _, argument = line.partition(" ")
                verb = verb.upper()
                if verb == "QUIT":
                    send("221 Goodbye")
                    return
                if verb == "USER":
                    supplied_user = argument
                    send("331 Password required")
                    continue
                if verb == "PASS":
                    authenticated = supplied_user == self.username and argument == self.password
                    send("230 Logged in" if authenticated else "530 Authentication failed")
                    continue
                if not authenticated:
                    send("530 Please login")
                    continue
                if verb == "TYPE":
                    send("200 Type set")
                elif verb == "PWD":
                    send('257 "/" is the current directory')
                elif verb == "MKD":
                    path = self._path(argument)
                    if argument in self.rejected_directories:
                        send("553 Directory creation rejected")
                    elif path.exists():
                        send("550 Directory exists")
                    else:
                        path.mkdir()
                        send(f'257 "{argument}" created')
                elif verb == "SIZE":
                    path = self._path(argument)
                    if path.is_file():
                        send(f"213 {self.size_overrides.get(argument, path.stat().st_size)}")
                    else:
                        send("550 File does not exist")
                elif verb == "PASV":
                    if passive is not None:
                        self._release_socket(passive)
                    passive = socket.socket()
                    passive.bind((self.host, 0))
                    passive.listen(1)
                    passive.settimeout(0.1)
                    self._track_socket(passive)
                    port = passive.getsockname()[1]
                    send(f"227 Entering Passive Mode (127,0,0,1,{port // 256},{port % 256})")
                    active = None
                    self.data_modes.append("passive")
                elif verb == "PORT":
                    values = [int(value) for value in argument.split(",")]
                    active = (".".join(str(value) for value in values[:4]), values[4] * 256 + values[5])
                    if active[0] != self.host:
                        send("501 Only loopback data peers are allowed")
                        active = None
                    else:
                        send("200 Active data address accepted")
                        self.data_modes.append("active")
                elif verb in {"STOR", "RETR"}:
                    path = self._path(argument)
                    if not path.parent.is_dir():
                        send("550 Destination directory does not exist")
                        continue
                    if verb == "RETR" and not path.is_file():
                        send("550 File does not exist")
                        continue
                    if passive is None and active is None:
                        send("425 Set a data connection first")
                        continue
                    send("150 Opening binary data connection")
                    if active is not None:
                        data = socket.create_connection(active, timeout=1)
                    else:
                        assert passive is not None
                        while not self._stop.is_set():
                            try:
                                data, _ = passive.accept()
                                break
                            except socket.timeout:
                                continue
                        else:
                            return
                        self._release_socket(passive)
                        passive = None
                    self._track_socket(data)
                    self._data_ready.set()
                    data.settimeout(0.1)
                    try:
                        if verb == "RETR":
                            with data, path.open("rb") as source:
                                for chunk in iter(lambda: source.read(65536), b""):
                                    data.sendall(chunk)
                        else:
                            with data, path.open("wb") as target:
                                while not self._stop.is_set():
                                    try:
                                        chunk = data.recv(65536)
                                    except socket.timeout:
                                        continue
                                    if not chunk:
                                        break
                                    target.write(chunk)
                    finally:
                        self._release_socket(data)
                    if self._stop.is_set():
                        return
                    with self._lock:
                        (self.transfers if verb == "STOR" else self.downloads).append((argument, path.read_bytes()))
                    send("226 Transfer complete")
                else:
                    send("502 Command not implemented")
        except (ConnectionError, OSError):
            pass
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            if passive is not None:
                self._release_socket(passive)
            with self._lock:
                self._connections.discard(control)
