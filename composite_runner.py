import os
import time
from datetime import datetime
from typing import List, Sequence, Tuple

from config_loader import load_image_specs
from env_config import get_composite_env, get_env
from logger import setup_logger
from main import RUNTIME_MAX_BYTES, ask_package_link_overrides, ask_target_host, enforce_runtime_size_limit
from models import DownloadedImage
from renderer import render_script
from tools import HDFSClient, fetch_and_download_image, upload_files_via_scp


def run_one_environment(env_name: str) -> Tuple[str, str]:
    """
    执行单个已注册环境。
    返回：(run_dir, script_name)
    """
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = setup_logger(run_id=f"{run_id}_{env_name}")
    logger.info("开始执行组合环境中的子环境: %s", env_name)

    image_specs = load_image_specs("config.json")
    env = get_env(env_name)

    for _var_name, spec_name in env.image_vars.items():
        if spec_name not in image_specs:
            raise KeyError(f"环境 {env_name} 引用的镜像 name 不存在: {spec_name}")

    # 每个子环境都要求用户单独输入 link 覆盖
    link_overrides = ask_package_link_overrides(env.image_vars, image_specs)

    client = HDFSClient(base_url="https://hdfs-ngx1.turing-ci.hisilicon.com", verify_ssl=False)

    runtime_root = os.path.join(os.getcwd(), "runtime")
    os.makedirs(runtime_root, exist_ok=True)
    run_dir = os.path.join(runtime_root, f"{run_id}_{env_name}")
    os.makedirs(run_dir, exist_ok=True)

    render_triples: List[Tuple[str, str, str]] = []
    downloaded_local_files: List[str] = []

    for var_name, spec_name in env.image_vars.items():
        spec = image_specs[spec_name]
        if spec_name in link_overrides:
            spec.link = link_overrides[spec_name]

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

        logger.info("映射三元组: (%s, %s, %s)", image_ctx.var_name, image_ctx.spec_name, image_ctx.real_name)

    rendered = render_script(env.script_template, render_triples)
    script_name = f"{env.env_name}_{int(time.time())}.sh"
    script_path = os.path.join(run_dir, script_name)
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(rendered)
    os.chmod(script_path, 0o755)

    enforce_runtime_size_limit(runtime_root, RUNTIME_MAX_BYTES, protected_dir=run_dir)

    # 每个子环境都要求用户单独输入目标服务器
    target_host = ask_target_host()
    upload_files_via_scp(host=target_host, local_files=[*downloaded_local_files, script_path])

    logger.info("子环境执行完成: %s | run_dir=%s | script=%s", env_name, run_dir, script_name)
    return run_dir, script_name


def run_composite_environments(env_sequence: Sequence[str]) -> None:
    """
    组合执行：按数组顺序依次执行多个已注册环境。
    每个子环境都会独立询问 link 覆盖和目标服务器。
    """
    if not env_sequence:
        raise ValueError("env_sequence 不能为空")

    print("=== 开始组合环境执行 ===")
    for index, env_name in enumerate(env_sequence, start=1):
        print(f"\n[{index}/{len(env_sequence)}] 执行子环境: {env_name}")
        run_dir, script_name = run_one_environment(env_name)
        print(f"✅ 子环境 {env_name} 完成：{run_dir}/{script_name}")

    print("\n🎉 组合环境执行完成")


if __name__ == "__main__":
    # 示例：按顺序组合执行多个环境。
    # 你可以按需改成自己的环境名数组。
    run_composite_environments(get_composite_env("A_B_CHAIN_RUN"))
