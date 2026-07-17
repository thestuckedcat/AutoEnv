from __future__ import annotations

import errno
import hashlib
import io
import posixpath
import shlex
import socket
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import paramiko
import pytest

from autoenv.results import CommandPhase, CommandStatus
from autoenv.selectors import extra_file, match, package
from autoenv.ssh_host import SSHConnectionInfo, SSHDefaults, SSHHost


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


class FakeRecorder:
    def __init__(self) -> None:
        self.operation = 0
        self.streams: list[tuple[str, str]] = []
        self.results: list[tuple[str, Any]] = []

    def next_operation_id(self) -> str:
        self.operation += 1
        return f"{self.operation:04d}"

    def stream(self, prefix: str, text: str) -> None:
        self.streams.append((prefix, text))

    def record_result(self, operation: str, result: Any) -> None:
        self.results.append((operation, result))


class FakeChannel:
    def __init__(
        self,
        *,
        stdout: tuple[bytes, ...] = (),
        stderr: tuple[bytes, ...] = (),
        exit_code: int | None = 0,
        disconnect_when_drained: bool = False,
        recv_ready_error: BaseException | None = None,
        send_error: BaseException | None = None,
    ) -> None:
        self.stdout = list(stdout)
        self.stderr = list(stderr)
        self.exit_code = exit_code
        self.disconnect_when_drained = disconnect_when_drained
        self.recv_ready_error = recv_ready_error
        self.send_error = send_error
        self.commands: list[str] = []
        self.sent: list[bytes] = []
        self.close_count = 0
        self._explicitly_closed = False

    @property
    def closed(self) -> bool:
        return self._explicitly_closed or (
            self.disconnect_when_drained and not self.stdout and not self.stderr
        )

    def exec_command(self, command: str) -> None:
        self.commands.append(command)

    def sendall(self, data: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(data)

    def recv_ready(self) -> bool:
        if self.stdout:
            return True
        if self.recv_ready_error is not None:
            error = self.recv_ready_error
            self.recv_ready_error = None
            raise error
        return False

    def recv(self, _size: int) -> bytes:
        return self.stdout.pop(0)

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def recv_stderr(self, _size: int) -> bytes:
        return self.stderr.pop(0)

    def exit_status_ready(self) -> bool:
        return self.exit_code is not None

    def recv_exit_status(self) -> int:
        assert self.exit_code is not None
        return self.exit_code

    def close(self) -> None:
        self.close_count += 1
        self._explicitly_closed = True


class FakeExecChannel:
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code

    def recv_exit_status(self) -> int:
        return self.exit_code


class FakeExecStream(io.BytesIO):
    def __init__(self, data: bytes = b"", *, exit_code: int = 0) -> None:
        super().__init__(data)
        self.channel = FakeExecChannel(exit_code)


class FakeRemoteFS:
    def __init__(self) -> None:
        self.dirs = {"/", "."}
        self.files: dict[str, bytes] = {}
        self.mkdir_calls: list[str] = []

    @staticmethod
    def normalize(path: str) -> str:
        return posixpath.normpath(path)

    def ensure_dir(self, path: str) -> None:
        path = self.normalize(path)
        if path in ("/", "."):
            self.dirs.add(path)
            return
        self.ensure_dir(posixpath.dirname(path) or ".")
        self.dirs.add(path)

    def seed_file(self, path: str, data: bytes) -> None:
        path = self.normalize(path)
        self.ensure_dir(posixpath.dirname(path) or ".")
        self.files[path] = data


class FakeSFTP:
    def __init__(
        self,
        fs: FakeRemoteFS,
        upload_transform: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.fs = fs
        self.upload_transform = upload_transform or (lambda data: data)
        self.put_calls: list[tuple[str, str]] = []
        self.open_calls: list[tuple[str, str]] = []
        self.close_count = 0

    def stat(self, path: str) -> SimpleNamespace:
        path = self.fs.normalize(path)
        if path in self.fs.dirs:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)
        if path in self.fs.files:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o644)
        raise FileNotFoundError(errno.ENOENT, "not found", path)

    def mkdir(self, path: str) -> None:
        path = self.fs.normalize(path)
        parent = posixpath.dirname(path) or "."
        if parent not in self.fs.dirs:
            raise FileNotFoundError(errno.ENOENT, "parent not found", parent)
        self.fs.mkdir_calls.append(path)
        self.fs.dirs.add(path)

    def put(self, local_path: str, remote_path: str) -> None:
        remote_path = self.fs.normalize(remote_path)
        self.put_calls.append((local_path, remote_path))
        data = Path(local_path).read_bytes()
        self.fs.files[remote_path] = self.upload_transform(data)

    def open(self, remote_path: str, mode: str) -> io.BytesIO:
        remote_path = self.fs.normalize(remote_path)
        self.open_calls.append((remote_path, mode))
        if remote_path not in self.fs.files:
            raise FileNotFoundError(errno.ENOENT, "not found", remote_path)
        return io.BytesIO(self.fs.files[remote_path])

    def close(self) -> None:
        self.close_count += 1


