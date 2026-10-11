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
from .logs import LogCollection, LogGroup, LogSource, MetadataPattern, TimestampPattern
from .selectors import extra_file, match, package
from .ssh_host import SSHDefaults
from .ftp_host import FTPDefaults
from .telnet_client import TelnetDefaults
from .errors import AutoEnvOperationError
from .file_handles import (
    FileHandle, FileHandleGroup, accurate_search, create_file, find_subdir,
    fuzz_search, get_file, get_handler, hdfs_download,
)
from .local_archives import detect_archive
from .script_session import ScriptPaths, get_remote_info, init_script
from .remote_environment import FTPRemote, SSHRemote, TelnetRemote

__all__ = [
    "AutoEnvOperationError",
    "FileHandle",
    "FileHandleGroup",
    "ScriptPaths",
    "SSHRemote",
    "TelnetRemote",
    "FTPRemote",
    "init_script",
    "get_remote_info",
    "get_file",
    "get_handler",
    "fuzz_search",
    "accurate_search",
    "find_subdir",
    "detect_archive",
    "create_file",
    "hdfs_download",
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
    "LogSource",
    "MetadataPattern",
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
