import os
import time
from typing import Dict

from config_loader import load_image_specs
from env_config import get_env, list_env_names
from logger import setup_logger
from models import DownloadedImage
from renderer import render_script
from tools import HDFSClient, fetch_and_download_image, upload_files_via_scp


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


def main() -> None:
    logger = setup_logger()
    image_specs = load_image_specs("config.json")

    selected = choose_environment()
    env = get_env(selected)
    logger.info("已选择环境: %s", selected)

    client = HDFSClient(base_url="https://hdfs-ngx1.turing-ci.hisilicon.com", verify_ssl=False)
    runtime_dir = os.path.join(os.getcwd(), "runtime")
    os.makedirs(runtime_dir, exist_ok=True)

    variable_values: Dict[str, str] = {}
    downloaded_local_files = []

    for var_name, spec_name in env.image_vars.items():
        if spec_name not in image_specs:
            raise KeyError(f"环境引用的镜像 name 不存在: {spec_name}")

        spec = image_specs[spec_name]
        logger.info("开始下载 %s -> name=%s", var_name, spec.name)
        real_name = fetch_and_download_image(client, spec, runtime_dir)
        variable_values[var_name] = real_name
        downloaded_local_files.append(os.path.join(runtime_dir, real_name))

        _ = DownloadedImage(var_name=var_name, spec_name=spec_name, real_name=real_name)
        logger.info("下载完成 %s = %s", var_name, real_name)

    rendered = render_script(env.script_template, variable_values)
    script_name = f"{env.env_name}_{int(time.time())}.sh"
    script_path = os.path.join(runtime_dir, script_name)
    with open(script_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(rendered)
    os.chmod(script_path, 0o755)
    logger.info("脚本已生成: %s", script_path)

    host = ask_target_host()
    logger.info("准备上传到: %s", host)
    upload_files_via_scp(host=host, local_files=[*downloaded_local_files, script_path])
    logger.info("上传完成，目标目录: /root/autoEnv")

    print("\n✅ 所有文件上传成功")
    print(f"请登录服务器执行: source /root/autoEnv/{script_name}")


if __name__ == "__main__":
    main()