class FakeTransport:
    def __init__(self, channels: list[FakeChannel] | None = None) -> None:
        self.channels = list(channels or [])
        self.open_timeouts: list[float] = []
        self.active = True
        self.authenticated = True
        self.close_count = 0

    def is_active(self) -> bool:
        return self.active

    def is_authenticated(self) -> bool:
        return self.authenticated

    def open_session(self, timeout: float) -> FakeChannel:
        self.open_timeouts.append(timeout)
        if not self.channels:
            raise AssertionError("no fake channel queued")
        return self.channels.pop(0)

    def close(self) -> None:
        self.close_count += 1
        self.active = False


class FakeClient:
    def __init__(
        self,
        transport: FakeTransport,
        *,
        sftp: FakeSFTP | None = None,
        sftp_factory: Callable[[], FakeSFTP] | None = None,
        fs: FakeRemoteFS | None = None,
        connect_error: BaseException | None = None,
    ) -> None:
        self.transport = transport
        self.sftp = sftp
        self.sftp_factory = sftp_factory
        self.fs = fs
        self.sftp_sessions = [sftp] if sftp is not None else []
        self.connect_error = connect_error
        self.connect_calls: list[dict[str, Any]] = []
        self.policies: list[Any] = []
        self.open_sftp_count = 0
        self.close_count = 0
        self.exec_calls: list[tuple[str, float | None]] = []

    def set_missing_host_key_policy(self, policy: Any) -> None:
        self.policies.append(policy)

    def connect(self, **kwargs: Any) -> None:
        self.connect_calls.append(kwargs)
        if self.connect_error is not None:
            raise self.connect_error

    def get_transport(self) -> FakeTransport:
        return self.transport

    def open_sftp(self) -> FakeSFTP:
        self.open_sftp_count += 1
        if self.open_sftp_count == 1:
            assert self.sftp is not None
            return self.sftp
        assert self.sftp_factory is not None
        session = self.sftp_factory()
        self.sftp_sessions.append(session)
        return session

    def exec_command(
        self, command: str, *, timeout: float | None = None
    ) -> tuple[FakeExecStream, FakeExecStream, FakeExecStream]:
        self.exec_calls.append((command, timeout))
        assert self.fs is not None
        tokens = shlex.split(command)
        output = b""
        error = b""
        exit_code = 0
        if tokens[:2] == ["mkdir", "-p"]:
            remote_dir = self.fs.normalize(tokens[2])
            missing: list[str] = []
            cursor = remote_dir
            while cursor not in self.fs.dirs and cursor not in ("/", "."):
                missing.append(cursor)
                cursor = posixpath.dirname(cursor) or "."
            for item in reversed(missing):
                self.fs.mkdir_calls.append(item)
                self.fs.dirs.add(item)
        elif tokens[:3] == ["if", "[", "-e"]:
            path = self.fs.normalize(tokens[3])
            output = b"1" if path in self.fs.files or path in self.fs.dirs else b"0"
        elif tokens[:1] == ["md5sum"]:
            path = self.fs.normalize(tokens[1])
            if path in self.fs.files:
                output = f"{md5(self.fs.files[path])}  {path}\n".encode()
            else:
                error = f"md5sum: {path}: No such file\n".encode()
                exit_code = 1
        else:
            raise AssertionError(f"unexpected fake SSH command: {command}")
        return (
            FakeExecStream(),
            FakeExecStream(output, exit_code=exit_code),
            FakeExecStream(error),
        )

    def close(self) -> None:
        self.close_count += 1
        self.transport.close()


