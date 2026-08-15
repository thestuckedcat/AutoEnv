"""Copyable examples for registering standalone and combined environments."""

from autoenv import (
    CommandStatus,
    SSHDefaults,
    TelnetDefaults,
    extra_file,
    match,
    package,
    register_func,
    register_script,
)


@register_script(
    name="example_host_environment",
    description="Example: download, extract, upload and run SSH commands",
)
def example_host_environment(ctx):
    # Declare every file selector and connection object in one place. The ordered
    # workflow below reuses these names instead of reconstructing selectors.
    ubengine_run = package(
        "A1",
        alias="A1 主安装包",
        description="从 HDFS 下载并上传到 1260 主机的安装包。",
    )
    manual_bundle = extra_file("manual_bundle.tar.gz")
    driver_file = extra_file("driver.bin")
    firmware_image = match(r"^firmware-.*\.bin$")
    install_script = extra_file("install.sh")
    host_1260 = ctx.register_ssh_host(
        "example_host",
        resource_label="1260网口",
        alias="1260 管理网口",
        description="用于上传安装包、执行安装命令并检查 READY 状态。",
        defaults=SSHDefaults(
            host="192.0.2.10",
            port=22,
            username="root",
            password="",
            connect_timeout=30.0,
        ),
    )

    result = ctx.download_package(ubengine_run)
    if not result.success:
        return result

    # Extraction is always explicit. Change the target to a real file in A1.
    result = ctx.extract_file_from(
        source=ubengine_run,
        target_file="driver.bin",
    )
    if not result.success:
        return result

    # target_dir is mutually exclusive with target_file. This manual archive must
    # already be present in the packages directory printed at script startup.
    result = ctx.extract_file_from(
        source=manual_bundle,
        target_dir="firmware",
    )
    if not result.success:
        return result

    result = host_1260.scp_upload(
        local_file=ubengine_run,
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    result = host_1260.sftp_upload(
        local_file=driver_file,
        remote_dir="/root/autoEnv",
        overwrite=True,
    )
    if not result.success:
        return result

    result = host_1260.sftp_upload(
        local_file=firmware_image,
        remote_dir="/root/autoEnv/firmware",
        overwrite=False,
    )
    if not result.success:
        return result

    ctx.generate_sh_file(
        "install.sh",
        """#!/bin/sh
set -e
cd /root/autoEnv
chmod +x "S{A1}"
./"S{A1}"
""",
    )

    result = host_1260.sftp_upload(
        local_file=install_script,
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    result = host_1260.execute(
        "bash /root/autoEnv/S{install.sh}",
        timeout=600,
    )
    if not result.success:
        return result

    status = host_1260.execute("cat /tmp/env_status", timeout=30)
    if status.success and "READY" not in status.output:
        return status.with_failure("environment status is not READY")
    if not status.success:
        return status

    # Register reusable post-start flows only after the main environment is ready.
    # They reuse this run's context and the already registered/connected host.
    @register_func(
        name="check_environment_status",
        description="Read and validate the environment READY marker",
    )
    def check_environment_status(_func_ctx):
        result = host_1260.execute("cat /tmp/env_status", timeout=30)
        if result.success and "READY" not in result.output:
            return result.with_failure("environment status is not READY")
        return result

    @register_func(
        name="list_environment_files",
        description="List files uploaded to /root/autoEnv",
    )
    def list_environment_files(_func_ctx):
        return host_1260.execute("ls -la /root/autoEnv", timeout=30)

    return status


@register_script(
    name="example_console_environment",
    description="Example: Telnet auto detection and a timed Ctrl+B response",
)
def example_console_environment(ctx):
    console = ctx.register_telnet(
        "example_console",
        resource_label="1260串口",
        alias="1260 调试串口",
        description="用于启动从环境并在启动提示出现时发送 Ctrl+B。",
        defaults=TelnetDefaults(
            host="192.0.2.20",
            port=23,
            timeout=30.0,
            shell_mode="auto",
        ),
    )

    result = console.execute(
        "source /root/start_slave.sh",
        timeout=300,
    )
    if result.status == CommandStatus.RESULT_UNKNOWN:
        if "READY" not in result.output:
            return result.with_failure("Telnet output does not contain READY")
    elif not result.success:
        return result

    return console.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
        timeout=60,
    )


@register_script(
    name="example_combined_environment",
    description="Example: run two registered scripts serially and independently",
)
def example_combined_environment(_ctx):
    host_result = example_host_environment()
    if not host_result.success:
        return host_result
    return example_console_environment()
