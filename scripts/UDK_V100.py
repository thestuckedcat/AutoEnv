"""UDK V100：下载包、重启 1260，并上传执行环境启动脚本。"""

import math
import time
import uuid

from autoenv import (
    AutoEnvOperationError, CommandStatus, create_file, get_remote_info,
    hdfs_download, init_script,
)

SCRIPT_ARGUMENTS = [
    {"name": "reboot_timeout", "type": "float", "default": 180.0,
     "description": "等待 1260 SSH 恢复且 boot_id 变化的秒数"},
    {"name": "command_timeout", "type": "float", "default": 300.0,
     "description": "启动脚本和检查命令的超时秒数"},
]


def _positive_timeout(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


def _boot_id(dut, timeout):
    result = dut.execute("cat /proc/sys/kernel/random/boot_id", timeout=timeout)
    if result.status != CommandStatus.SUCCESS:
        raise AutoEnvOperationError(result)
    try:
        return uuid.UUID(result.stdout.strip())
    except ValueError as exc:
        raise ValueError("1260 未返回有效的 Linux boot_id，无法确认重启完成") from exc


def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("SingleUnions_S3", "1260网口")])
    bmc = get_remote_info("ssh", [("SingleUnions_S3", "BMC网口")])
    console = get_remote_info("telnet", [("SingleUnions_S3", "BMC串口")])

    reboot_timeout = _positive_timeout(paths.arguments["reboot_timeout"], "reboot_timeout")
    command_timeout = _positive_timeout(paths.arguments["command_timeout"], "command_timeout")
    for remote in (dut, bmc, console):
        if not remote.available:
            raise AutoEnvOperationError(remote.self_check_result)

    ube_driver = hdfs_download(paths.runtime_path / "packages" / "ube_driver", "ube_driver")
    ube_testcase = hdfs_download(paths.runtime_path / "packages" / "ube_testcase", "ube_testcase")
    udk_drv_v100 = hdfs_download(paths.runtime_path / "packages" / "udk_drv_v100", "udk_drv_v100")
    handler_start_env = create_file(
        paths.runtime_path, "start_env.sh",
        "#!/bin/sh\n# TODO: 在这里填写 UDK V100 安装和环境拉起命令。\n"
        "printf 'TODO: UDK V100 start_env.sh is a placeholder\\n'\n",
    )

    before = _boot_id(dut, command_timeout)
    reboot = dut.execute("reboot", timeout=10, expect_disconnect=True)
    if reboot.status != CommandStatus.SUCCESS:
        return reboot

    deadline = time.monotonic() + reboot_timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("等待 1260 重启完成超时：SSH 未恢复或 boot_id 没有变化")
        try:
            # retry 只重新连接与自检，不能重发 reboot。
            dut.retry(2, 1)
            after = _boot_id(dut, min(command_timeout, remaining))
        except AutoEnvOperationError:
            pass
        else:
            if after != before:
                print(f"1260 重启完成，boot_id: {before} -> {after}")
                break
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(2, remaining))

    print("1260 重启完成后等待 10 秒")
    time.sleep(10)
    print(dut.run("rm -rf /root/autoenv"))
    dut.upload(ube_driver, "/root/autoenv")
    dut.upload(ube_testcase, "/root/autoenv")
    dut.upload(udk_drv_v100, "/root/autoenv")
    dut.upload(handler_start_env, "/root/autoenv")
    started = dut.execute("cd /root/autoenv && sh start_env.sh", timeout=command_timeout)
    if started.status != CommandStatus.SUCCESS:
        return started
    print(started.output)

    checked = dut.execute("ps -ef | grep mami | grep devId", timeout=command_timeout)
    if checked.status != CommandStatus.SUCCESS:
        return checked
    if not checked.stdout.strip():
        return checked.with_failure("未找到同时包含 mami 和 devId 的进程输出")
    print(checked.output)
    return checked


if __name__ == "__main__":
    import sys
    from autoenv.cli import main as cli_main

    raise SystemExit(cli_main(["run", __file__, *sys.argv[1:]]))
