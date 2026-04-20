import getpass
import logging
import os
import shutil
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config_loader import load_image_specs
from env_config import get_env, get_ssh_defaults, list_env_names
from logger import setup_logger
from models import DownloadedImage, ImageSpec
from renderer import render_script
from tools import HDFSClient, fetch_and_download_image, upload_files_via_scp

RUNTIME_MAX_BYTES = 1 * 1024 * 1024 * 1024  # 1GB


def choose_environment() -> str:
    envs = list_env_names()
    if not envs:
        raise RuntimeError("没有已注册环境")

    print("可选环境：")
    for idx, name in enumerate(envs, start=1):
        print(f"  {idx}. {name}")

    while True:
        value = input("请选择环境编号: ").strip()
        if not value.isdigit():
            print("请输入数字编号")
            continue
        i = int(value)
        if i < 1 or i > len(envs):
            print("编号超出范围")
            continue
        return envs[i - 1]


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


def enforce_runtime_size_limit(
    runtime_root: str,
    max_bytes: int,
    protected_dir: str,
    logger: Optional[logging.Logger] = None,
) -> None:
    if not os.path.isdir(runtime_root):
        return

    def log_info(msg: str, *args: object) -> None:
        if logger:
            logger.info(msg, *args)

    def log_warning(msg: str, *args: object) -> None:
        if logger:
            logger.warning(msg, *args)
        else:
            print("⚠️ " + (msg % args if args else msg))

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
            break

        oldest = sorted(candidates, key=lambda p: os.path.getmtime(p))[0]
        oldest_mtime = datetime.fromtimestamp(os.path.getmtime(oldest)).isoformat(timespec="seconds")
        oldest_size = get_directory_size(oldest)
        log_info(
            "准备删除候选目录: path=%s, mtime=%s, size=%s bytes",
            oldest,
            oldest_mtime,
            oldest_size,
        )

        try:
            shutil.rmtree(oldest)
        except Exception as exc:
            log_warning("删除目录失败: path=%s, error=%r", oldest, exc)
            break

        current_total = get_directory_size(runtime_root)
        log_info("目录删除完成，当前 runtime 总大小: %s bytes", current_total)

    final_total = get_directory_size(runtime_root)
    if final_total > max_bytes:
        log_warning(
            "runtime 目录仍超限: current=%s bytes, limit=%s bytes，请手动清理。",
            final_total,
            max_bytes,
        )


def main() -> None:
    selected = choose_environment()
    env = get_env(selected)
    logger.info("已选择环境: %s", selected)

    for _var_name, spec_name in env.image_vars.items():
        if spec_name not in image_specs:
            raise KeyError(f"环境引用的镜像 name 不存在: {spec_name}")

    link_overrides = ask_package_link_overrides(env.image_vars, image_specs)

    client = HDFSClient(base_url="https://hdfs-ngx1.turing-ci.hisilicon.com", verify_ssl=False)

    runtime_root = os.path.join(os.getcwd(), "runtime")
    os.makedirs(runtime_root, exist_ok=True)
    run_dir = os.path.join(runtime_root, run_id)
    os.makedirs(run_dir, exist_ok=True)
    logger.info("本次执行目录: %s", run_dir)

    # 三元组结构：(var_name, spec_name, real_name)
    render_triples: List[Tuple[str, str, str]] = []
    downloaded_local_files: List[str] = []

    for var_name, spec_name in env.image_vars.items():
        spec = image_specs[spec_name]
        if spec_name in link_overrides:
            spec.link = link_overrides[spec_name]

        logger.info("开始下载 %s -> name=%s", var_name, spec.name)
        real_name = fetch_and_download_image(client, spec, run_dir)
        local_path = os.path.join(run_dir, real_name)

        image_ctx = DownloadedImage(
            var_name=var_name,
            spec_name=spec_name,
            real_name=real_name,
            local_path=local_path,
        )
        render_triples.append((image_ctx.var_name, image_ctx.spec_name, image_ctx.real_name))
        downloaded_local_files.append(image_ctx.local_path)

        logger.info(
            "映射三元组: (%s, %s, %s)",
            image_ctx.var_name,
            image_ctx.spec_name,
            image_ctx.real_name,
        )

    rendered = render_script(env.script_template, render_triples)
    script_name = f"{env.env_name}_{int(time.time())}.sh"
    script_path = os.path.join(run_dir, script_name)
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(rendered)
    os.chmod(script_path, 0o755)
    logger.info("脚本已生成: %s", script_path)

    enforce_runtime_size_limit(runtime_root, RUNTIME_MAX_BYTES, protected_dir=run_dir, logger=logger)
    logger.info("runtime 目录容量控制完成（上限 1GB）")

    host = ask_target_host()
    defaults = get_ssh_defaults(selected)
    username, password, port = ask_ssh_credentials(
        default_username=str(defaults["username"]),
        default_password=str(defaults["password"]),
        default_port=int(defaults["port"]),
    )

    logger.info("准备上传到: %s@%s:%s", username, host, port)
    upload_files_via_scp(
        host=host,
        local_files=[*downloaded_local_files, script_path],
        username=username,
        password=password,
        port=port,
    )
    logger.info("上传完成，目标目录: /root/autoEnv")

    print("\n✅ 所有文件上传成功")
    print(f"请登录服务器执行: source /root/autoEnv/{script_name}")


if __name__ == "__main__":
    main()
