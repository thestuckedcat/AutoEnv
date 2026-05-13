from __future__ import annotations

import getpass
import os
import shutil
import time
from datetime import datetime
from typing import Dict, List, Sequence, Tuple

from config_loader import load_image_specs
from env_config import get_env, get_ftp_defaults, get_ssh_defaults, get_telnet_defaults
from env_processes import default_environment_process
from logger import setup_logger
from models import DownloadedImage, EnvironmentSpec, ImageSpec, normalize_image_var_ref
from renderer import render_script
from telnet import run_telnet_commands
from tools import (
    HDFSClient,
    fetch_and_download_image,
    run_ssh_commands,
    upload_file_via_ftp,
    upload_files_via_ftp,
    upload_files_via_scp,
)
from unextract import extract_target_files

RUNTIME_MAX_BYTES = 1 * 1024 * 1024 * 1024  # 1GB


def _normalize_target_files(target_file: str | Sequence[str] | None) -> List[str]:
    if target_file is None:
        return []
    if isinstance(target_file, str):
        return [target_file] if target_file else []
    return [str(item) for item in target_file if str(item)]


def ask_upload_protocol(default_protocol: str) -> str:
    protocol = input(f"请选择发包方式 scp/ftp [默认:{default_protocol}]: ").strip().lower()
    protocol = protocol or default_protocol.lower()
    if protocol not in {"scp", "ftp"}:
        print("⚠️ 发包方式非法，使用默认方式")
        return default_protocol.lower()
    return protocol


def ask_ftp_credentials(
    default_username: str,
    default_password: str,
    default_port: int,
    default_remote_path: str,
) -> Tuple[str, str, int, str]:
    username = input(f"请输入 FTP 用户名 [默认:{default_username}]: ").strip() or default_username
    password = getpass.getpass("请输入 FTP 密码 [回车使用默认密码]: ").strip() or default_password

    port_input = input(f"请输入 FTP 端口 [默认:{default_port}]: ").strip()
    if not port_input:
        port = default_port
    elif port_input.isdigit():
        port = int(port_input)
    else:
        print("⚠️ FTP 端口输入非法，使用默认端口")
        port = default_port

    remote_path = input(f"请输入 FTP 目标目录 [默认:{default_remote_path}]: ").strip() or default_remote_path
    return username, password, port, remote_path


def ask_telnet_run(
    default_host: str,
    default_port: int,
    default_timeout: float,
    script_name: str,
) -> Tuple[bool, str, int, float, List[str]]:
    enabled = input("是否通过 Telnet 串口逐条发送执行指令？y/N [默认:N]: ").strip().lower()
    if enabled not in {"y", "yes"}:
        return False, default_host, default_port, default_timeout, []

    host = input(f"请输入 Telnet 串口服务器 IP/域名 [默认:{default_host}]: ").strip() or default_host
    port_input = input(f"请输入 Telnet 端口 [默认:{default_port}]: ").strip()
    port = int(port_input) if port_input.isdigit() else default_port
    timeout_input = input(f"请输入单条命令超时时间秒 [默认:{default_timeout}]: ").strip()
    try:
        timeout = float(timeout_input) if timeout_input else default_timeout
    except ValueError:
        print("⚠️ Telnet 超时时间非法，使用默认值")
        timeout = default_timeout

    command = input(f"请输入 Telnet 执行命令 [默认:source /root/autoEnv/{script_name}]: ").strip()
    commands = [command or f"source /root/autoEnv/{script_name}"]
    return True, host, port, timeout, commands


def ask_target_host(default_host: str) -> str:
    host = input(f"请输入目标服务器 IP/域名 [默认:{default_host}]: ").strip()
    return host or default_host


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


def ask_package_link_overrides(image_vars: Dict[str, object], image_specs: Dict[str, ImageSpec]) -> Dict[str, str]:
    """按环境依赖的 spec_name 让用户可选覆盖下载路径。"""
    spec_names = sorted({normalize_image_var_ref(value).spec_name for value in image_vars.values()})
    overrides: Dict[str, str] = {}

    print("\n请选择驱动包路径（直接回车将使用 config.json 默认路径）")
    for spec_name in spec_names:
        spec = image_specs[spec_name]
        if spec.link:
            default_hint = spec.link
        elif spec.base_link:
            default_hint = f"<自动 newest from base_link={spec.base_link}>"
        else:
            default_hint = "<必填: link/base_link 均为空>"

        while True:
            user_input = input(f"- {spec_name} 路径 [默认: {default_hint}]: ").strip()
            if user_input:
                normalized = user_input.rstrip("/")
                if not normalized.startswith("/"):
                    normalized = "/" + normalized
                overrides[spec_name] = normalized
                break

            if spec.link or spec.base_link:
                break

            print(f"❌ {spec_name} 未配置默认路径，请输入可用目录路径")

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


