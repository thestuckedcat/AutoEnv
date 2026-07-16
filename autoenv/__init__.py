from .registry import register_func, register_script
from .results import (
    CommandPhase,
    CommandProtocol,
    CommandResult,
    CommandStatus,
    DownloadResult,
    ExtractResult,
    ScriptResult,
    UploadResult,
)
from .selectors import extra_file, match, package
from .ssh_host import SSHDefaults
from .telnet_client import TelnetDefaults

__all__ = [
    "CommandPhase",
    "CommandProtocol",
    "CommandResult",
    "CommandStatus",
    "DownloadResult",
    "ExtractResult",
    "SSHDefaults",
    "ScriptResult",
    "TelnetDefaults",
    "UploadResult",
    "extra_file",
    "match",
    "package",
    "register_script",
    "register_func",
]
