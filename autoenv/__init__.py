from .registry import register_func, register_script
from .results import (
    CommandPhase,
    CommandProtocol,
    CommandResult,
    CommandStatus,
    DownloadResult,
    RemoteDownloadResult,
    RemoteBatchDownloadResult,
    RemoteDownloadedFile,
    LogOperationResult,
    ExtractResult,
    ScriptResult,
    UploadResult,
)
from .logs import LogCollection, LogGroup, TimestampPattern
from .selectors import extra_file, match, package
from .ssh_host import SSHDefaults
from .ftp_host import FTPDefaults
from .telnet_client import TelnetDefaults

__all__ = [
    "CommandPhase",
    "CommandProtocol",
    "CommandResult",
    "CommandStatus",
    "DownloadResult",
    "RemoteDownloadResult",
    "RemoteBatchDownloadResult",
    "RemoteDownloadedFile",
    "LogOperationResult",
    "ExtractResult",
    "LogCollection",
    "LogGroup",
    "TimestampPattern",
    "SSHDefaults",
    "FTPDefaults",
    "ScriptResult",
    "TelnetDefaults",
    "UploadResult",
    "extra_file",
    "match",
    "package",
    "register_script",
    "register_func",
]
