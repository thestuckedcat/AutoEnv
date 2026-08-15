"""Download log bundles and prepare a hand-off point for business log parsing."""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from autoenv import SSHDefaults, register_script


@register_script(
    name="download_and_parse_logs",
    description="Download one remote ZIP, recursively unpack ZIP files, then parse log blocks",
    parameters=(
        {"name": "remote_dir", "label": "远端目录", "required": True},
        {"name": "remote_file", "label": "精确文件名", "required": False},
        {"name": "remote_pattern", "label": "文件名正则", "required": False},
        {"name": "log_pattern", "label": "日志块 pattern（待补规则）", "required": False},
    ),
)
def download_and_parse_logs(ctx):
    server = ctx.register_ssh_host(
        "log_server",
        resource_label="1260网口",
        alias="日志服务器网口",
        description="用于通过 SFTP 下载待解析日志 ZIP 的 SSH 连接。",
        defaults=SSHDefaults(),
    )
    remote_dir = str(ctx.argument("remote_dir", required=True))
    remote_file = str(ctx.argument("remote_file", default="") or "").strip()
    remote_pattern = str(ctx.argument("remote_pattern", default="") or "").strip()
    log_pattern = str(ctx.argument("log_pattern", default="") or "").strip()
    if bool(remote_file) == bool(remote_pattern):
        raise ValueError("exactly one of remote_file and remote_pattern must be provided")

    downloaded = server.sftp_download(
        remote_dir,
        remote_file=remote_file or None,
        pattern=remote_pattern or None,
    )
    if not downloaded.success:
        return downloaded

    assert downloaded.local_file is not None
    output_dir = ctx.run_dir / "parsed_logs"
    _unpack_all_zips(Path(downloaded.local_file), output_dir)
    _parse_log_blocks(output_dir, log_pattern)
    return downloaded


def _unpack_all_zips(source: Path, output_dir: Path) -> None:
    """Script-specific recursive ZIP expansion; keep outside AutoEnv public API."""

    output_dir.mkdir(parents=True, exist_ok=True)
    pending = [source]
    seen: set[Path] = set()
    while pending:
        archive_path = pending.pop(0)
        resolved = archive_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        destination = output_dir / archive_path.stem
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                normalized = member.filename.replace("\\", "/")
                relative = PurePosixPath(normalized)
                windows_path = PureWindowsPath(member.filename)
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                if (
                    not normalized
                    or relative.is_absolute()
                    or windows_path.is_absolute()
                    or windows_path.drive
                    or ".." in relative.parts
                ):
                    raise ValueError(f"unsafe ZIP member: {member.filename!r}")
                if unix_mode and stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                    raise ValueError(f"ZIP links are not allowed: {member.filename!r}")
                target = destination.joinpath(*relative.parts).resolve()
                target.relative_to(destination.resolve())
            archive.extractall(destination)
        pending.extend(sorted(destination.rglob("*.zip")))
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() not in {".zip", ".log"}:
            log_path = path.with_suffix(path.suffix + ".log")
            if not log_path.exists():
                shutil.copy2(path, log_path)


def _parse_log_blocks(output_dir: Path, pattern: str) -> None:
    """HAND-OFF: implement after receiving real log samples and block boundaries.

    Required future inputs: encoding, start pattern, end condition, overlap policy,
    context lines, and expected output format. This intentionally performs no
    guess-based extraction today.
    """

    manifest = output_dir / "LOG_PARSER_TODO.txt"
    manifest.write_text(
        "Log files are unpacked. Business block parsing is pending a real sample.\n"
        f"Requested pattern: {pattern or '<not supplied>'}\n",
        encoding="utf-8",
    )
