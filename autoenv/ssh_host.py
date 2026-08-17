from __future__ import annotations

import codecs
import errno
import fnmatch
import hashlib
import json
import math
import os
import posixpath
import shlex
import socket
import stat
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import paramiko

from .command_files import UploadedFileRegistry
from .recorder import RunRecorder
from .results import (
    CommandPhase,
    CommandProtocol,
    CommandResult,
    CommandStatus,
    UploadResult,
    RemoteDownloadResult,
    RemoteBatchDownloadResult,
    RemoteDownloadedFile,
)
from .selectors import (
    LocalFileSelector,
    SelectorResolutionError,
    describe_selector,
    resolve_local_file,
)


_READ_SIZE = 64 * 1024
_POLL_INTERVAL = 0.01


def _default_scp_factory(transport: Any) -> Any:
    # SCP is only needed by scp_upload(); keep SSH/SFTP usable when the optional
    # package has not yet been installed in a development/test environment.
    from scp import SCPClient

    return SCPClient(transport)


def _text(value: str, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _password(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("SSH password must be a string")
    return value


def _port(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("SSH port must be an integer")
    if not 1 <= value <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    return value


def _timeout(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be a finite number greater than zero")
    return normalized


@dataclass(frozen=True)
class SSHDefaults:
    host: str = ""
    port: int = 22
    username: str = "root"
    password: str = ""
    connect_timeout: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", _text(self.host, "SSH host", allow_empty=True))
        object.__setattr__(self, "port", _port(self.port))
        object.__setattr__(
            self,
            "username",
            _text(self.username, "SSH username", allow_empty=True),
        )
        object.__setattr__(self, "password", _password(self.password))
        object.__setattr__(
            self,
            "connect_timeout",
            _timeout(self.connect_timeout, "SSH connect_timeout"),
        )


@dataclass(frozen=True)
class SSHConnectionInfo:
    host: str
    port: int = 22
    username: str = "root"
    password: str = ""
    connect_timeout: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "host", _text(self.host, "SSH host"))
        object.__setattr__(self, "port", _port(self.port))
        object.__setattr__(self, "username", _text(self.username, "SSH username"))
        object.__setattr__(self, "password", _password(self.password))
        object.__setattr__(
            self,
            "connect_timeout",
            _timeout(self.connect_timeout, "SSH connect_timeout"),
        )


class _HostClosedError(RuntimeError):
    pass


class _RemoteDownloadError(RuntimeError):
    def __init__(self, status: str, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type


class SSHHost:
    """A lazily connected SSH target owned by one RunContext."""

    def __init__(
        self,
        *,
        name: str,
        info: SSHConnectionInfo,
        run_id: str,
        package_dir: Path,
        recorder: RunRecorder,
        image_pattern_for: Callable[[str], str],
        uploaded_files: UploadedFileRegistry | None = None,
        client_factory: Callable[[], Any] = paramiko.SSHClient,
        scp_factory: Callable[[Any], Any] = _default_scp_factory,
    ) -> None:
        self.name = _text(name, "SSH host name")
        if not isinstance(info, SSHConnectionInfo):
            raise TypeError("info must be SSHConnectionInfo")
        self.info = info
        self.run_id = _text(run_id, "run_id")
        self.package_dir = Path(package_dir).resolve()
        if not callable(image_pattern_for):
            raise TypeError("image_pattern_for must be callable")
        if not callable(client_factory):
            raise TypeError("client_factory must be callable")
        if not callable(scp_factory):
            raise TypeError("scp_factory must be callable")
        self.recorder = recorder
        self.image_pattern_for = image_pattern_for
        if uploaded_files is not None and not isinstance(uploaded_files, UploadedFileRegistry):
            raise TypeError("uploaded_files must be UploadedFileRegistry")
        self.uploaded_files = uploaded_files or UploadedFileRegistry()
        self._client_factory = client_factory
        self._scp_factory = scp_factory
        self._client: Any | None = None
        self._transport: Any | None = None
        self._sftp: Any | None = None
        self._scp: Any | None = None
        self._closed = False

    @property
    def connected(self) -> bool:
        return self._transport is not None and self._transport_is_active(self._transport)

    def __enter__(self) -> "SSHHost":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def execute(
        self,
        command: str,
        timeout: float = 300.0,
        expect_disconnect: bool = False,
    ) -> CommandResult:
        return self._execute(
            command,
            timeout=timeout,
            expect_disconnect=expect_disconnect,
        )

    def execute_on_output(
        self,
        command: str,
        *,
        keyword: str,
        send_data: bytes,
        timeout: float = 300.0,
    ) -> CommandResult:
        """Run a command, then block until output matches and send raw bytes."""
        if not isinstance(keyword, str):
            raise TypeError("keyword must be a string")
        if not keyword:
            raise ValueError("keyword must not be empty")
        if not isinstance(send_data, bytes):
            raise TypeError("send_data must be bytes")
        if not send_data:
            raise ValueError("send_data must not be empty")
        return self._execute(
            command,
            timeout=timeout,
            expect_disconnect=False,
            keyword=keyword,
            send_data=send_data,
        )

    def _execute(
        self,
        command: str,
        *,
        timeout: float,
        expect_disconnect: bool,
        keyword: str | None = None,
        send_data: bytes | None = None,
        console: bool = True,
    ) -> CommandResult:
        command = self.uploaded_files.resolve(command, target_name=self.name)
        timeout = _timeout(timeout, "SSH command timeout")
        if not isinstance(expect_disconnect, bool):
            raise TypeError("expect_disconnect must be a boolean")

        operation_id = self.recorder.next_operation_id()
        started_at = datetime.now().astimezone()
        started_clock = time.monotonic()
        phase = CommandPhase.CONNECT
        status = CommandStatus.PROTOCOL_ERROR
        exit_code: int | None = None
        error_type: str | None = None
        error_message: str | None = None
        disconnected = False
        command_sent = False
        connected = False
        channel: Any | None = None
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        raw_parts: list[str] = []
        stdout_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        stderr_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        match_tails = {"stdout": "", "stderr": ""}
        response_send_attempted = False
        response_sent = False

        def receive(stream: str, data: bytes) -> None:
            nonlocal response_send_attempted, response_sent
            decoder = stdout_decoder if stream == "stdout" else stderr_decoder
            text = decoder.decode(data, final=False)
            if not text:
                return
            if stream == "stdout":
                stdout_parts.append(text)
            else:
                stderr_parts.append(text)
            raw_parts.append(text)
            # Internal discovery commands may emit hundreds of NUL-delimited
            # file records.  Preserve them in run.log without flooding the Web
            # event stream; explicit user commands keep streaming by default.
            self.recorder.stream(
                f"SSH {self.name} {stream.upper()}",
                text,
                console=console,
            )
            if keyword is not None and not response_sent:
                candidate = match_tails[stream] + text
                if keyword in candidate:
                    response_send_attempted = True
                    assert channel is not None and send_data is not None
                    channel.sendall(send_data)
                    response_sent = True
                keep = max(0, len(keyword) - 1)
                match_tails[stream] = candidate[-keep:] if keep else ""

        try:
            transport = self._ensure_transport()
            connected = True
            phase = CommandPhase.SEND_COMMAND
            remaining = max(0.001, timeout - (time.monotonic() - started_clock))
            channel = transport.open_session(timeout=remaining)
            channel.exec_command(command)
            command_sent = True
            phase = CommandPhase.WAIT_OUTPUT

            while True:
                made_progress = False
                if channel.recv_ready():
                    chunk = channel.recv(_READ_SIZE)
                    if not chunk:
                        disconnected = True
                    else:
                        receive("stdout", chunk)
                        made_progress = True
                if channel.recv_stderr_ready():
                    chunk = channel.recv_stderr(_READ_SIZE)
                    if not chunk:
                        disconnected = True
                    else:
                        receive("stderr", chunk)
                        made_progress = True

                if response_sent:
                    phase = CommandPhase.COMPLETE
                    status = CommandStatus.SUCCESS
                    break

                if channel.exit_status_ready():
                    # Paramiko may announce exit before the caller has drained all data.
                    if channel.recv_ready() or channel.recv_stderr_ready():
                        continue
                    phase = CommandPhase.PARSE_RESULT
                    exit_code = channel.recv_exit_status()
                    if keyword is not None:
                        status = CommandStatus.COMMAND_FAILED
                        error_type = "KEYWORD_NOT_FOUND"
                        error_message = (
                            f"SSH command exited before output keyword {keyword!r} "
                            "was found"
                        )
                    else:
                        status = (
                            CommandStatus.SUCCESS
                            if exit_code == 0
                            else CommandStatus.COMMAND_FAILED
                        )
                    if keyword is None and exit_code != 0:
                        error_type = "NON_ZERO_EXIT_CODE"
                        error_message = f"remote command exited with code {exit_code}"
                    phase = CommandPhase.COMPLETE
                    break

                if disconnected or getattr(channel, "closed", False):
                    disconnected = True
                    raise EOFError("SSH channel closed before an exit status was received")
                if not self._transport_is_active(transport):
                    disconnected = True
                    raise EOFError("SSH transport disconnected while command was running")
                if time.monotonic() - started_clock >= timeout:
                    status = CommandStatus.TIMEOUT
                    if keyword is None:
                        error_type = "COMMAND_TIMEOUT"
                        error_message = f"SSH command timed out after {timeout:g} seconds"
                    else:
                        error_type = "KEYWORD_NOT_FOUND"
                        error_message = (
                            f"SSH output keyword {keyword!r} was not found within "
                            f"{timeout:g} seconds"
                        )
                    break
                if not made_progress:
                    time.sleep(_POLL_INTERVAL)
        except paramiko.AuthenticationException as exc:
            status = CommandStatus.AUTH_FAILED
            phase = CommandPhase.AUTHENTICATE
            error_type = "AUTHENTICATION_FAILED"
            error_message = str(exc) or "SSH authentication failed"
            self._invalidate_connection()
        except (socket.timeout, TimeoutError) as exc:
            if response_send_attempted and not response_sent:
                status = CommandStatus.PROTOCOL_ERROR
                phase = CommandPhase.SEND_COMMAND
                error_type = "RESPONSE_SEND_FAILED"
            elif command_sent:
                status = CommandStatus.TIMEOUT
                phase = CommandPhase.WAIT_OUTPUT
                error_type = (
                    "KEYWORD_NOT_FOUND" if keyword is not None else "COMMAND_TIMEOUT"
                )
            elif connected:
                status = CommandStatus.TIMEOUT
                phase = CommandPhase.SEND_COMMAND
                error_type = "SEND_TIMEOUT"
            else:
                status = CommandStatus.CONNECTION_FAILED
                phase = CommandPhase.CONNECT
                error_type = "CONNECTION_TIMEOUT"
                self._invalidate_connection()
            error_message = str(exc) or "SSH operation timed out"
        except (EOFError, ConnectionError) as exc:
            disconnected = command_sent
            if command_sent and expect_disconnect:
                status = CommandStatus.SUCCESS
                phase = CommandPhase.COMPLETE
                error_type = None
                error_message = None
            else:
                status = (
                    CommandStatus.DISCONNECTED
                    if command_sent
                    else CommandStatus.CONNECTION_FAILED
                )
                error_type = "CONNECTION_LOST" if command_sent else "CONNECTION_FAILED"
                error_message = str(exc) or "SSH connection was lost"
            self._invalidate_connection()
        except (paramiko.ssh_exception.NoValidConnectionsError, OSError) as exc:
            if response_send_attempted and not response_sent:
                status = CommandStatus.PROTOCOL_ERROR
                phase = CommandPhase.SEND_COMMAND
                error_type = "RESPONSE_SEND_FAILED"
                error_message = str(exc) or "SSH response bytes could not be sent"
            elif command_sent:
                if self._is_disconnect_exception(exc) or self._looks_disconnected(exc):
                    disconnected = True
                    if expect_disconnect:
                        status = CommandStatus.SUCCESS
                        phase = CommandPhase.COMPLETE
                        error_type = None
                        error_message = None
                    else:
                        status = CommandStatus.DISCONNECTED
                        phase = CommandPhase.WAIT_OUTPUT
                        error_type = "CONNECTION_LOST"
                        error_message = str(exc) or "SSH connection was lost"
                else:
                    status = CommandStatus.PROTOCOL_ERROR
                    phase = CommandPhase.WAIT_OUTPUT
                    error_type = "SSH_IO_ERROR_AFTER_SEND"
                    error_message = str(exc) or "SSH I/O failed after command submission"
            elif connected:
                status = CommandStatus.PROTOCOL_ERROR
                phase = CommandPhase.SEND_COMMAND
                error_type = "SEND_FAILED"
                error_message = str(exc) or "SSH command could not be sent"
            else:
                status = CommandStatus.CONNECTION_FAILED
                phase = CommandPhase.CONNECT
                error_type = "CONNECTION_FAILED"
                error_message = str(exc) or "SSH connection failed"
            self._invalidate_connection()
        except paramiko.SSHException as exc:
            if response_send_attempted and not response_sent:
                status = CommandStatus.PROTOCOL_ERROR
                phase = CommandPhase.SEND_COMMAND
                error_type = "RESPONSE_SEND_FAILED"
                error_message = str(exc) or "SSH response bytes could not be sent"
            elif command_sent and self._looks_disconnected(exc):
                disconnected = True
                if expect_disconnect:
                    status = CommandStatus.SUCCESS
                    phase = CommandPhase.COMPLETE
                    error_type = None
                    error_message = None
                else:
                    status = CommandStatus.DISCONNECTED
                    error_type = "CONNECTION_LOST"
                    error_message = str(exc) or "SSH connection was lost"
            else:
                status = CommandStatus.PROTOCOL_ERROR
                error_type = "SSH_PROTOCOL_ERROR"
                error_message = str(exc) or "SSH protocol error"
            self._invalidate_connection()
        except _HostClosedError as exc:
            status = CommandStatus.CONNECTION_FAILED
            phase = CommandPhase.CONNECT
            error_type = "SSH_HOST_CLOSED"
            error_message = str(exc)
        except Exception as exc:  # A malformed/failed channel is a protocol failure.
            status = CommandStatus.PROTOCOL_ERROR
            if response_send_attempted and not response_sent:
                phase = CommandPhase.SEND_COMMAND
                error_type = "RESPONSE_SEND_FAILED"
            else:
                error_type = type(exc).__name__
            error_message = str(exc) or repr(exc)
            self._invalidate_connection()
        finally:
            for stream, decoder, parts in (
                ("stdout", stdout_decoder, stdout_parts),
                ("stderr", stderr_decoder, stderr_parts),
            ):
                tail = decoder.decode(b"", final=True)
                if tail:
                    parts.append(tail)
                    raw_parts.append(tail)
                    self.recorder.stream(
                        f"SSH {self.name} {stream.upper()}",
                        tail,
                        console=console,
                    )
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass

        finished_at = datetime.now().astimezone()
        result = CommandResult(
            run_id=self.run_id,
            operation_id=operation_id,
            protocol=CommandProtocol.SSH,
            target_name=self.name,
            command=command,
            status=status,
            phase=phase,
            exit_code=exit_code,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            raw_output="".join(raw_parts),
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
            error_type=error_type,
            error_message=error_message,
            expected_disconnect=expect_disconnect,
            disconnected=disconnected,
        )
        self.recorder.record_result("SSH EXECUTE", result, console=console)
        return result

    def scp_upload(
        self,
        local_file: LocalFileSelector,
        remote_dir: str,
        overwrite: bool = True,
    ) -> UploadResult:
        return self._upload("scp", local_file, remote_dir, overwrite)

    def sftp_upload(
        self,
        local_file: LocalFileSelector,
        remote_dir: str,
        overwrite: bool = True,
    ) -> UploadResult:
        return self._upload("sftp", local_file, remote_dir, overwrite)

    def scp_download(
        self,
        remote_dir: str,
        *,
        remote_file: str | None = None,
        pattern: str | None = None,
        overwrite: bool = True,
    ) -> RemoteDownloadResult:
        return self._download("scp", remote_dir, remote_file, pattern, overwrite)

    def sftp_download(
        self,
        remote_dir: str,
        *,
        remote_file: str | None = None,
        pattern: str | None = None,
        overwrite: bool = True,
    ) -> RemoteDownloadResult:
        return self._download("sftp", remote_dir, remote_file, pattern, overwrite)

    def scp_download_many(
        self,
        remote_dir: str,
        *,
        glob: str,
        destination: Path | str,
    ) -> RemoteBatchDownloadResult:
        """Download every basename matched by a glob into an empty run artifact directory.

        Enumeration is intentionally non-recursive.  Higher-level workflows
        select each remote directory explicitly, which keeps the collection
        scope reviewable and avoids unexpectedly walking large filesystem trees.
        """

        remote_dir = self._normalize_remote_dir(remote_dir)
        glob = _text(glob, "glob")
        destination_path = Path(destination).resolve()
        run_dir = self.package_dir.parent.resolve()
        try:
            destination_path.relative_to(run_dir)
        except ValueError as exc:
            raise ValueError("destination must be inside the current run directory") from exc
        if destination_path == run_dir:
            raise ValueError("destination must be a child of the current run directory")
        if destination_path.exists() and any(destination_path.iterdir()):
            raise ValueError("destination must be empty")
        destination_path.mkdir(parents=True, exist_ok=True)

        operation_id = self.recorder.next_operation_id()
        started = datetime.now().astimezone()
        downloaded: list[RemoteDownloadedFile] = []
        matched_count = 0
        completed_count = 0
        success = False
        status = "download_failed"
        error_type: str | None = None
        error_message: str | None = None
        self.recorder.log(
            "SCP BATCH START "
            f"operation_id={operation_id} remote_dir={remote_dir!r} "
            f"glob={glob!r} destination={str(destination_path)!r}"
        )
        try:
            self._ensure_transport()
            entries = self._list_remote_file_metadata(remote_dir)
            # Stable chronological ordering is captured in the result and later
            # reused by LogCollection after archives are expanded.  Filename is
            # a deterministic tie breaker for coarse/equal remote mtimes.
            matches = sorted(
                (item for item in entries if fnmatch.fnmatchcase(item[0], glob)),
                key=lambda item: (item[2], item[0].casefold(), item[0]),
            )
            matched_count = len(matches)
            self.recorder.log(
                "SCP BATCH MATCHED "
                f"operation_id={operation_id} count={matched_count}"
            )
            if not matches:
                raise _RemoteDownloadError(
                    "remote_file_not_found",
                    "REMOTE_FILE_NOT_FOUND",
                    f"no file in {remote_dir!r} matched glob {glob!r}",
                )
            names = [item[0] for item in matches]
            if len(set(names)) != len(names):
                raise _RemoteDownloadError(
                    "remote_list_failed",
                    "REMOTE_LIST_FAILED",
                    "remote file enumeration returned duplicate basenames",
                )
            scp_client = self._get_scp()
            try:
                self.recorder.log(
                    "SCP BATCH PROGRESS "
                    f"operation_id={operation_id} completed=0 total={matched_count}"
                )
                for index, (name, size, mtime) in enumerate(matches, start=1):
                    remote_path = self._remote_file(remote_dir, name)
                    local_path = (destination_path / name).resolve()
                    local_path.relative_to(destination_path)
                    # Never expose a truncated file under its final name.  Size
                    # verification happens on .part, followed by an atomic local
                    # replace inside the same destination directory.
                    pending = local_path.with_name(local_path.name + ".part")
                    pending.unlink(missing_ok=True)
                    self._record_batch_file_event(
                        operation_id=operation_id,
                        event="start",
                        index=index,
                        total=matched_count,
                        name=name,
                        remote_path=remote_path,
                        local_path=local_path,
                        size=size,
                    )
                    try:
                        scp_client.get(remote_path, local_path=str(pending))
                        if pending.stat().st_size != size:
                            raise _RemoteDownloadError(
                                "size_verification_failed",
                                "SIZE_VERIFICATION_FAILED",
                                f"downloaded size mismatch for {name}: remote={size}, local={pending.stat().st_size}",
                            )
                        pending.replace(local_path)
                        os.utime(local_path, (mtime, mtime))
                        completed_count += 1
                        downloaded.append(
                            RemoteDownloadedFile(
                                name=name,
                                remote_file=remote_path,
                                local_file=str(local_path),
                                remote_size=size,
                                remote_mtime=mtime,
                            )
                        )
                        self._record_batch_file_event(
                            operation_id=operation_id,
                            event="complete",
                            index=index,
                            total=matched_count,
                            name=name,
                            remote_path=remote_path,
                            local_path=local_path,
                            size=size,
                        )
                        # The Web bridge recognizes this structured line and
                        # updates one progress bar in place.  It is also flushed
                        # to run.log, so an interrupted batch retains its exact
                        # last completed count without printing file names.
                        self.recorder.log(
                            "SCP BATCH PROGRESS "
                            f"operation_id={operation_id} "
                            f"completed={completed_count} total={matched_count}"
                        )
                    except Exception as exc:
                        self._record_batch_file_event(
                            operation_id=operation_id,
                            event="failed",
                            index=index,
                            total=matched_count,
                            name=name,
                            remote_path=remote_path,
                            local_path=local_path,
                            size=size,
                            error=exc,
                        )
                        raise
                    finally:
                        pending.unlink(missing_ok=True)
            finally:
                self._close_scp()
            success, status = True, "success"
        except _RemoteDownloadError as exc:
            status, error_type, error_message = exc.status, exc.error_type, str(exc)
        except paramiko.AuthenticationException as exc:
            status, error_type, error_message = "auth_failed", "AUTHENTICATION_FAILED", str(exc) or "SSH authentication failed"
            self._invalidate_connection()
        except (socket.timeout, TimeoutError) as exc:
            status, error_type, error_message = "connection_timeout", "CONNECTION_TIMEOUT", str(exc) or "SSH operation timed out"
            self._invalidate_connection()
        except Exception as exc:
            status, error_type, error_message = "download_failed", type(exc).__name__, str(exc) or repr(exc)
        if not success:
            # Batch semantics are all-or-nothing for one remote directory.  The
            # multi-directory layer applies the same rule across these batches.
            for item in downloaded:
                Path(item.local_file).unlink(missing_ok=True)
            downloaded.clear()
        finished = datetime.now().astimezone()
        result = RemoteBatchDownloadResult(
            run_id=self.run_id,
            operation_id=operation_id,
            protocol="scp",
            target_name=self.name,
            remote_dir=remote_dir,
            glob=glob,
            success=success,
            status=status,
            started_at=started,
            finished_at=finished,
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
            destination=str(destination_path),
            files=tuple(downloaded),
            matched_count=matched_count,
            completed_count=completed_count,
            error_type=error_type,
            error_message=error_message,
        )
        self.recorder.record_result("SCP BATCH DOWNLOAD", result)
        return result

    def _record_batch_file_event(
        self,
        *,
        operation_id: str,
        event: str,
        index: int,
        total: int,
        name: str,
        remote_path: str,
        local_path: Path,
        size: int,
        error: Exception | None = None,
    ) -> None:
        """Write per-file transfer evidence to run.log, never to the console."""

        detail: dict[str, object] = {
            "operation_id": operation_id,
            "event": event,
            "index": index,
            "total": total,
            "name": name,
            "remote_file": remote_path,
            "local_file": str(local_path),
            "remote_size": size,
        }
        if error is not None:
            detail["error_type"] = type(error).__name__
            detail["error_message"] = str(error) or repr(error)
        self.recorder.log(
            "SCP BATCH FILE "
            + json.dumps(detail, ensure_ascii=False, sort_keys=True),
            console=False,
        )

    def _download(
        self,
        protocol: str,
        remote_dir: str,
        remote_file: str | None,
        pattern: str | None,
        overwrite: bool,
    ) -> RemoteDownloadResult:
        remote_dir = self._normalize_remote_dir(remote_dir)
        if (remote_file is None) == (pattern is None):
            raise ValueError("exactly one of remote_file and pattern must be provided")
        if remote_file is not None:
            remote_file = self._remote_basename(remote_file)
        if pattern is not None:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError("pattern must be a non-empty regular expression")
            pattern = pattern.strip()
            re.compile(pattern)
        if not isinstance(overwrite, bool):
            raise TypeError("overwrite must be a boolean")

        operation_id = self.recorder.next_operation_id()
        started = datetime.now().astimezone()
        selected_name: str | None = None
        remote_path: str | None = None
        local_path: Path | None = None
        remote_size: int | None = None
        local_size: int | None = None
        local_existed = False
        local_md5: str | None = None
        success = False
        status = "download_failed"
        error_type: str | None = None
        error_message: str | None = None
        pending: Path | None = None
        try:
            self._ensure_transport()
            if pattern is not None:
                names = self._list_remote_files(remote_dir, protocol)
                matches = sorted(name for name in names if re.search(pattern, name))
                if not matches:
                    raise _RemoteDownloadError("remote_file_not_found", "REMOTE_FILE_NOT_FOUND", f"no file in {remote_dir!r} matched {pattern!r}")
                if len(matches) > 1:
                    raise _RemoteDownloadError("ambiguous_remote_file", "AMBIGUOUS_REMOTE_FILE", f"multiple files in {remote_dir!r} matched {pattern!r}: {', '.join(matches)}")
                selected_name = matches[0]
            else:
                selected_name = remote_file
            assert selected_name is not None
            remote_path = self._remote_file(remote_dir, selected_name)
            remote_size = self._remote_size(remote_path, protocol)
            local_path = (self.package_dir / selected_name).resolve()
            local_path.relative_to(self.package_dir.resolve())
            local_existed = local_path.exists()
            if local_existed and not overwrite:
                raise _RemoteDownloadError("local_file_exists", "LOCAL_FILE_EXISTS", f"local file already exists: {local_path}")
            pending = local_path.with_name(local_path.name + ".part")
            pending.unlink(missing_ok=True)
            if protocol == "scp":
                scp_client = self._get_scp()
                try:
                    scp_client.get(remote_path, local_path=str(pending))
                finally:
                    self._close_scp()
            else:
                sftp = self._get_sftp()
                sftp.get(remote_path, str(pending))
            local_size = pending.stat().st_size
            if local_size != remote_size:
                raise _RemoteDownloadError("size_verification_failed", "SIZE_VERIFICATION_FAILED", f"downloaded size mismatch: remote={remote_size}, local={local_size}")
            local_md5 = self._local_md5(pending)
            pending.replace(local_path)
            success, status = True, "success"
        except _RemoteDownloadError as exc:
            status, error_type, error_message = exc.status, exc.error_type, str(exc)
        except FileNotFoundError as exc:
            status, error_type, error_message = "remote_file_not_found", "REMOTE_FILE_NOT_FOUND", str(exc)
        except paramiko.AuthenticationException as exc:
            status, error_type, error_message = "auth_failed", "AUTHENTICATION_FAILED", str(exc) or "SSH authentication failed"
            self._invalidate_connection()
        except (socket.timeout, TimeoutError) as exc:
            status, error_type, error_message = "connection_timeout", "CONNECTION_TIMEOUT", str(exc) or "SSH operation timed out"
            self._invalidate_connection()
        except Exception as exc:
            status, error_type, error_message = "download_failed", type(exc).__name__, str(exc) or repr(exc)
        finally:
            if pending is not None and pending.exists():
                pending.unlink(missing_ok=True)
        finished = datetime.now().astimezone()
        result = RemoteDownloadResult(
            run_id=self.run_id, operation_id=operation_id, protocol=protocol,
            target_name=self.name, remote_dir=remote_dir, requested_file=remote_file,
            pattern=pattern, success=success, status=status, overwrite=overwrite,
            started_at=started, finished_at=finished,
            duration_ms=max(0, int((finished-started).total_seconds()*1000)),
            remote_file=remote_path, remote_size=remote_size,
            local_file=str(local_path) if local_path else None, local_size=local_size,
            local_existed=local_existed, local_md5=local_md5,
            size_verified=success, error_type=error_type, error_message=error_message,
        )
        self.recorder.record_result(f"{protocol.upper()} DOWNLOAD", result)
        return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._discard_connection()

    def _upload(
        self,
        protocol: str,
        selector: LocalFileSelector,
        remote_dir: str,
        overwrite: bool,
    ) -> UploadResult:
        selector_type, selector_value = describe_selector(selector)
        remote_dir = self._normalize_remote_dir(remote_dir)
        if not isinstance(overwrite, bool):
            raise TypeError("overwrite must be a boolean")

        operation_id = self.recorder.next_operation_id()
        started_at = datetime.now().astimezone()
        resolved_path: Path | None = None
        remote_file: str | None = None
        remote_existed = False
        local_md5: str | None = None
        remote_md5_before: str | None = None
        remote_md5_after: str | None = None
        md5_changed: bool | None = None
        md5_verified = False
        connected = False
        success = False
        status = "upload_failed"
        error_type: str | None = None
        error_message: str | None = None

        try:
            resolved = resolve_local_file(
                selector, self.package_dir, self.image_pattern_for
            )
            resolved_path = resolved.path
            local_md5 = self._local_md5(resolved_path)
            remote_file = self._remote_file(remote_dir, resolved_path.name)

            self._ensure_transport()
            connected = True
            if protocol == "scp":
                self._mkdir_via_ssh(remote_dir)
                remote_existed = self._remote_exists_via_ssh(remote_file)
            else:
                sftp = self._get_sftp()
                self._mkdir_recursive(sftp, remote_dir)
                remote_existed = self._remote_exists(sftp, remote_file)
            if remote_existed:
                remote_md5_before = (
                    self._remote_md5_via_ssh(remote_file)
                    if protocol == "scp"
                    else self._remote_md5(sftp, remote_file)
                )
                if not overwrite:
                    status = "remote_file_exists"
                    error_type = "REMOTE_FILE_EXISTS"
                    error_message = f"remote file already exists: {remote_file}"
                    return self._finish_upload(
                        protocol=protocol,
                        operation_id=operation_id,
                        selector_type=selector_type,
                        selector_value=selector_value,
                        remote_dir=remote_dir,
                        overwrite=overwrite,
                        started_at=started_at,
                        resolved_path=resolved_path,
                        remote_file=remote_file,
                        remote_existed=remote_existed,
                        local_md5=local_md5,
                        remote_md5_before=remote_md5_before,
                        remote_md5_after=None,
                        md5_changed=None,
                        md5_verified=False,
                        success=False,
                        status=status,
                        error_type=error_type,
                        error_message=error_message,
                    )

            if protocol == "scp":
                scp_client = self._get_scp()
                try:
                    scp_client.put(str(resolved_path), remote_path=remote_dir)
                finally:
                    self._close_scp()
                remote_md5_after = self._remote_md5_via_ssh(remote_file)
            else:
                sftp.put(str(resolved_path), remote_file)
                remote_md5_after = self._remote_md5(sftp, remote_file)

            md5_changed = remote_md5_before != remote_md5_after
            md5_verified = remote_md5_after == local_md5
            if md5_verified:
                success = True
                status = "success"
            else:
                status = "md5_verification_failed"
                error_type = "MD5_VERIFICATION_FAILED"
                error_message = (
                    f"uploaded file MD5 mismatch: local={local_md5}, "
                    f"remote={remote_md5_after}"
                )
        except SelectorResolutionError as exc:
            status = exc.code.lower()
            error_type = exc.code
            error_message = str(exc)
        except paramiko.AuthenticationException as exc:
            status = "auth_failed"
            error_type = "AUTHENTICATION_FAILED"
            error_message = str(exc) or "SSH authentication failed"
            self._invalidate_connection()
        except (socket.timeout, TimeoutError) as exc:
            status = "connection_timeout"
            error_type = "CONNECTION_TIMEOUT"
            error_message = str(exc) or "SSH operation timed out"
            self._invalidate_connection()
        except (EOFError, ConnectionError) as exc:
            status = "disconnected"
            error_type = "CONNECTION_LOST"
            error_message = str(exc) or "SSH connection was lost"
            self._invalidate_connection()
        except (paramiko.ssh_exception.NoValidConnectionsError, OSError) as exc:
            if resolved_path is not None and remote_file is None:
                status = "local_file_read_failed"
                error_type = "LOCAL_FILE_READ_FAILED"
            elif not connected:
                status = "connection_failed"
                error_type = "CONNECTION_FAILED"
            elif self._is_disconnect_exception(exc) or self._looks_disconnected(exc):
                status = "disconnected"
                error_type = "CONNECTION_LOST"
            else:
                status = "upload_failed"
                error_type = type(exc).__name__
            error_message = str(exc) or repr(exc)
            if remote_file is not None or connected:
                self._invalidate_connection()
        except paramiko.SSHException as exc:
            status = "protocol_error"
            error_type = "SSH_PROTOCOL_ERROR"
            error_message = str(exc) or "SSH protocol error"
            self._invalidate_connection()
        except _HostClosedError as exc:
            status = "connection_failed"
            error_type = "SSH_HOST_CLOSED"
            error_message = str(exc)
        except Exception as exc:
            status = "upload_failed"
            error_type = type(exc).__name__
            error_message = str(exc) or repr(exc)

        return self._finish_upload(
            protocol=protocol,
            operation_id=operation_id,
            selector_type=selector_type,
            selector_value=selector_value,
            remote_dir=remote_dir,
            overwrite=overwrite,
            started_at=started_at,
            resolved_path=resolved_path,
            remote_file=remote_file,
            remote_existed=remote_existed,
            local_md5=local_md5,
            remote_md5_before=remote_md5_before,
            remote_md5_after=remote_md5_after,
            md5_changed=md5_changed,
            md5_verified=md5_verified,
            success=success,
            status=status,
            error_type=error_type,
            error_message=error_message,
        )

    def _finish_upload(
        self,
        *,
        protocol: str,
        operation_id: str,
        selector_type: str,
        selector_value: str,
        remote_dir: str,
        overwrite: bool,
        started_at: datetime,
        resolved_path: Path | None,
        remote_file: str | None,
        remote_existed: bool,
        local_md5: str | None,
        remote_md5_before: str | None,
        remote_md5_after: str | None,
        md5_changed: bool | None,
        md5_verified: bool,
        success: bool,
        status: str,
        error_type: str | None,
        error_message: str | None,
    ) -> UploadResult:
        finished_at = datetime.now().astimezone()
        result = UploadResult(
            run_id=self.run_id,
            operation_id=operation_id,
            protocol=protocol,
            target_name=self.name,
            selector_type=selector_type,
            selector=selector_value,
            package_dir=str(self.package_dir),
            remote_dir=remote_dir,
            success=success,
            status=status,
            overwrite=overwrite,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
            resolved_local_file=str(resolved_path) if resolved_path is not None else None,
            remote_file=remote_file,
            remote_existed=remote_existed,
            local_md5=local_md5,
            remote_md5_before=remote_md5_before,
            remote_md5_after=remote_md5_after,
            md5_changed=md5_changed,
            md5_verified=md5_verified,
            error_type=error_type,
            error_message=error_message,
        )
        if result.success and result.remote_file is not None:
            self.uploaded_files.record(
                result.selector,
                result.remote_file,
                target_name=self.name,
            )
        self.recorder.record_result(f"{protocol.upper()} UPLOAD", result)
        return result

    def _ensure_transport(self) -> Any:
        if self._closed:
            raise _HostClosedError(f"SSH host {self.name!r} is closed")
        if self._transport is not None and self._transport_is_active(self._transport):
            return self._transport
        self._discard_connection()

        client = self._client_factory()
        self._client = client
        try:
            if hasattr(client, "set_missing_host_key_policy"):
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.info.host,
                port=self.info.port,
                username=self.info.username,
                password=self.info.password,
                timeout=self.info.connect_timeout,
                banner_timeout=self.info.connect_timeout,
                auth_timeout=self.info.connect_timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            transport = client.get_transport()
            if transport is None or not self._transport_is_active(transport):
                raise EOFError("SSH client did not provide an active transport")
            self._transport = transport
            return transport
        except Exception:
            self._discard_connection()
            raise

    @staticmethod
    def _transport_is_active(transport: Any) -> bool:
        try:
            active = transport.is_active()
        except AttributeError:
            return True
        except Exception:
            return False
        if not active:
            return False
        try:
            authenticated = transport.is_authenticated()
        except AttributeError:
            return True
        except Exception:
            return False
        return bool(authenticated)

    def _get_sftp(self) -> Any:
        self._ensure_transport()
        if self._sftp is None:
            if self._client is not None and hasattr(self._client, "open_sftp"):
                self._sftp = self._client.open_sftp()
            else:
                self._sftp = paramiko.SFTPClient.from_transport(self._transport)
        return self._sftp

    def _get_scp(self) -> Any:
        transport = self._ensure_transport()
        if self._scp is None:
            self._scp = self._scp_factory(transport)
        return self._scp

    def _exec_remote_checked(self, command: str) -> str:
        """Run a short SSH command without opening or depending on SFTP."""
        self._ensure_transport()
        assert self._client is not None
        stdin = stdout = stderr = None
        try:
            stdin, stdout, stderr = self._client.exec_command(
                command,
                timeout=self.info.connect_timeout,
            )
            stdout_data = stdout.read()
            stderr_data = stderr.read()
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                detail = self._decode_remote_output(stderr_data).strip()
                raise paramiko.SSHException(
                    f"remote command failed with code {exit_code}"
                    + (f": {detail}" if detail else "")
                )
            return self._decode_remote_output(stdout_data)
        finally:
            for stream in (stdin, stdout, stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

    @staticmethod
    def _decode_remote_output(data: Any) -> str:
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    @staticmethod
    def _quoted_remote_path(remote_path: str) -> str:
        # Avoid treating a relative path beginning with '-' as a command option.
        safe_path = f"./{remote_path}" if remote_path.startswith("-") else remote_path
        return shlex.quote(safe_path)

    def _mkdir_via_ssh(self, remote_dir: str) -> None:
        if remote_dir in ("", ".", "/"):
            return
        self._exec_remote_checked(
            f"mkdir -p {self._quoted_remote_path(remote_dir)}"
        )

    def _remote_exists_via_ssh(self, remote_path: str) -> bool:
        output = self._exec_remote_checked(
            "if [ -e {path} ]; then printf 1; else printf 0; fi".format(
                path=self._quoted_remote_path(remote_path)
            )
        )
        value = output.strip()
        if value not in {"0", "1"}:
            raise paramiko.SSHException(
                f"unexpected remote file existence response: {value!r}"
            )
        return value == "1"

    def _remote_md5_via_ssh(self, remote_path: str) -> str:
        output = self._exec_remote_checked(
            f"md5sum {self._quoted_remote_path(remote_path)}"
        )
        digest = output.strip().split(maxsplit=1)[0] if output.strip() else ""
        if len(digest) != 32 or any(
            char not in "0123456789abcdefABCDEF" for char in digest
        ):
            raise paramiko.SSHException(
                f"unexpected md5sum response for {remote_path}: {output.strip()!r}"
            )
        return digest.lower()

    def _invalidate_connection(self) -> None:
        self._discard_connection()

    def _close_scp(self) -> None:
        resource = self._scp
        self._scp = None
        if resource is not None:
            try:
                resource.close()
            except Exception:
                pass

    def _close_sftp(self) -> None:
        resource = self._sftp
        self._sftp = None
        if resource is not None:
            try:
                resource.close()
            except Exception:
                pass

    def _discard_connection(self) -> None:
        self._close_scp()
        self._close_sftp()
        resources = (self._client, self._transport)
        self._client = None
        self._transport = None
        for resource in resources:
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass

    @staticmethod
    def _normalize_remote_dir(remote_dir: str) -> str:
        remote_dir = _text(remote_dir, "remote_dir").replace("\\", "/")
        if "\x00" in remote_dir:
            raise ValueError("remote_dir must not contain NUL")
        normalized = posixpath.normpath(remote_dir)
        return normalized

    @staticmethod
    def _remote_file(remote_dir: str, filename: str) -> str:
        if remote_dir == ".":
            return filename
        return posixpath.join(remote_dir, filename)

    @staticmethod
    def _remote_basename(value: str) -> str:
        value = _text(value, "remote_file")
        if value != posixpath.basename(value) or value in {".", ".."}:
            raise ValueError("remote_file must be a filename; pass its directory via remote_dir")
        return value

    def _list_remote_files(self, remote_dir: str, protocol: str) -> list[str]:
        if protocol == "sftp":
            entries = self._get_sftp().listdir_attr(remote_dir)
            return [item.filename for item in entries if stat.S_ISREG(getattr(item, "st_mode", 0))]
        # BusyBox find does not implement GNU find's -printf.  Shell globbing
        # plus test/printf keeps the same non-recursive semantics and works in
        # BusyBox ash without requiring SFTP to be enabled on the device.
        command = self._busybox_list_command(remote_dir, metadata=False)
        result = self._execute(
            command,
            timeout=self.info.connect_timeout,
            expect_disconnect=False,
            console=False,
        )
        if not result.success:
            raise _RemoteDownloadError("remote_list_failed", "REMOTE_LIST_FAILED", result.error_message or result.output or "failed to list remote directory")
        # NUL delimiters preserve spaces, tabs, and newlines in legal filenames;
        # a remote filename itself can never contain NUL.
        names = result.stdout.split("\0")
        if names and names[-1] == "":
            names.pop()
        for name in names:
            self._validate_listed_remote_name(name)
        return names

    def _list_remote_file_metadata(self, remote_dir: str) -> list[tuple[str, int, float]]:
        """List direct child files using commands supplied by BusyBox.

        ``stat -c %Y`` yields a whole-second Unix mtime.  That is less precise
        than GNU find's ``%T@`` but sufficient because collection ordering also
        uses case-folded and original filenames as deterministic tie breakers.
        """

        command = self._busybox_list_command(remote_dir, metadata=True)
        result = self._execute(
            command,
            timeout=self.info.connect_timeout,
            expect_disconnect=False,
            console=False,
        )
        if not result.success:
            raise _RemoteDownloadError(
                "remote_list_failed",
                "REMOTE_LIST_FAILED",
                result.error_message or result.output or "failed to list remote directory",
            )
        parts = result.stdout.split("\0")
        if parts and parts[-1] == "":
            parts.pop()
        if len(parts) % 3:
            raise _RemoteDownloadError(
                "remote_list_failed",
                "REMOTE_LIST_FAILED",
                f"cannot parse remote metadata field count: {len(parts)}",
            )
        entries: list[tuple[str, int, float]] = []
        for index in range(0, len(parts), 3):
            name, raw_size, raw_mtime = parts[index : index + 3]
            self._validate_listed_remote_name(name)
            try:
                size = int(raw_size)
                mtime = float(raw_mtime)
            except ValueError as exc:
                raise _RemoteDownloadError(
                    "remote_list_failed",
                    "REMOTE_LIST_FAILED",
                    f"cannot parse remote metadata for {name!r}",
                ) from exc
            if size < 0 or not math.isfinite(mtime):
                raise _RemoteDownloadError(
                    "remote_list_failed",
                    "REMOTE_LIST_FAILED",
                    f"invalid remote metadata for {name!r}",
                )
            entries.append((name, size, mtime))
        return entries

    @staticmethod
    def _busybox_list_command(remote_dir: str, *, metadata: bool) -> str:
        """Build a BusyBox-ash-compatible, non-recursive file listing command."""

        quoted_dir = shlex.quote(remote_dir)
        if metadata:
            emit = (
                'size=$(wc -c < "$path") || exit 41\n'
                'mtime=$(stat -c %Y "$path") || exit 42\n'
                'printf \'%s\\000%s\\000%s\\000\' "${path##*/}" "$size" "$mtime"'
            )
        else:
            emit = 'printf \'%s\\000\' "${path##*/}"'
        return (
            f"remote_dir={quoted_dir}\n"
            'if [ ! -d "$remote_dir" ]; then\n'
            '  echo "remote directory not found: $remote_dir" >&2\n'
            "  exit 40\n"
            "fi\n"
            # Include regular and hidden direct children.  Unmatched patterns
            # remain literal strings, then fail the -f test harmlessly.
            'for path in "$remote_dir"/* "$remote_dir"/.[!.]* "$remote_dir"/..?*; do\n'
            '  [ -f "$path" ] || continue\n'
            f"  {emit.replace(chr(10), chr(10) + '  ')}\n"
            "done"
        )

    @staticmethod
    def _validate_listed_remote_name(name: str) -> None:
        if not name or name != posixpath.basename(name) or name in {".", ".."}:
            raise _RemoteDownloadError(
                "remote_list_failed",
                "REMOTE_LIST_FAILED",
                f"unsafe remote filename: {name!r}",
            )

    def _remote_size(self, remote_path: str, protocol: str) -> int:
        if protocol == "sftp":
            return int(self._get_sftp().stat(remote_path).st_size)
        result = self._execute(
            f"wc -c < {shlex.quote(remote_path)}",
            timeout=self.info.connect_timeout,
            expect_disconnect=False,
            console=False,
        )
        if not result.success:
            raise _RemoteDownloadError("remote_file_not_found", "REMOTE_FILE_NOT_FOUND", result.error_message or result.output or f"cannot read remote file: {remote_path}")
        try:
            return int(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError) as exc:
            raise _RemoteDownloadError("remote_size_failed", "REMOTE_SIZE_FAILED", f"cannot parse remote file size: {result.stdout!r}") from exc

    @classmethod
    def _mkdir_recursive(cls, sftp: Any, remote_dir: str) -> None:
        if remote_dir in ("", ".", "/"):
            return
        try:
            attributes = sftp.stat(remote_dir)
        except OSError as exc:
            if not cls._is_not_found(exc):
                raise
        else:
            mode = getattr(attributes, "st_mode", None)
            if mode is not None and not stat.S_ISDIR(mode):
                raise NotADirectoryError(f"remote path is not a directory: {remote_dir}")
            return

        parent = posixpath.dirname(remote_dir)
        if parent and parent != remote_dir:
            cls._mkdir_recursive(sftp, parent)
        try:
            sftp.mkdir(remote_dir)
        except OSError as exc:
            # Another process may have created it between stat() and mkdir().
            if not cls._remote_exists(sftp, remote_dir):
                raise exc

    @classmethod
    def _remote_exists(cls, sftp: Any, remote_path: str) -> bool:
        try:
            sftp.stat(remote_path)
            return True
        except OSError as exc:
            if cls._is_not_found(exc):
                return False
            raise

    @staticmethod
    def _is_not_found(exc: OSError) -> bool:
        return isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) in (
            errno.ENOENT,
            None if not exc.args else -1,
        ) and bool(exc.args) and exc.args[0] == errno.ENOENT

    @staticmethod
    def _local_md5(path: Path) -> str:
        digest = hashlib.md5()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_READ_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _remote_md5(sftp: Any, remote_path: str) -> str:
        opener = getattr(sftp, "open", None) or getattr(sftp, "file", None)
        if opener is None:
            raise paramiko.SSHException("SFTP client cannot open a remote file")
        handle = opener(remote_path, "rb")
        digest = hashlib.md5()
        try:
            while True:
                chunk = handle.read(_READ_SIZE)
                if not chunk:
                    break
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                digest.update(chunk)
        finally:
            handle.close()
        return digest.hexdigest()

    @staticmethod
    def _is_disconnect_exception(exc: OSError) -> bool:
        return getattr(exc, "errno", None) in {
            errno.ECONNABORTED,
            errno.ECONNRESET,
            errno.ENETDOWN,
            errno.ENETRESET,
            errno.ENETUNREACH,
            errno.EPIPE,
            errno.ESHUTDOWN,
            errno.ETIMEDOUT,
        }

    @staticmethod
    def _looks_disconnected(exc: BaseException) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in ("closed", "disconnect", "eof", "end of file", "socket is closed")
        )
