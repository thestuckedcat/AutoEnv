from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import upload_file_via_ftp, upload_files_via_ftp


def debug_upload_file_to_telnet_path(
    host: str,
    local_file: str,
    remote_file_path: str,
    username: str,
    password: str,
    *,
    port: int = 21,
    timeout: int = 30,
) -> None:
    """调试接口：通过 FTP 上传单个文件到 Telnet 服务器指定文件路径。"""
    upload_file_via_ftp(
        host=host,
        local_file=local_file,
        username=username,
        password=password,
        remote_file_path=remote_file_path,
        port=port,
        timeout=timeout,
    )


def debug_upload_files_via_ftp(
    host: str,
    local_files: Sequence[str],
    username: str,
    password: str,
    *,
    remote_path: str = "/root/autoEnv",
    port: int = 21,
    timeout: int = 30,
) -> None:
    """调试接口：通过 FTP 上传文件到 Telnet 服务器可访问目录。"""
    upload_files_via_ftp(
        host=host,
        local_files=local_files,
        username=username,
        password=password,
        remote_path=remote_path,
        port=port,
        timeout=timeout,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调试 FTP 发包到 Telnet 服务器可访问目录")
    parser.add_argument("--host", required=True, help="FTP/Telnet 服务器 IP/域名")
    parser.add_argument("--username", required=True, help="FTP 用户名")
    parser.add_argument("--password", help="FTP 密码；不传则交互输入")
    parser.add_argument("--port", type=int, default=21, help="FTP 端口，默认 21")
    parser.add_argument("--remote-path", default="/root/autoEnv", help="FTP 目标目录")
    parser.add_argument("--timeout", type=int, default=30, help="FTP 连接超时时间秒")
    parser.add_argument(
        "--remote-file",
        help="单文件上传时的远端完整路径；传入该参数时 files 只能有一个",
    )
    parser.add_argument("files", nargs="+", help="待上传本地文件")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    password = args.password if args.password is not None else getpass.getpass("请输入 FTP 密码: ")
    if args.remote_file:
        if len(args.files) != 1:
            raise ValueError("--remote-file 只能配合单个本地文件使用")
        debug_upload_file_to_telnet_path(
            host=args.host,
            local_file=args.files[0],
            remote_file_path=args.remote_file,
            username=args.username,
            password=password,
            port=args.port,
            timeout=args.timeout,
        )
        print(f"FTP 单文件上传完成: {args.files[0]} -> {args.host}:{args.remote_file}")
        return

    debug_upload_files_via_ftp(
        host=args.host,
        local_files=args.files,
        username=args.username,
        password=password,
        remote_path=args.remote_path,
        port=args.port,
        timeout=args.timeout,
    )
    print(f"FTP 上传完成: {len(args.files)} file(s) -> {args.host}:{args.remote_path}")


if __name__ == "__main__":
    main()