class EnvironmentProcessContext:
    """环境 process 的可扩展操作上下文。

    自定义 process 可以通过这些方法编排：下载/解包、渲染脚本、SCP/FTP 上传、
    单文件发到 Telnet 可访问位置，以及 SSH/Telnet 命令执行。
    """

    def __init__(
        self,
        *,
        env: EnvironmentSpec,
        env_name: str,
        run_dir: str,
        downloaded_images: Dict[str, DownloadedImage],
        script_paths: Dict[str, str],
        upload_files: List[str],
        logger,
        image_specs: Dict[str, ImageSpec] | None = None,
        hdfs_client: HDFSClient | None = None,
    ) -> None:  # type: ignore[no-untyped-def]
        self.env = env
        self.env_name = env_name
        self.run_dir = run_dir
        self.downloaded_images = downloaded_images
        self.script_paths = script_paths
        self.upload_files = upload_files
        self.logger = logger
        self.image_specs = image_specs or {}
        self.hdfs_client = hdfs_client

    @property
    def main_script_path(self) -> str:
        return next(iter(self.script_paths.values()))

    @property
    def main_script_name(self) -> str:
        return os.path.basename(self.main_script_path)

    def get_image_path(self, var_name: str, *, selected: bool = True) -> str:
        image = self.downloaded_images[var_name]
        return image.selected_local_path if selected else image.local_path

    def download_image_var(
        self,
        var_name: str,
        spec_name: str,
        *,
        target_file: str | Sequence[str] | None = None,
        link_override: str | None = None,
    ) -> DownloadedImage:
        """从 WebHDFS 拉取指定包，并按需解压 target_file。

        该接口供自定义 process 在默认预下载之外，按远端输出决定是否继续拉包。
        """
        if not self.hdfs_client:
            raise RuntimeError("当前 context 未配置 HDFS client，无法下载包")
        if spec_name not in self.image_specs:
            raise KeyError(f"镜像 name 不存在: {spec_name}")

        spec = self.image_specs[spec_name]
        if link_override:
            spec.link = link_override

        self.logger.info("process 开始下载 %s -> name=%s", var_name, spec.name)
        real_name = fetch_and_download_image(self.hdfs_client, spec, self.run_dir)
        local_path = os.path.join(self.run_dir, real_name)
        image_ctx = DownloadedImage(
            var_name=var_name,
            spec_name=spec_name,
            real_name=real_name,
            local_path=local_path,
            target_file=target_file,
            selected_local_path=local_path,
            selected_real_name=real_name,
        )
        self.upload_files.append(local_path)

        target_files = _normalize_target_files(target_file) or spec.target_file
        if target_files:
            extracted_files = extract_target_files(local_path, self.run_dir, target_files)
            image_ctx.extracted_paths.extend(extracted_files)
            self.upload_files.extend(extracted_files)
            if target_file is not None and extracted_files:
                image_ctx.selected_local_path = extracted_files[0]
                image_ctx.selected_real_name = os.path.basename(extracted_files[0])

        self.downloaded_images[var_name] = image_ctx
        return image_ctx

    def extract_package_targets(self, package_path: str, target_files: Sequence[str]) -> List[str]:
        extracted = extract_target_files(package_path, self.run_dir, target_files)
        self.upload_files.extend(extracted)
        return extracted

    def render_template(self, template_name: str, template: str, mappings: Sequence[Tuple[str, str, str]]) -> str:
        rendered = render_script(template, mappings)
        script_name = f"{self.env.env_name}_{template_name}_{int(time.time())}.sh"
        script_path = os.path.join(self.run_dir, script_name)
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)
        os.chmod(script_path, 0o755)
        self.script_paths[template_name] = script_path
        self.upload_files.append(script_path)
        return script_path

    def upload_files_scp(
        self,
        host: str,
        username: str,
        password: str,
        *,
        remote_path: str = "/root/autoEnv",
        port: int = 22,
        files: Sequence[str] | None = None,
    ) -> None:
        upload_files_via_scp(host, files or self.upload_files, username, password, remote_path, port)

    def upload_files_ftp(
        self,
        host: str,
        username: str,
        password: str,
        *,
        remote_path: str = "/root/autoEnv",
        port: int = 21,
        files: Sequence[str] | None = None,
    ) -> None:
        upload_files_via_ftp(host, files or self.upload_files, username, password, remote_path, port)

    def upload_file_to_telnet_path(
        self,
        host: str,
        local_file: str,
        remote_file_path: str,
        username: str,
        password: str,
        *,
        port: int = 21,
        timeout: int = 30,
    ) -> None:
        upload_file_via_ftp(host, local_file, username, password, remote_file_path, port, timeout)

    def send_telnet_commands(
        self,
        host: str,
        commands: Sequence[str],
        *,
        port: int = 23,
        timeout: float = 30.0,
        log_path: str | None = None,
    ) -> List[str]:
        return run_telnet_commands(host, commands, port=port, timeout=timeout, log_path=log_path, logger=self.logger)

    def send_ssh_commands(
        self,
        host: str,
        commands: Sequence[str],
        username: str,
        password: str,
        *,
        port: int = 22,
        timeout: int = 30,
    ) -> List[str]:
        return run_ssh_commands(host, commands, username, password, port, timeout)

    def default_upload(self) -> None:
        upload_protocol = ask_upload_protocol(self.env.upload_protocol)

        if upload_protocol == "ftp":
            telnet_defaults = get_telnet_defaults(self.env_name)
            default_host = str(telnet_defaults["host"])
            ftp_defaults = get_ftp_defaults(self.env_name)
            host = ask_target_host(default_host=default_host)
            username, password, port, remote_path = ask_ftp_credentials(
                default_username=str(ftp_defaults["username"]),
                default_password=str(ftp_defaults["password"]),
                default_port=int(ftp_defaults["port"]),
                default_remote_path=str(ftp_defaults["remote_path"]),
            )
            self.logger.info("准备通过 FTP 上传到 Telnet 服务器: %s@%s:%s%s", username, host, port, remote_path)
            self.upload_files_ftp(host, username, password, port=port, remote_path=remote_path)
            self.logger.info("FTP 上传完成，目标目录: %s", remote_path)
            return

        defaults = get_ssh_defaults(self.env_name)
        host = ask_target_host(default_host=str(defaults["host"]))
        username, password, port = ask_ssh_credentials(
            default_username=str(defaults["username"]),
            default_password=str(defaults["password"]),
            default_port=int(defaults["port"]),
        )

        self.logger.info("准备通过 SCP 上传到: %s@%s:%s", username, host, port)
        self.upload_files_scp(host, username, password, port=port)
        self.logger.info("SCP 上传完成，目标目录: /root/autoEnv")

    def default_telnet_run(self) -> None:
        telnet_defaults = get_telnet_defaults(self.env_name)
        telnet_enabled, telnet_host, telnet_port, telnet_timeout, telnet_commands = ask_telnet_run(
            default_host=str(telnet_defaults["host"]),
            default_port=int(telnet_defaults["port"]),
            default_timeout=float(telnet_defaults["timeout"]),
            script_name=self.main_script_name,
        )
        if not telnet_enabled:
            return

        commands = [
            command.replace("${script_name}", self.main_script_name)
            for command in (self.env.telnet_commands or telnet_commands)
        ]
        command_log = os.path.join(self.run_dir, "telnet_commands.log")
        self.logger.info("开始通过 Telnet 逐条发送命令: %s", commands)
        self.send_telnet_commands(telnet_host, commands, port=telnet_port, timeout=telnet_timeout, log_path=command_log)
        self.logger.info("Telnet 命令执行完成，输出日志: %s", command_log)