class FakeClientFactory:
    def __init__(self, clients: list[FakeClient]) -> None:
        self.clients = list(clients)
        self.created: list[FakeClient] = []

    def __call__(self) -> FakeClient:
        if not self.clients:
            raise AssertionError("no fake SSH client queued")
        client = self.clients.pop(0)
        self.created.append(client)
        return client


class FakeSCP:
    def __init__(
        self,
        fs: FakeRemoteFS,
        upload_transform: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.fs = fs
        self.upload_transform = upload_transform or (lambda data: data)
        self.put_calls: list[tuple[str, str]] = []
        self.close_count = 0

    def put(self, local_path: str, *, remote_path: str) -> None:
        remote_path = self.fs.normalize(remote_path)
        self.put_calls.append((local_path, remote_path))
        data = Path(local_path).read_bytes()
        if remote_path in self.fs.dirs:
            remote_path = self.fs.normalize(
                posixpath.join(remote_path, Path(local_path).name)
            )
        self.fs.files[remote_path] = self.upload_transform(data)

    def close(self) -> None:
        self.close_count += 1


class FakeSCPFactory:
    def __init__(
        self,
        fs: FakeRemoteFS,
        upload_transform: Callable[[bytes], bytes] | None = None,
    ) -> None:
        self.fs = fs
        self.upload_transform = upload_transform
        self.transports: list[FakeTransport] = []
        self.created: list[FakeSCP] = []

    def __call__(self, transport: FakeTransport) -> FakeSCP:
        self.transports.append(transport)
        client = FakeSCP(self.fs, self.upload_transform)
        self.created.append(client)
        return client


def make_host(
    tmp_path: Path,
    clients: list[FakeClient],
    *,
    scp_factory: Callable[[Any], Any] | None = None,
    image_pattern_for: Callable[[str], str] | None = None,
) -> tuple[SSHHost, FakeRecorder, FakeClientFactory, Path]:
    package_dir = tmp_path / "packages"
    package_dir.mkdir(exist_ok=True)
    recorder = FakeRecorder()
    client_factory = FakeClientFactory(clients)
    host = SSHHost(
        name="primary",
        info=SSHConnectionInfo(
            host="ssh.example.test",
            port=2202,
            username="deploy",
            password="secret",
            connect_timeout=7,
        ),
        run_id="run-1",
        package_dir=package_dir,
        recorder=recorder,  # type: ignore[arg-type]
        image_pattern_for=image_pattern_for or (lambda name: rf"^{name}$"),
        client_factory=client_factory,
        scp_factory=scp_factory or (lambda _transport: None),
    )
    return host, recorder, client_factory, package_dir


def upload_rig(
    tmp_path: Path,
    *,
    sftp_transform: Callable[[bytes], bytes] | None = None,
    scp_transform: Callable[[bytes], bytes] | None = None,
    image_pattern_for: Callable[[str], str] | None = None,
) -> tuple[Any, ...]:
    fs = FakeRemoteFS()
    sftp = FakeSFTP(fs, sftp_transform)
    transport = FakeTransport()
    client = FakeClient(
        transport,
        sftp=sftp,
        sftp_factory=lambda: FakeSFTP(fs, sftp_transform),
        fs=fs,
    )
    scp_factory = FakeSCPFactory(fs, scp_transform)
    host, recorder, client_factory, package_dir = make_host(
        tmp_path,
        [client],
        scp_factory=scp_factory,
        image_pattern_for=image_pattern_for,
    )
    return (
        host,
        recorder,
        client_factory,
        package_dir,
        fs,
        sftp,
        scp_factory,
        client,
        transport,
    )


def test_defaults_are_normalized_and_connection_defaults_require_identity() -> None:
    assert SSHDefaults() == SSHDefaults(
        host="", port=22, username="root", password="", connect_timeout=30.0
    )
    assert SSHDefaults(
        host="  host.example  ",
        port=2222,
        username="  admin  ",
        password="  preserved  ",
        connect_timeout=3,
    ) == SSHDefaults(
        host="host.example",
        port=2222,
        username="admin",
        password="  preserved  ",
        connect_timeout=3.0,
    )
    assert SSHConnectionInfo(host=" host ", username=" user ").host == "host"


@pytest.mark.parametrize(
    ("factory", "kwargs", "error"),
    [
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
    ],
)
def test_defaults_validation(
    factory: Callable[..., Any], kwargs: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        factory(**kwargs)


def test_connection_is_lazy_and_reused_for_commands(tmp_path: Path) -> None:
    first = FakeChannel(stdout=(b"one",))
    second = FakeChannel(stdout=(b"two",))
    transport = FakeTransport([first, second])
    client = FakeClient(transport)
    host, recorder, factory, _ = make_host(tmp_path, [client])

    assert not host.connected
    assert factory.created == []
    assert host.execute("first").stdout == "one"
    assert host.execute("second").stdout == "two"

    assert factory.created == [client]
    assert len(client.connect_calls) == 1
    assert first.commands == ["first"]
    assert second.commands == ["second"]
    assert len(transport.open_timeouts) == 2
    assert isinstance(client.policies[0], paramiko.AutoAddPolicy)
    assert client.connect_calls[0] == {
        "hostname": "ssh.example.test",
        "port": 2202,
        "username": "deploy",
        "password": "secret",
        "timeout": 7.0,
        "banner_timeout": 7.0,
        "auth_timeout": 7.0,
        "allow_agent": False,
        "look_for_keys": False,
    }
    assert [item[0] for item in recorder.results] == ["SSH EXECUTE", "SSH EXECUTE"]


def test_inactive_transport_is_discarded_and_next_call_reconnects(tmp_path: Path) -> None:
    old_transport = FakeTransport([FakeChannel(stdout=(b"old",))])
    old_client = FakeClient(old_transport)
    new_channel = FakeChannel(stdout=(b"new",))
    new_client = FakeClient(FakeTransport([new_channel]))
    host, _, factory, _ = make_host(tmp_path, [old_client, new_client])

    assert host.execute("before").success
    old_transport.active = False
    result = host.execute("after")

    assert result.success and result.stdout == "new"
    assert factory.created == [old_client, new_client]
    assert old_client.close_count >= 1
    assert new_channel.commands == ["after"]


@pytest.mark.parametrize(
    ("exit_code", "status", "error_type"),
    [(0, CommandStatus.SUCCESS, None), (23, CommandStatus.COMMAND_FAILED, "NON_ZERO_EXIT_CODE")],
)
def test_execute_success_and_nonzero_exit(
    tmp_path: Path,
    exit_code: int,
    status: CommandStatus,
    error_type: str | None,
) -> None:
    channel = FakeChannel(
        stdout=(b"hello ", "世界".encode()), stderr=(b"warning\n",), exit_code=exit_code
    )
    host, recorder, _, _ = make_host(tmp_path, [FakeClient(FakeTransport([channel]))])

    result = host.execute("run it")

    assert result.status is status
    assert result.phase is CommandPhase.COMPLETE
    assert result.exit_code == exit_code
    assert result.stdout == "hello 世界"
    assert result.stderr == "warning\n"
    assert result.raw_output == "hello warning\n世界"
    assert result.error_type == error_type
    assert channel.close_count == 1
    assert recorder.streams == []
    assert recorder.results[-1] == ("SSH EXECUTE", result)


def test_execute_on_output_matches_across_chunks_and_sends_raw_bytes(
    tmp_path: Path,
) -> None:
    channel = FakeChannel(
        stdout=(b"rebooting\nPress Ctrl", b"+B to enter menu"),
        exit_code=None,
    )
    host, recorder, _, _ = make_host(
        tmp_path, [FakeClient(FakeTransport([channel]))]
    )

    result = host.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
        timeout=1,
    )

    assert result.status is CommandStatus.SUCCESS
    assert result.phase is CommandPhase.COMPLETE
    assert result.exit_code is None
    assert result.stdout == "rebooting\nPress Ctrl+B to enter menu"
    assert channel.commands == ["reboot"]
    assert channel.sent == [b"\x02"]
    assert channel.close_count == 1
    assert recorder.streams == []
    assert recorder.results[-1] == ("SSH EXECUTE", result)


def test_execute_on_output_keeps_ssh_transport_reusable(tmp_path: Path) -> None:
    triggered = FakeChannel(stdout=(b"Continue?",), exit_code=None)
    follow_up = FakeChannel(stdout=(b"healthy",))
    transport = FakeTransport([triggered, follow_up])
    client = FakeClient(transport)
    host, _, factory, _ = make_host(tmp_path, [client])

    first = host.execute_on_output(
        "installer",
        keyword="Continue?",
        send_data=b"y\n",
    )
    second = host.execute("health-check")

    assert first.success
    assert second.success and second.stdout == "healthy"
    assert factory.created == [client]
    assert triggered.sent == [b"y\n"]
    assert follow_up.commands == ["health-check"]


def test_execute_on_output_reports_command_exit_before_keyword(tmp_path: Path) -> None:
    channel = FakeChannel(stdout=(b"ordinary output",), exit_code=0)
    host, _, _, _ = make_host(
        tmp_path, [FakeClient(FakeTransport([channel]))]
    )

    result = host.execute_on_output(
        "short-command",
        keyword="never appears",
        send_data=b"response\n",
    )

    assert result.status is CommandStatus.COMMAND_FAILED
    assert result.phase is CommandPhase.COMPLETE
    assert result.exit_code == 0
    assert result.error_type == "KEYWORD_NOT_FOUND"
    assert channel.sent == []


def test_execute_on_output_reports_response_send_failure(tmp_path: Path) -> None:
    channel = FakeChannel(
        stdout=(b"Continue?",),
        exit_code=None,
        send_error=OSError(errno.EIO, "send failed"),
    )
    host, _, _, _ = make_host(
        tmp_path, [FakeClient(FakeTransport([channel]))]
    )

    result = host.execute_on_output(
        "installer",
        keyword="Continue?",
        send_data=b"y\n",
    )

    assert result.status is CommandStatus.PROTOCOL_ERROR
    assert result.phase is CommandPhase.SEND_COMMAND
    assert result.error_type == "RESPONSE_SEND_FAILED"
    assert "send failed" in (result.error_message or "")


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
    tmp_path: Path,
    kwargs: dict[str, Any],
    error: type[Exception],
) -> None:
    host, _, factory, _ = make_host(tmp_path, [FakeClient(FakeTransport())])

    with pytest.raises(error):
        host.execute_on_output("command", **kwargs)  # type: ignore[arg-type]

    assert factory.created == []


