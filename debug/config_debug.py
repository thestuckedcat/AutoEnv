from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_loader import load_image_specs
from env_config import get_ftp_defaults, get_telnet_defaults


def debug_load_image_specs(config_path: str = "config.json") -> dict[str, dict[str, object]]:
    """调试接口：加载镜像配置并输出包含 target_file 的结构化结果。"""
    specs = load_image_specs(config_path)
    return {name: asdict(spec) for name, spec in specs.items()}


def debug_env_transport_defaults(env_name: str) -> dict[str, dict[str, object]]:
    """调试接口：查看指定环境的 Telnet/FTP 默认配置。"""
    return {
        "telnet": dict(get_telnet_defaults(env_name)),
        "ftp": dict(get_ftp_defaults(env_name)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调试 config.json target_file 解析和环境传输默认值")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--env", help="环境名；传入后额外输出 Telnet/FTP 默认配置")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result: dict[str, object] = {"image_specs": debug_load_image_specs(args.config)}
    if args.env:
        result["transport_defaults"] = debug_env_transport_defaults(args.env)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
