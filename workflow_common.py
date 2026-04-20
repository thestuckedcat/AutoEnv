import getpass
import os
import shutil
from typing import Dict, Tuple

from models import ImageSpec

RUNTIME_MAX_BYTES = 1 * 1024 * 1024 * 1024  # 1GB


def ask_target_host() -> str:
    host = input("请输入目标服务器 IP/域名 [默认:192.168.1.100]: ").strip()
    return host or "192.168.1.100"


def ask_ssh_credentials(default_username: str, default_password: str, default_port: int) -> Tuple[str, str, int]:
    username = input(f"请输入 SSH 用户名 [默认:{default_username}]: ").strip() or default_username
    password = getpass.getpass("请输入 SSH 密码 [回车使用默认密码]: ").strip() or default_password

    port_input = input(f"请输入 SSH 端口 [默认:{default_port}]: ").strip()
    if not port_input:
        port = default_port
    elif port_input.isdigit():
        port = int(port_input)
    else:
        print("⚠️ 端口输入非法，使用默认端口")
        port = default_port

    return username, password, port


def ask_package_link_overrides(image_vars: Dict[str, str], image_specs: Dict[str, ImageSpec]) -> Dict[str, str]:
    """按环境依赖的 spec_name 让用户可选覆盖下载路径。"""
    spec_names = sorted(set(image_vars.values()))
    overrides: Dict[str, str] = {}

    print("\n请选择驱动包路径（直接回车将使用 config.json 默认路径）")
    for spec_name in spec_names:
        spec = image_specs[spec_name]
        default_link = spec.link or "<自动 newest>"
        user_input = input(f"- {spec_name} 路径 [默认: {default_link}]: ").strip()
        if user_input:
            normalized = user_input.rstrip("/")
            if not normalized.startswith("/"):
                normalized = "/" + normalized
            overrides[spec_name] = normalized

    return overrides


def get_directory_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for filename in files:
            full_path = os.path.join(root, filename)
            try:
                total += os.path.getsize(full_path)
            except OSError:
                continue
    return total


def enforce_runtime_size_limit(runtime_root: str, max_bytes: int, protected_dir: str) -> None:
    if not os.path.isdir(runtime_root):
        return

    while get_directory_size(runtime_root) > max_bytes:
        candidates = []
        for name in os.listdir(runtime_root):
            full_path = os.path.join(runtime_root, name)
            if not os.path.isdir(full_path):
                continue
            if os.path.abspath(full_path) == os.path.abspath(protected_dir):
                continue
            candidates.append(full_path)

        if not candidates:
            return

        oldest = sorted(candidates, key=lambda p: os.path.getmtime(p))[0]
        shutil.rmtree(oldest, ignore_errors=True)