@pytest.mark.parametrize(
    ("failure", "status", "phase", "error_type"),
    [
        (OSError(errno.EHOSTUNREACH, "unreachable"), CommandStatus.CONNECTION_FAILED, CommandPhase.CONNECT, "CONNECTION_FAILED"),
        (paramiko.AuthenticationException("bad credentials"), CommandStatus.AUTH_FAILED, CommandPhase.AUTHENTICATE, "AUTHENTICATION_FAILED"),
        (socket.timeout("connect took too long"), CommandStatus.CONNECTION_FAILED, CommandPhase.CONNECT, "CONNECTION_TIMEOUT"),
    ],
)
def test_execute_connection_authentication_and_connect_timeout_failures(
    tmp_path: Path,
    failure: BaseException,
    status: CommandStatus,
    phase: CommandPhase,
    error_type: str,
) -> None:
    client = FakeClient(FakeTransport(), connect_error=failure)
    host, _, factory, _ = make_host(tmp_path, [client])

    result = host.execute("never sent")

    assert result.status is status
    assert result.phase is phase
    assert result.error_type == error_type
    assert result.exit_code is None
    assert client.close_count >= 1
    assert factory.created == [client]


def test_command_timeout_keeps_partial_output_and_never_replays(tmp_path: Path) -> None:
    channel = FakeChannel(
        stdout=(b"partial",),
        exit_code=None,
        recv_ready_error=socket.timeout("read timed out"),
    )
    client = FakeClient(FakeTransport([channel]))
    host, _, factory, _ = make_host(tmp_path, [client])

    result = host.execute("mutating-command")

    assert result.status is CommandStatus.TIMEOUT
    assert result.phase is CommandPhase.WAIT_OUTPUT
    assert result.error_type == "COMMAND_TIMEOUT"
    assert result.stdout == "partial"
    assert channel.commands == ["mutating-command"]
    assert factory.created == [client]


