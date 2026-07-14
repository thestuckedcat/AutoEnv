from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class CommandProtocol(str, Enum):
    SSH = "ssh"
    TELNET = "telnet"


class CommandStatus(str, Enum):
    SUCCESS = "success"
    COMMAND_FAILED = "command_failed"
    CONNECTION_FAILED = "connection_failed"
    AUTH_FAILED = "auth_failed"
    TIMEOUT = "timeout"
    DISCONNECTED = "disconnected"
    PROTOCOL_ERROR = "protocol_error"
    RESULT_UNKNOWN = "result_unknown"


class CommandPhase(str, Enum):
    CONNECT = "connect"
    AUTHENTICATE = "authenticate"
    DETECT_PROMPT = "detect_prompt"
    SEND_COMMAND = "send_command"
    WAIT_OUTPUT = "wait_output"
    PARSE_RESULT = "parse_result"
    COMPLETE = "complete"


@dataclass(frozen=True)
class CommandResult:
    run_id: str
    operation_id: str
    protocol: CommandProtocol
    target_name: str
    command: str
    status: CommandStatus
    phase: CommandPhase
    exit_code: int | None
    stdout: str
    stderr: str
    raw_output: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    error_type: str | None = None
    error_message: str | None = None
    expected_disconnect: bool = False
    disconnected: bool = False

    @property
    def success(self) -> bool:
        return self.status == CommandStatus.SUCCESS

    @property
    def timed_out(self) -> bool:
        return self.status == CommandStatus.TIMEOUT

    @property
    def output(self) -> str:
        if not self.stderr:
            return self.stdout
        if not self.stdout:
            return self.stderr
        return f"{self.stdout}\n{self.stderr}"

    def with_failure(
        self,
        message: str,
        *,
        error_type: str = "BUSINESS_RULE_FAILED",
    ) -> "CommandResult":
        if not isinstance(message, str) or not message.strip():
            raise ValueError("failure message must not be empty")
        return replace(
            self,
            status=CommandStatus.COMMAND_FAILED,
            phase=CommandPhase.COMPLETE,
            error_type=error_type,
            error_message=message.strip(),
        )


@dataclass(frozen=True)
class DownloadResult:
    run_id: str
    operation_id: str
    success: bool
    status: str
    config_name: str
    image_pattern: str
    package_dir: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    remote_dir: str | None = None
    remote_file: str | None = None
    remote_size: int | None = None
    remote_modified_at: datetime | None = None
    local_file: str | None = None
    local_size: int | None = None
    local_existed: bool = False
    local_md5_before: str | None = None
    local_md5_after: str | None = None
    md5_changed: bool | None = None
    size_verified: bool = False
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class UploadResult:
    run_id: str
    operation_id: str
    protocol: str
    target_name: str
    selector_type: str
    selector: str
    package_dir: str
    remote_dir: str
    success: bool
    status: str
    overwrite: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    resolved_local_file: str | None = None
    remote_file: str | None = None
    remote_existed: bool = False
    local_md5: str | None = None
    remote_md5_before: str | None = None
    remote_md5_after: str | None = None
    md5_changed: bool | None = None
    md5_verified: bool = False
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ExtractResult:
    run_id: str
    operation_id: str
    success: bool
    status: str
    selector_type: str
    selector: str
    package_dir: str
    target_type: str
    target: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    source_file: str | None = None
    destination: str | None = None
    destination_existed: bool = False
    md5_before: str | None = None
    md5_after: str | None = None
    tree_md5_before: str | None = None
    tree_md5_after: str | None = None
    file_count_before: int | None = None
    file_count_after: int | None = None
    content_changed: bool | None = None
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ScriptResult:
    run_id: str
    script_name: str
    success: bool
    status: str
    run_dir: str
    package_dir: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    final_operation: Any | None = field(default=None, repr=False)
    error_type: str | None = None
    error_message: str | None = None


OperationResult = CommandResult | DownloadResult | UploadResult | ExtractResult


def result_to_dict(value: Any) -> Any:
    """Convert result models and common value types into JSON-safe objects."""

    if hasattr(value, "__dataclass_fields__"):
        return result_to_dict(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): result_to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [result_to_dict(item) for item in value]
    return value
