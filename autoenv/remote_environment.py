"""Small script-facing objects around the existing, verified protocol clients."""

from __future__ import annotations

import math
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping

from .errors import AutoEnvOperationError
from .file_handles import FileHandle, filename_only
from .ftp_host import FTPConnectionInfo, FTPHost
from .results import CommandPhase, CommandProtocol, CommandResult, CommandStatus
from .ssh_host import SSHConnectionInfo, SSHHost
from .telnet_client import (
    TelnetClient, TelnetConnectionInfo, _ConnectionClosed, _PromptNotFound, _ReadTimedOut,
)


@dataclass(frozen=True)
class RemoteCheckResult:
    run_id: str
    operation_id: str
    protocol: str
    target_name: str
    success: bool
    status: str
    transport_ready: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    error_type: str | None = None
    error_message: str | None = None
    check_scope: str | None = None
    exit_status_available: bool | None = None


class _Remote:
    protocol: str

    def __init__(self, session: Any, config: Mapping[str, Any]) -> None:
        self.session = session
        self.config = MappingProxyType(dict(config))
        self.name = self.config["name"]
        self.topology = self.config.get("topology", "")
        self.target_name = f"{self.topology}/{self.name}" if self.topology else self.name
        self.available = False
        self.self_check_result: RemoteCheckResult | None = None
        self._check_scope: str | None = None
        self._exit_status_available: bool | None = None
        self._closed = False
        self._lock = RLock()
        self._client = self._new_client()

    @property
    def transport_ready(self) -> bool:
        return bool(getattr(self._client, "connected", getattr(self._client, "_ftp", None) is not None))

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"remote {self.target_name!r} is permanently closed")

    def _ensure_available(self) -> None:
        self._ensure_open()
        if not self.available:
            raise AutoEnvOperationError(self.self_check_result)

    def _new_client(self) -> Any:
        raise NotImplementedError

    def _probe(self) -> None:
        raise NotImplementedError

    def _self_check(self) -> RemoteCheckResult:
        started = datetime.now().astimezone()
        error_type = error_message = None
        self._check_scope = None
        self._exit_status_available = None
        try:
            self._probe()
            self.available = True
        except Exception as exc:
            self.available = False
            error_type = getattr(getattr(exc, "result", None), "error_type", None) or ("AUTH_FAILED" if isinstance(exc, PermissionError) else type(exc).__name__)
            error_message = str(exc) or repr(exc)
        finished = datetime.now().astimezone()
        result = RemoteCheckResult(
            self.session.run_id, self.session.recorder.next_operation_id(), self.protocol,
            self.target_name, self.available, "available" if self.available else "unavailable",
            self.transport_ready, started, finished,
            max(0, int((finished - started).total_seconds() * 1000)), error_type, error_message,
            self._check_scope, self._exit_status_available,
        )
        self.self_check_result = result
        self.session.recorder.record_result("REMOTE SELF CHECK", result)
        return result

    def retry(self, interval: float, max_try_times: int) -> None:
        """Reconnect and recheck; business commands and file transfers are not replayed."""
        if isinstance(interval, bool) or not isinstance(interval, (int, float)):
            raise TypeError("interval must be a number")
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("interval must be finite and nonnegative")
        if isinstance(max_try_times, bool) or not isinstance(max_try_times, int) or max_try_times < 1:
            raise ValueError("max_try_times must be a positive integer")
        with self._lock, self.session.trace(f"{self.target_name}.retry"):
            self._ensure_open()
            for attempt in range(max_try_times):
                if attempt:
                    time.sleep(interval)
                self._client.close()
                self._client = self._new_client()
                if self._self_check().success:
                    return
            raise AutoEnvOperationError(self.self_check_result)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._client.close()
            self._closed = True

    def _download_directory(self) -> Path:
        label = re.sub(r"[^\w.-]+", "_", self.name)
        destination = Path(self.session.runtime_path) / "downloads" / label / uuid.uuid4().hex
        destination.mkdir(parents=True, exist_ok=True)
        return destination