def test_io_error_after_command_send_is_not_reported_as_connection_failure(
    tmp_path: Path,
) -> None:
    channel = FakeChannel(
        stdout=(b"accepted",),
        exit_code=None,
        recv_ready_error=OSError(errno.EIO, "channel io failed"),
    )
    host, _, _, _ = make_host(
        tmp_path, [FakeClient(FakeTransport([channel]))]
    )

    result = host.execute("apply-once")

    assert result.status is CommandStatus.PROTOCOL_ERROR
    assert result.phase is CommandPhase.WAIT_OUTPUT
    assert result.error_type == "SSH_IO_ERROR_AFTER_SEND"
    assert result.stdout == "accepted"
    assert channel.commands == ["apply-once"]


def test_open_session_timeout_is_send_timeout_not_connection_timeout(tmp_path: Path) -> None:
    class TimeoutTransport(FakeTransport):
        def open_session(self, timeout: float):
            self.open_timeouts.append(timeout)
            raise socket.timeout("session timeout")

    host, _, _, _ = make_host(tmp_path, [FakeClient(TimeoutTransport())])

    result = host.execute("never-submitted")

    assert result.status is CommandStatus.TIMEOUT
    assert result.phase is CommandPhase.SEND_COMMAND
    assert result.error_type == "SEND_TIMEOUT"