def execute_environment(env_name: str, *, runtime_suffix: str | None = None) -> Tuple[str, str]:
    """执行一个已注册环境，返回 (run_dir, main_script_name)。"""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_label = f"{run_id}_{runtime_suffix}" if runtime_suffix else run_id
    logger = setup_logger(run_id=run_label)

    image_specs = load_image_specs("config.json")
    env = get_env(env_name)
    logger.info("已选择环境: %s", env_name)

    image_refs = {var_name: normalize_image_var_ref(value) for var_name, value in env.image_vars.items()}
    for _var_name, ref in image_refs.items():
        if ref.spec_name not in image_specs:
            raise KeyError(f"环境引用的镜像 name 不存在: {ref.spec_name}")

    link_overrides = ask_package_link_overrides(env.image_vars, image_specs)

    client = HDFSClient(base_url="https://hdfs-ngx1.turing-ci.hisilicon.com", verify_ssl=False)

    runtime_root = os.path.join(os.getcwd(), "runtime")
    os.makedirs(runtime_root, exist_ok=True)
    run_dir_name = run_label if runtime_suffix else run_id
    run_dir = os.path.join(runtime_root, run_dir_name)
    os.makedirs(run_dir, exist_ok=True)
    logger.info("本次执行目录: %s", run_dir)

    render_triples: List[Tuple[str, str, str]] = []
    downloaded_images: Dict[str, DownloadedImage] = {}
    upload_files: List[str] = []

    for var_name, ref in image_refs.items():
        spec = image_specs[ref.spec_name]
        if ref.spec_name in link_overrides:
            spec.link = link_overrides[ref.spec_name]

        logger.info("开始下载 %s -> name=%s", var_name, spec.name)
        real_name = fetch_and_download_image(client, spec, run_dir)
        local_path = os.path.join(run_dir, real_name)
        logger.info("下载完成 %s，本地真实路径: %s", var_name, local_path)

        image_ctx = DownloadedImage(
            var_name=var_name,
            spec_name=ref.spec_name,
            real_name=real_name,
            local_path=local_path,
            target_file=ref.target_file,
            selected_local_path=local_path,
            selected_real_name=real_name,
        )
        upload_files.append(image_ctx.local_path)

        target_files = _normalize_target_files(ref.target_file) or spec.target_file
        if target_files:
            logger.info("开始解压并提取 target_file: %s", target_files)
            extracted_files = extract_target_files(image_ctx.local_path, run_dir, target_files)
            image_ctx.extracted_paths.extend(extracted_files)
            upload_files.extend(extracted_files)
            if ref.target_file is not None and extracted_files:
                image_ctx.selected_local_path = extracted_files[0]
                image_ctx.selected_real_name = os.path.basename(extracted_files[0])
            logger.info("target_file 提取完成: %s", extracted_files)

        render_triples.append((image_ctx.var_name, image_ctx.spec_name, image_ctx.selected_real_name))
        downloaded_images[var_name] = image_ctx

        logger.info(
            "映射三元组: (%s, %s, %s)",
            image_ctx.var_name,
            image_ctx.spec_name,
            image_ctx.selected_real_name,
        )

    script_paths: Dict[str, str] = {}
    templates = env.get_script_templates()
    if not templates:
        raise ValueError(f"环境 {env_name} 未配置 script_templates")
    for template_name, template in templates.items():
        rendered = render_script(template, render_triples)
        suffix = "" if len(templates) == 1 and template_name == "main" else f"_{template_name}"
        script_name = f"{env.env_name}{suffix}_{int(time.time())}.sh"
        script_path = os.path.join(run_dir, script_name)
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(rendered)
        os.chmod(script_path, 0o755)
        script_paths[template_name] = script_path
        upload_files.append(script_path)
        logger.info("脚本已生成: %s", script_path)

    enforce_runtime_size_limit(runtime_root, RUNTIME_MAX_BYTES, protected_dir=run_dir)
    logger.info("runtime 目录容量控制完成（上限 1GB）")

    context = EnvironmentProcessContext(
        env=env,
        env_name=env_name,
        run_dir=run_dir,
        downloaded_images=downloaded_images,
        script_paths=script_paths,
        upload_files=upload_files,
        logger=logger,
        image_specs=image_specs,
        hdfs_client=client,
    )
    process = env.process or default_environment_process
    process(context)

    print("\n✅ 所有文件上传成功")
    print(f"请登录服务器执行: source /root/autoEnv/{context.main_script_name}")
    return run_dir, context.main_script_name
