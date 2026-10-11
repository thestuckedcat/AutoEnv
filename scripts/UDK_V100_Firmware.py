"""UDK V100：通过同一组网的 BMC 网口和串口替换固件。"""

import math
import re

from autoenv import (
    AutoEnvOperationError, CommandStatus, accurate_search, get_remote_info,
    hdfs_download, init_script,
)

SCRIPT_ARGUMENTS = [
    {"name": "command_timeout", "type": "float", "default": 600.0,
     "description": "串口等待 unions.sh 执行结束的超时秒数"},
]


def main():
    paths = init_script()
    bmc = get_remote_info("ssh", [("SingleUnions_S3", "BMC网口")])
    console = get_remote_info("telnet", [("SingleUnions_S3", "BMC串口")])

    timeout = float(paths.arguments["command_timeout"])
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("command_timeout 必须大于 0")
    for remote in (bmc, console):
        if not remote.available:
            raise AutoEnvOperationError(remote.self_check_result)

    firmware = hdfs_download(paths.runtime_path / "packages" / "firmware", "firmware")
    unzip_path = paths.runtime_path / "firmware"
    firmware.unzip(unzip_path)
    fw_handler = accurate_search(unzip_path, "OnChipAsic.bin", recursive=True)
    if fw_handler is None:
        raise FileNotFoundError(f"固件包中没有 OnChipAsic.bin：{unzip_path}")
    print(f"固件文件：{fw_handler.path}")
    bmc.upload(fw_handler, "/tmp")

    # POSIX 返回码标记证明执行结束；success 只在返回后作额外业务判断。
    result = console.execute("cd /data && sh unions.sh", timeout=timeout)
    if result.status != CommandStatus.SUCCESS:
        return result
    if result.exit_code != 0:
        return result.with_failure("串口结果未提供退出码 0，无法确认 unions.sh 正常结束")
    if re.search(r"(?i)\bsuccess\b", result.stdout) is None:
        return result.with_failure("unions.sh 已结束且退出码为 0，但输出中没有 success")
    print(result.output)
    return result


if __name__ == "__main__":
    import sys
    from autoenv.cli import main as cli_main

    raise SystemExit(cli_main(["run", __file__, *sys.argv[1:]]))
