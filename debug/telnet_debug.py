from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from telnet import run_telnet_commands


def debug_telnet_commands(
    host: str,
    commands: Sequence[str],
    *,
    port: int = 23,
    timeout: float = 30.0,
    log_path: str | None = None,
) -> list[str]:
    """调试接口：连接 Telnet 串口服务器，逐条发送命令并返回输出。"""
    return run_telnet_commands(
        host=host,
        commands=commands,
        port=port,
        timeout=timeout,
        log_path=log_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调试 Telnet 串口逐条命令发送")
    parser.add_argument("--host", required=True, help="Telnet 串口服务器 IP/域名")
    parser.add_argument("--port", type=int, default=23, help="Telnet 端口，默认 23")
    parser.add_argument("--timeout", type=float, default=30.0, help="单条命令超时时间秒")
    parser.add_argument("--log-path", default="runtime/debug_telnet.log", help="输出日志路径")
    parser.add_argument(
        "--command",
        action="append",
        dest="commands",
        required=True,
        help="要发送的命令；可重复传入多次，按传入顺序逐条发送",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    outputs = debug_telnet_commands(
        host=args.host,
        commands=args.commands,
        port=args.port,
        timeout=args.timeout,
        log_path=args.log_path,
    )
    for index, output in enumerate(outputs, start=1):
        print(f"===== command #{index} output =====")
        print(output.rstrip())


if __name__ == "__main__":
    main()
