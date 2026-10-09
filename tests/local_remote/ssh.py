"""A disk-backed SSH peer: genuine Paramiko/SFTP and SCP wire protocols.

Only the server's shell is simulated. Arbitrary commands are recorded, never
executed. The small built-in command vocabulary implements framework metadata
requests against a sandbox directory. Clients and file transfer methods are
never replaced or patched.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shlex
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import paramiko


@dataclass(frozen=True)
class CommandReply:
    stdout: tuple[bytes, ...] = ()
    stderr: tuple[bytes, ...] = ()
    exit_code: int | None = 0
    disconnect: bool = False
    wait_for_input: bool = False
    chunk_delay: float = 0.02


class _Server(paramiko.ServerInterface):
    def __init__(self, peer: "LocalSSHServer") -> None:
        self.peer = peer

    def check_auth_password(self, username: str, password: str) -> int:
        self.peer.auth_attempts.append((username, password))
        if (username, password) == (self.peer.username, self.peer.password):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        text = command.decode("utf-8")
        self.peer.commands.append(text)
        self.peer._start_thread(self.peer._execute, channel, text)
        return True

    def check_channel_subsystem_request(self, channel: paramiko.Channel, name: str) -> bool:
        if name == "sftp" and self.peer.sftp_enabled:
            self.peer.sftp_sessions += 1
            return super().check_channel_subsystem_request(channel, name)
        return False


class _FileHandle(paramiko.SFTPHandle):
    def __init__(self, flags: int, peer: "LocalSSHServer", path: str, handle) -> None:
        super().__init__(flags)
        self.peer, self.path, self.handle = peer, path, handle
        self.readfile = self.writefile = handle
        self.writable = bool(flags & (os.O_WRONLY | os.O_RDWR))

    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.handle.fileno()))

    def close(self):
        self.handle.close()
        if self.writable:
            self.peer._after_upload("sftp", self.path)
        return paramiko.SFTP_OK


class _SFTP(paramiko.SFTPServerInterface):
    def __init__(self, server: _Server, *args, **kwargs) -> None:
        super().__init__(server, *args, **kwargs)
        self.peer = server.peer

    def _error(self, exc: OSError):
        return paramiko.SFTPServer.convert_errno(exc.errno or 5)

    def stat(self, path: str):
        try:
            value = paramiko.SFTPAttributes.from_stat(self.peer.path(path).stat())
            if path in self.peer.reported_sizes:
                value.st_size = self.peer.reported_sizes[path]
            return value
        except OSError as exc:
            return self._error(exc)

    lstat = stat

    def list_folder(self, path: str):
        try:
            result = []
            for child in sorted(self.peer.path(path).iterdir()):
                value = paramiko.SFTPAttributes.from_stat(child.stat())
                value.filename = child.name
                result.append(value)
            return result
        except OSError as exc:
            return self._error(exc)

    def mkdir(self, path: str, attr):
        try:
            self.peer.path(path).mkdir()
            return paramiko.SFTP_OK
        except OSError as exc:
            return self._error(exc)

    def open(self, path: str, flags: int, attr):
        try:
            if path in self.peer.denied_reads and not flags & (os.O_WRONLY | os.O_RDWR):
                return paramiko.SFTP_PERMISSION_DENIED
            writable = bool(flags & (os.O_WRONLY | os.O_RDWR))
            direction = "upload" if writable else "download"
            self.peer.transfers.append(("sftp", direction, path))
            descriptor = os.open(self.peer.path(path), flags | getattr(os, "O_BINARY", 0), 0o644)
            handle = os.fdopen(descriptor, "r+b" if writable else "rb")
            return _FileHandle(flags, self.peer, path, handle)
        except OSError as exc:
            return self._error(exc)

    def remove(self, path: str):
        try:
            self.peer.path(path).unlink()
            return paramiko.SFTP_OK
        except OSError as exc:
            return self._error(exc)


class LocalSSHServer:
    host = "127.0.0.1"
    username = "local-user"
    password = "local-password"

    def __init__(self, root: Path, *, sftp_enabled: bool = True, banner_delay: float = 0) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.sftp_enabled = sftp_enabled
        self.banner_delay = banner_delay
        self.commands: list[str] = []
        self.replies: dict[str, CommandReply] = {}
        self.received_input: list[tuple[str, bytes]] = []
        self.transfers: list[tuple[str, str, str]] = []
        self.auth_attempts: list[tuple[str, str]] = []
        self.upload_transforms: dict[str, Callable[[bytes], bytes]] = {}
        self.denied_reads: set[str] = set()
        self.truncated_downloads: set[str] = set()
        self.reported_sizes: dict[str, int] = {}
        self.transfer_barrier: threading.Barrier | None = None
        self.active_transfers = self.peak_transfers = self.connections = self.sftp_sessions = 0
        self.errors: list[BaseException] = []
        self._stopped = threading.Event()
        self._threads: list[threading.Thread] = []
        self._transports: list[paramiko.Transport] = []
        self._channels: list[paramiko.Channel] = []
        self._sockets: list[socket.socket] = []
        self._lock = threading.Lock()
        self._key = paramiko.RSAKey.generate(2048)
        self._listener: socket.socket | None = None
        self.port = 0

    def path(self, remote_path: str) -> Path:
        normalized = posixpath.normpath("/" + remote_path.lstrip("/"))
        result = (self.root / normalized.lstrip("/")).resolve()
        result.relative_to(self.root)
        return result

    def seed(self, remote_path: str, payload: bytes, *, mtime: float | None = None) -> Path:
        result = self.path(remote_path)
        result.parent.mkdir(parents=True, exist_ok=True)
        result.write_bytes(payload)
        if mtime is not None:
            os.utime(result, (mtime, mtime))
        return result

    def start(self) -> "LocalSSHServer":
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind((self.host, 0))
        self._listener.listen(16)
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._start_thread(self._accept)
        return self

    def _start_thread(self, function, *args) -> None:
        worker = threading.Thread(target=function, args=args, daemon=True)
        self._threads.append(worker)
        worker.start()

    def _accept(self) -> None:
        while not self._stopped.is_set():
            try:
                assert self._listener is not None
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections += 1
            self._sockets.append(connection)
            self._start_thread(self._serve, connection)

    def _serve(self, connection: socket.socket) -> None:
        try:
            if self._stopped.wait(self.banner_delay):
                return
            transport = paramiko.Transport(connection)
            self._transports.append(transport)
            transport.add_server_key(self._key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _SFTP)
            transport.start_server(server=_Server(self))
            while transport.is_active() and not self._stopped.wait(0.02):
                # Retain accepted channels: releasing a channel before its exec
                # request arrives can send an unintended close to the client.
                channel = transport.accept(timeout=0.05)
                if channel is not None:
                    self._channels.append(channel)
        except (EOFError, OSError, paramiko.SSHException):
            # Client auth/timeout/disconnection scenarios are intentional.
            pass

    def _execute(self, channel: paramiko.Channel, command: str) -> None:
        try:
            # The exec-success packet must precede channel completion.
            time.sleep(0.005)
            tokens = shlex.split(command)
            if tokens and tokens[0] == "scp":
                self._scp(channel, tokens)
                return
            reply = self.replies.get(command) or self._metadata_reply(command)
            for chunk in reply.stdout:
                channel.sendall(chunk)
                if self._stopped.wait(reply.chunk_delay):
                    return
            for chunk in reply.stderr:
                channel.sendall_stderr(chunk)
                if self._stopped.wait(reply.chunk_delay):
                    return
            if reply.wait_for_input:
                channel.settimeout(2)
                data = channel.recv(65536)
                self.received_input.append((command, data))
            if reply.disconnect:
                channel.get_transport().close()
                return
            if reply.exit_code is None:
                while not channel.closed and not self._stopped.wait(0.01):
                    pass
                return
            channel.send_exit_status(reply.exit_code)
            channel.shutdown_write()
        except (EOFError, OSError, paramiko.SSHException):
            pass
        except Exception as exc:
            self.errors.append(exc)
        finally:
            channel.close()

    def _metadata_reply(self, command: str) -> CommandReply:
        tokens = shlex.split(command)
        probe = re.search(r"(__AUTOENV_SELF_CHECK_[0-9a-f]+__)", command)
        if probe:
            return CommandReply(stdout=((probe.group(1) + "\n").encode(),))
        if tokens[:2] == ["mkdir", "-p"]:
            self.path(tokens[2]).mkdir(parents=True, exist_ok=True)
        elif tokens[:3] == ["if", "[", "-e"]:
            return CommandReply(stdout=(b"1" if self.path(tokens[3]).exists() else b"0",))
        elif tokens and tokens[0] == "md5sum":
            file = self.path(tokens[1])
            return CommandReply(stdout=(f"{hashlib.md5(file.read_bytes()).hexdigest()}  {tokens[1]}\n".encode(),))
        elif tokens[:3] == ["wc", "-c", "<"]:
            file = self.path(tokens[3])
            if not file.is_file():
                return CommandReply(stderr=(b"file not found",), exit_code=1)
            return CommandReply(stdout=(str(self.reported_sizes.get(tokens[3], file.stat().st_size)).encode(),))
        elif command.startswith("remote_dir="):
            remote_dir = shlex.split(command.split("\n", 1)[0].split("=", 1)[1])[0]
            directory = self.path(remote_dir)
            if not directory.is_dir():
                return CommandReply(stderr=(b"remote directory not found",), exit_code=40)
            metadata = "stat -c %Y" in command
            records = []
            for file in sorted(directory.iterdir()):
                if file.is_file():
                    records.append(file.name)
                    if metadata:
                        records.append(str(int(file.stat().st_mtime)))
            output = ("\0".join(records) + ("\0" if records else "")).encode("utf-8")
            return CommandReply(stdout=(output,))
        # Arbitrary user commands are delivered and recorded only.
        return CommandReply()

    @staticmethod
    def _read_exact(channel: paramiko.Channel, size: int) -> bytes:
        parts = []
        while size:
            part = channel.recv(min(size, 65536))
            if not part:
                raise EOFError("SCP peer closed the channel")
            parts.append(part)
            size -= len(part)
        return b"".join(parts)

    @classmethod
    def _read_line(cls, channel: paramiko.Channel) -> bytes:
        result = bytearray()
        while True:
            byte = cls._read_exact(channel, 1)
            if byte == b"\n":
                return bytes(result)
            result.extend(byte)

    @classmethod
    def _ack(cls, channel: paramiko.Channel) -> None:
        value = cls._read_exact(channel, 1)
        if value != b"\0":
            raise OSError("SCP peer rejected a protocol message")

    def _after_upload(self, protocol: str, remote_path: str) -> None:
        transform = self.upload_transforms.get(protocol)
        if transform:
            file = self.path(remote_path)
            file.write_bytes(transform(file.read_bytes()))

    def _scp(self, channel: paramiko.Channel, tokens: list[str]) -> None:
        channel.settimeout(3)
        remote_path = tokens[-1]
        uploading = "-t" in tokens
        if uploading:
            channel.sendall(b"\0")
            header = self._read_line(channel)
            if header.startswith(b"T"):
                channel.sendall(b"\0")
                header = self._read_line(channel)
            mode, size, filename = header[1:].split(b" ", 2)
            if not header.startswith(b"C"):
                raise ValueError("unsupported SCP receive record")
            destination = posixpath.join(remote_path, filename.decode("utf-8"))
            file = self.path(destination)
            self.transfers.append(("scp", "upload", destination))
            channel.sendall(b"\0")
            with file.open("wb") as output:
                remaining = int(size)
                while remaining:
                    block = self._read_exact(channel, min(remaining, 65536))
                    output.write(block)
                    remaining -= len(block)
            self._ack(channel)
            self._after_upload("scp", destination)
            channel.sendall(b"\0")
        else:
            self._ack(channel)
            if remote_path in self.denied_reads:
                channel.sendall(b"\x01Permission denied\n")
                return
            file = self.path(remote_path)
            if not file.is_file():
                channel.sendall(b"\x01File not found\n")
                return
            self.transfers.append(("scp", "download", remote_path))
            with self._lock:
                self.active_transfers += 1
                self.peak_transfers = max(self.peak_transfers, self.active_transfers)
            try:
                if self.transfer_barrier is not None:
                    self.transfer_barrier.wait(timeout=3)
                size = file.stat().st_size
                channel.sendall(f"C0644 {size} {file.name}\n".encode("utf-8"))
                self._ack(channel)
                if remote_path in self.truncated_downloads:
                    channel.sendall(file.read_bytes()[: max(1, size // 2)])
                    return
                with file.open("rb") as source:
                    for chunk in iter(lambda: source.read(65536), b""):
                        channel.sendall(chunk)
                channel.sendall(b"\0")
                self._ack(channel)
            finally:
                with self._lock:
                    self.active_transfers -= 1
        channel.send_exit_status(0)
        channel.shutdown_write()

    def disconnect_clients(self) -> None:
        for transport in self._transports:
            transport.close()

    def wait_for_input(self, command: str, *, timeout: float = 2) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for received_command, data in self.received_input:
                if received_command == command:
                    return data
            time.sleep(0.005)
        raise AssertionError(f"no SSH input arrived for {command!r}")

    def close(self) -> None:
        self._stopped.set()
        if self._listener:
            self._listener.close()
        self.disconnect_clients()
        for connection in self._sockets:
            connection.close()
        for thread in self._threads:
            thread.join(timeout=2)

    def __enter__(self) -> "LocalSSHServer":
        return self.start()

    def __exit__(self, *_) -> None:
        self.close()