class SSHRemote(_Remote):
    protocol = "ssh"

    @property
    def upload_type(self) -> str:
        return self._upload_type

    def _new_client(self) -> SSHHost:
        upload_type = self.config.get("upload_type", "scp")
        if upload_type not in {"scp", "sftp"}:
            raise ValueError("SSH upload_type must be scp or sftp")
        self._upload_type = upload_type
        return SSHHost(
            name=self.target_name,
            info=SSHConnectionInfo(
                self.config["host"], self.config.get("port", 22),
                self.config.get("username", "root"), self.config.get("password", ""),
                self.config.get("connect_timeout", 30.0),
            ),
            run_id=self.session.run_id, package_dir=Path(self.session.runtime_path),
            recorder=self.session.recorder, image_pattern_for=lambda _: "",
        )

    def _probe(self) -> None:
        token = f"__AUTOENV_SELF_CHECK_{uuid.uuid4().hex}__"
        prefix = "command -v scp >/dev/null 2>&1 && command -v md5sum >/dev/null 2>&1 && " if self.upload_type == "scp" else ""
        result = self._client.execute(prefix + f"printf '%s\\n' {shlex.quote(token)}", timeout=self._client.info.connect_timeout)
        if not result.success:
            raise AutoEnvOperationError(result)
        if result.exit_code != 0 or token not in result.stdout:
            raise AutoEnvOperationError(result.with_failure("SSH self-check did not return its unique probe token"))
        if self.upload_type == "sftp":
            self._client._get_sftp()

    def execute(self, command: str, timeout: float | None = None, expect_disconnect: bool = False) -> CommandResult:
        with self._lock, self.session.trace(f"{self.target_name}.execute {command}"):
            self._ensure_available()
            return self._client.execute(command, timeout=300.0 if timeout is None else timeout, expect_disconnect=expect_disconnect)

    def run(self, command: str) -> str:
        result = self.execute(command)
        if not result.success:
            raise AutoEnvOperationError(result)
        return result.output

    def upload(self, file: FileHandle, path: str) -> None:
        if not isinstance(file, FileHandle):
            raise TypeError("upload requires a FileHandle from get_file, create_file or download")
        with self._lock, self.session.trace(f"{self.target_name}.upload {file.filename} -> {path}"):
            self._ensure_available()
            file.verify()
            result = getattr(self._client, f"{self.upload_type}_upload")(file, path)
            if not result.success:
                raise AutoEnvOperationError(result)

    def download(self, path: str, filename: str) -> FileHandle:
        with self._lock, self.session.trace(f"{self.target_name}.download {path}/{filename}"):
            self._ensure_available()
            filename_only(filename)
            original = self._client.package_dir
            try:
                self._client.package_dir = self._download_directory()
                result = getattr(self._client, f"{self.upload_type}_download")(path, remote_file=filename)
            finally:
                self._client.package_dir = original
            if not result.success:
                raise AutoEnvOperationError(result)
            return FileHandle(Path(result.local_file))


class FTPRemote(_Remote):
    protocol = "ftp"

    def _new_client(self) -> FTPHost:
        return FTPHost(
            name=self.target_name,
            info=FTPConnectionInfo(
                self.config["host"], self.config.get("port", 21),
                self.config.get("username", "anonymous"), self.config.get("password", ""),
                self.config.get("timeout", 30.0), self.config.get("passive", True),
            ), run_id=self.session.run_id, package_dir=Path(self.session.runtime_path),
            recorder=self.session.recorder, image_pattern_for=lambda _: "",
        )

    def _probe(self) -> None:
        self._client._connect().pwd()

    def upload(self, file: FileHandle, path: str) -> None:
        if not isinstance(file, FileHandle):
            raise TypeError("upload requires a FileHandle from get_file, create_file or download")
        with self._lock, self.session.trace(f"{self.target_name}.upload {file.filename} -> {path}"):
            self._ensure_available()
            file.verify()
            result = self._client.upload(file, path)
            if not result.success:
                raise AutoEnvOperationError(result)

    def download(self, path: str, filename: str) -> FileHandle:
        with self._lock, self.session.trace(f"{self.target_name}.download {path}/{filename}"):
            self._ensure_available()
            filename_only(filename)
            original = self._client.package_dir
            try:
                self._client.package_dir = self._download_directory()
                result = self._client.download(path, filename)
            finally:
                self._client.package_dir = original
            if not result.success:
                raise AutoEnvOperationError(result)
            return FileHandle(Path(result.local_file))