@pytest.mark.parametrize(
    ("expect_disconnect", "expected_status", "expected_error"),
    [(False, CommandStatus.DISCONNECTED, "CONNECTION_LOST"), (True, CommandStatus.SUCCESS, None)],
)
def test_disconnect_preserves_partial_output_and_honors_expect_disconnect(
    tmp_path: Path,
    expect_disconnect: bool,
    expected_status: CommandStatus,
    expected_error: str | None,
) -> None:
    channel = FakeChannel(
        stdout=(b"started\n",),
        stderr=(b"rebooting\n",),
        exit_code=None,
        disconnect_when_drained=True,
    )
    client = FakeClient(FakeTransport([channel]))
    host, _, _, _ = make_host(tmp_path, [client])

    result = host.execute("reboot", expect_disconnect=expect_disconnect)

    assert result.status is expected_status
    assert result.error_type == expected_error
    assert result.stdout == "started\n"
    assert result.stderr == "rebooting\n"
    assert result.disconnected
    assert result.expected_disconnect is expect_disconnect
    assert client.close_count >= 1


def test_disconnect_invalidates_but_does_not_replay_on_reconnect(tmp_path: Path) -> None:
    disconnected = FakeChannel(
        stdout=(b"accepted",), exit_code=None, disconnect_when_drained=True
    )
    first_client = FakeClient(FakeTransport([disconnected]))
    next_channel = FakeChannel(stdout=(b"healthy",))
    second_client = FakeClient(FakeTransport([next_channel]))
    host, _, factory, _ = make_host(tmp_path, [first_client, second_client])

    first_result = host.execute("apply-once")
    second_result = host.execute("health-check")

    assert first_result.status is CommandStatus.DISCONNECTED
    assert second_result.success
    assert factory.created == [first_client, second_client]
    assert disconnected.commands == ["apply-once"]
    assert next_channel.commands == ["health-check"]
    assert "apply-once" not in next_channel.commands


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_single_file_upload_creates_remote_tree_and_verifies_md5(
    tmp_path: Path, protocol: str
) -> None:
    host, recorder, _, package_dir, fs, sftp, scp_factory, client, transport = upload_rig(
        tmp_path
    )
    content = b"release payload\x00\xff"
    local_file = package_dir / "release.bin"
    local_file.write_bytes(content)

    result = getattr(host, f"{protocol}_upload")(
        extra_file("release.bin"), r"\opt\apps\v1"
    )

    assert result.success and result.status == "success"
    assert result.selector_type == "extra_file"
    assert result.remote_dir == "/opt/apps/v1"
    assert result.remote_file == "/opt/apps/v1/release.bin"
    assert result.local_md5 == md5(content)
    assert result.remote_md5_before is None
    assert result.remote_md5_after == md5(content)
    assert result.md5_changed is True
    assert result.md5_verified is True
    assert fs.files[result.remote_file] == content
    assert fs.mkdir_calls == ["/opt", "/opt/apps", "/opt/apps/v1"]
    if protocol == "scp":
        assert scp_factory.transports == [transport]
        assert scp_factory.created[0].put_calls == [
            (str(local_file.resolve()), result.remote_dir)
        ]
        assert sftp.put_calls == []
        assert scp_factory.created[0].close_count == 1
        assert client.open_sftp_count == 0
        assert len(client.sftp_sessions) == 1
        assert client.exec_calls == [
            ("mkdir -p /opt/apps/v1", 7.0),
            (
                "if [ -e /opt/apps/v1/release.bin ]; "
                "then printf 1; else printf 0; fi",
                7.0,
            ),
            ("md5sum /opt/apps/v1/release.bin", 7.0),
        ]
    else:
        assert sftp.put_calls == [(str(local_file.resolve()), result.remote_file)]
        assert scp_factory.created == []
    assert recorder.results[-1] == (f"{protocol.upper()} UPLOAD", result)


