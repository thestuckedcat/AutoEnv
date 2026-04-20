import os
import shutil
import time
from datetime import datetime
from typing import Dict, List, Tuple

from config_loader import load_image_specs
from env_config import get_env, list_env_names
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


def main() -> None:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = setup_logger(run_id=run_id)
    image_specs = load_image_specs("config.json")

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

    enforce_runtime_size_limit(runtime_root, RUNTIME_MAX_BYTES, protected_dir=run_dir)
    logger.info("runtime 目录容量控制完成（上限 1GB）")

    host = ask_target_host()
    logger.info("准备上传到: %s", host)
    upload_files_via_scp(host=host, local_files=[*downloaded_local_files, script_path])
    logger.info("上传完成，目标目录: /root/autoEnv")

    print("\n✅ 所有文件上传成功")
    print(f"请登录服务器执行: source /root/autoEnv/{script_name}")


if __name__ == "__main__":
    main()