class TelnetRemote(_Remote):
    protocol = "telnet"

    def _new_client(self) -> TelnetClient:
        return TelnetClient(
            name=self.target_name,
            info=TelnetConnectionInfo(
                self.config["host"], self.config.get("port", 23),
                self.config.get("timeout", 30.0), self.config.get("shell_mode", "auto"),
            ), run_id=self.session.run_id, recorder=self.session.recorder,
        )

    def _probe(self) -> None:
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        command, keyword = self.config.get("probe_command"), self.config.get("probe_keyword")
        if bool(command) != bool(keyword):
            raise ValueError("SELF_CHECK_NOT_CONFIGURED: probe_command and probe_keyword must be supplied together")
        if password and not username:
            raise ValueError("Telnet password requires a username")
        if username:
            self._client.login_passive(
                username, password, login_prompt=self.config.get("login_prompt", "login:"),
                password_prompt=self.config.get("password_prompt", "Password:"),
            )
        elif self._client.info.shell_mode == "prompt_only":
            self._client.connect_passive()
        self._client.prepare_command_session()
        if self._client.shell_mode == "prompt_only":
            self._check_scope = "keyword" if command else "prompt"
            self._exit_status_available = False
            if command:
                self._client.wait_passive(keyword, probe_data=(command + "\r\n").encode("utf-8"))
        else:
            self._check_scope = "command"
            self._exit_status_available = True
            token = f"__AUTOENV_SELF_CHECK_{uuid.uuid4().hex}__"
            result = self._client.execute(f"printf '%s\\n' {shlex.quote(token)}")
            if not result.success:
                raise AutoEnvOperationError(result)
            if result.exit_code != 0 or token not in result.stdout:
                raise AutoEnvOperationError(result.with_failure("Telnet self-check did not return its unique probe token and exit status"))

    def _passive_result(self, operation: str, action: Any) -> CommandResult:
        started = datetime.now().astimezone()
        operation_id = self.session.recorder.next_operation_id()
        status, phase, output, error_type, error_message = CommandStatus.SUCCESS, CommandPhase.COMPLETE, "", None, None
        disconnected = False
        raw_output = ""
        self._client._last_passive_raw = ""
        try:
            output = action() or ""
            raw_output = self._client._last_passive_raw
        except _ReadTimedOut as exc:
            status, phase, output = CommandStatus.TIMEOUT, CommandPhase.WAIT_OUTPUT, exc.partial
            raw_output = self._client._last_passive_raw
            error_type, error_message = "KEYWORD_NOT_FOUND", "Telnet keyword was not received before timeout"
        except (_ConnectionClosed, OSError) as exc:
            status, phase = CommandStatus.DISCONNECTED, CommandPhase.WAIT_OUTPUT
            output = getattr(exc, "partial", "")
            raw_output = output
            output = self._client._clean_terminal_text(output)
            error_type, error_message = "CONNECTION_LOST", str(exc)
            disconnected = True
            self._client._drop_connection()
        except _PromptNotFound as exc:
            status, phase, output = CommandStatus.PROTOCOL_ERROR, CommandPhase.DETECT_PROMPT, exc.partial
            raw_output = output
            output = self._client._clean_terminal_text(output)
            error_type, error_message = "PROMPT_NOT_FOUND", "No Telnet prompt was received"
        except (BufferError, RuntimeError) as exc:
            status, phase = CommandStatus.PROTOCOL_ERROR, CommandPhase.WAIT_OUTPUT
            error_type, error_message = type(exc).__name__, str(exc)
        finished = datetime.now().astimezone()
        result = CommandResult(
            self.session.run_id, operation_id, CommandProtocol.TELNET, self.target_name,
            operation, status, phase, None, output, "", raw_output, started, finished,
            max(0, int((finished-started).total_seconds()*1000)), error_type, error_message,
            disconnected=disconnected,
        )
        self.session.recorder.record_result("TELNET CONSOLE", result)
        return result

    def execute(self, command: str, timeout: float | None = None, expect_disconnect: bool = False) -> CommandResult:
        with self._lock, self.session.trace(f"{self.target_name}.execute {command}"):
            self._ensure_available()
            if self._client.connected and self._client.prompt is None:
                prepared = self._passive_result("prepare command prompt", self._client.prepare_passive_command)
                if not prepared.success:
                    return prepared
            return self._client.execute(command, timeout=timeout, expect_disconnect=expect_disconnect)

    def run(self, command: str) -> str:
        result = self.execute(command)
        if not result.success:
            raise AutoEnvOperationError(result)
        return result.output

    def read(self) -> str:
        with self._lock, self.session.trace(f"{self.target_name}.read"):
            self._ensure_open()
            result = self._passive_result("read", self._client.read_passive)
            if not result.success:
                raise AutoEnvOperationError(result)
            return result.output

    def mark_output(self) -> None:
        with self._lock, self.session.trace(f"{self.target_name}.mark_output"):
            self._ensure_open()
            result = self._passive_result("mark_output", self._client.mark_passive_output)
            if not result.success:
                raise AutoEnvOperationError(result)

    def wait_reboot(self, keyword: str) -> str:
        return self._wait(keyword, None, "wait_reboot")

    def telnet_wait_exec(self, keyword: str, exec_content: str | bytes) -> str:
        if isinstance(exec_content, str):
            if not exec_content:
                raise ValueError("exec_content must not be empty")
            data = (exec_content + "\r\n").encode("utf-8")
        elif isinstance(exec_content, bytes) and exec_content:
            data = exec_content
        else:
            raise TypeError("exec_content must be nonempty str or bytes")
        return self._wait(keyword, data, "telnet_wait_exec")

    def _wait(self, keyword: str, data: bytes | None, operation: str) -> str:
        if not isinstance(keyword, str) or not keyword:
            raise ValueError("keyword must be a nonempty string")
        with self._lock, self.session.trace(f"{self.target_name}.{operation} {keyword!r}"):
            self._ensure_open()
            if operation == "wait_reboot":
                self._client.invalidate_passive_prompt()
            label = f"{operation}({keyword!r})" + (f" send={data!r}" if data is not None else "")
            result = self._passive_result(label, lambda: self._client.wait_passive(keyword, send_data=data))
            if not result.success:
                raise AutoEnvOperationError(result)
            return result.output


def create_remote(session: Any, config: Mapping[str, Any]) -> SSHRemote | TelnetRemote | FTPRemote:
    remote_class = {"ssh": SSHRemote, "telnet": TelnetRemote, "ftp": FTPRemote}.get(config.get("type"))
    if remote_class is None:
        raise ValueError("connection type must be ssh, telnet or ftp")
    remote = remote_class(session, config)
    session.remotes.append(remote)
    with session.trace(f"{remote.target_name}.self_check"):
        remote._self_check()
    return remote