def test_scp_upload_does_not_require_or_open_sftp(tmp_path: Path) -> None:
    fs = FakeRemoteFS()
    transport = FakeTransport()
    client = FakeClient(transport, fs=fs)
    scp_factory = FakeSCPFactory(fs)
    host, _, _, package_dir = make_host(
        tmp_path,
        [client],
        scp_factory=scp_factory,
    )
    local_file = package_dir / "artifact.bin"
    local_file.write_bytes(b"payload")

    result = host.scp_upload(extra_file("artifact.bin"), "/release")

    assert result.success
    assert client.open_sftp_count == 0
    assert fs.files["/release/artifact.bin"] == b"payload"


@pytest.mark.parametrize(
    ("selector", "expected_type", "expected_value", "expected_file"),
    [
        (package("api"), "package", "api", "api-2.4.tgz"),
        (match(r"\.cfg$"), "match", r"\.cfg$", "settings.cfg"),
    ],
)
def test_package_and_match_selectors_choose_package_root_files(
    tmp_path: Path,
    selector: Any,
    expected_type: str,
    expected_value: str,
    expected_file: str,
) -> None:
    host, _, _, package_dir, fs, _, _, _, _ = upload_rig(
        tmp_path, image_pattern_for=lambda name: rf"^{name}-\d+\.\d+\.tgz$"
    )
    (package_dir / "api-2.4.tgz").write_bytes(b"package")
    (package_dir / "settings.cfg").write_bytes(b"config")
    (package_dir / "ignored.part").write_bytes(b"partial")

    result = host.sftp_upload(selector, "/incoming")

    assert result.success
    assert result.selector_type == expected_type
    assert result.selector == expected_value
    assert result.resolved_local_file == str((package_dir / expected_file).resolve())
    assert fs.files[f"/incoming/{expected_file}"] == (package_dir / expected_file).read_bytes()


def test_execute_resolves_successfully_uploaded_package_to_actual_filename(
    tmp_path: Path,
) -> None:
    host, _, _, package_dir, _, _, _, _, transport = upload_rig(
        tmp_path, image_pattern_for=lambda _name: r"^api-\d+\.tgz$"
    )
    (package_dir / "api-24.tgz").write_bytes(b"package")
    assert host.sftp_upload(package("api"), "/incoming").success
    channel = FakeChannel()
    transport.channels.append(channel)

    result = host.execute("tar -tf /incoming/S{api}")

    assert result.success
    assert result.command == "tar -tf /incoming/api-24.tgz"
    assert channel.commands == ["tar -tf /incoming/api-24.tgz"]


def test_failed_upload_does_not_enable_command_placeholder(tmp_path: Path) -> None:
    host, _, _, package_dir, _, _, _, _, _ = upload_rig(
        tmp_path, sftp_transform=lambda _data: b"corrupt"
    )
    (package_dir / "artifact.bin").write_bytes(b"package")
    assert not host.sftp_upload(extra_file("artifact.bin"), "/incoming").success

    with pytest.raises(ValueError, match="successfully uploaded file"):
        host.execute("sha256sum S{artifact.bin}")


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_overwrite_true_reports_md5_before_and_after(
    tmp_path: Path, protocol: str
) -> None:
    host, _, _, package_dir, fs, sftp, scp_factory, _, _ = upload_rig(tmp_path)
    old = b"old content"
    new = b"new content"
    fs.seed_file("/releases/artifact.bin", old)
    (package_dir / "artifact.bin").write_bytes(new)

    result = getattr(host, f"{protocol}_upload")(
        extra_file("artifact.bin"), "/releases", overwrite=True
    )

    assert result.success
    assert result.remote_existed is True
    assert result.remote_md5_before == md5(old)
    assert result.remote_md5_after == md5(new)
    assert result.local_md5 == md5(new)
    assert result.md5_changed is True
    assert result.md5_verified is True
    assert fs.files["/releases/artifact.bin"] == new
    put_count = len(sftp.put_calls) + sum(len(item.put_calls) for item in scp_factory.created)
    assert put_count == 1


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_overwrite_false_does_not_transfer_existing_file(
    tmp_path: Path, protocol: str
) -> None:
    host, _, _, package_dir, fs, sftp, scp_factory, _, _ = upload_rig(tmp_path)
    existing = b"do not replace"
    fs.seed_file("/releases/artifact.bin", existing)
    (package_dir / "artifact.bin").write_bytes(b"replacement")

    result = getattr(host, f"{protocol}_upload")(
        extra_file("artifact.bin"), "/releases", overwrite=False
    )

    assert not result.success
    assert result.status == "remote_file_exists"
    assert result.error_type == "REMOTE_FILE_EXISTS"
    assert result.remote_existed is True
    assert result.remote_md5_before == md5(existing)
    assert result.remote_md5_after is None
    assert result.md5_changed is None
    assert result.md5_verified is False
    assert fs.files["/releases/artifact.bin"] == existing
    assert sftp.put_calls == []
    assert scp_factory.created == []


@pytest.mark.parametrize("protocol", ["scp", "sftp"])
def test_md5_verification_failure_reports_corrupt_remote_digest(
    tmp_path: Path, protocol: str
) -> None:
    transform = lambda _data: b"corrupted in transit"
    host, _, _, package_dir, fs, _, _, _, _ = upload_rig(
        tmp_path,
        sftp_transform=transform if protocol == "sftp" else None,
        scp_transform=transform if protocol == "scp" else None,
    )
    local = b"correct payload"
    (package_dir / "artifact.bin").write_bytes(local)

    result = getattr(host, f"{protocol}_upload")(
        extra_file("artifact.bin"), "/releases"
    )

    assert not result.success
    assert result.status == "md5_verification_failed"
    assert result.error_type == "MD5_VERIFICATION_FAILED"
    assert result.local_md5 == md5(local)
    assert result.remote_md5_after == md5(b"corrupted in transit")
    assert result.md5_changed is True
    assert result.md5_verified is False
    assert fs.files["/releases/artifact.bin"] == b"corrupted in transit"


def test_local_md5_read_failure_is_not_reported_as_connection_failure(
    tmp_path: Path, monkeypatch
) -> None:
    host, _, factory, package_dir, _, _, _, _, _ = upload_rig(tmp_path)
    (package_dir / "artifact.bin").write_bytes(b"payload")
    monkeypatch.setattr(
        host,
        "_local_md5",
        lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
    )

    result = host.sftp_upload(extra_file("artifact.bin"), "/release")

    assert result.status == "local_file_read_failed"
    assert result.error_type == "LOCAL_FILE_READ_FAILED"
    assert factory.created == []


def test_close_closes_all_cached_resources_once_and_prevents_reconnect(
    tmp_path: Path,
) -> None:
    host, _, factory, package_dir, _, sftp, scp_factory, client, transport = upload_rig(tmp_path)
    (package_dir / "artifact.bin").write_bytes(b"payload")
    assert host.scp_upload(extra_file("artifact.bin"), "/release").success
    scp = scp_factory.created[0]
    assert host.connected

    host.close()
    close_counts = (
        scp.close_count,
        sftp.close_count,
        client.close_count,
        transport.close_count,
    )
    host.close()

    assert not host.connected
    # The SCP path never opens SFTP, so there is no SFTP resource to close.
    assert close_counts[0:3] == (1, 0, 1)
    assert close_counts[3] >= 1
    assert (
        scp.close_count,
        sftp.close_count,
        client.close_count,
        transport.close_count,
    ) == close_counts
    result = host.execute("must not connect")
    assert result.status is CommandStatus.CONNECTION_FAILED
    assert result.error_type == "SSH_HOST_CLOSED"
    assert factory.created == [client]
