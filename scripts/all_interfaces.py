"""四类公共接口示例；include_hdfs=true 时访问 config.json 的真实 HDFS。"""

import shlex

from autoenv import CommandStatus, create_file, get_file, get_remote_info, hdfs_download, init_script

SCRIPT_ARGUMENTS = [
    {"name": "remote_dir", "type": "str", "default": "/tmp/autoenv", "description": "SSH 文件目录"},
    {"name": "local_dir", "type": "str", "required": True, "description": "本地输入目录"},
    {"name": "filename", "type": "str", "required": True, "description": "精确本地文件名"},
    {"name": "include_hdfs", "type": "bool", "default": False, "description": "是否下载 A1"},
]


def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("示例组网", "设备-ssh")])
    console = get_remote_info("telnet", [("示例组网", "设备-串口")])
    ftp = get_remote_info("ftp", [("示例组网", "文件-ftp")])

    local = get_file(paths.arguments["local_dir"], paths.arguments["filename"])
    shell = create_file(paths.runtime_path, "start.sh", "#!/bin/sh\nprintf 'READY\\n'\n")
    binary = create_file(paths.runtime_path, "payload.bin", b"\x00\xff\x02\n")
    remote_dir = paths.arguments["remote_dir"]
    if paths.arguments["include_hdfs"]:
        package = hdfs_download(paths.runtime_path, "A1")
        dut.upload(package, remote_dir)
    dut.upload(local, remote_dir)
    dut.upload(shell, remote_dir)
    downloaded = dut.download(remote_dir, local.filename)
    ftp.upload(downloaded, "/incoming")
    ftp.upload(binary, "/incoming")
    ftp_file = ftp.download("/incoming", binary.filename)
    print("FTP 下载到", ftp_file.path)

    result = dut.execute("sh " + shlex.quote(remote_dir.rstrip("/") + "/" + shell.filename))
    if result.status != CommandStatus.SUCCESS:
        return result
    if "READY" not in result.output:
        return result.with_failure("启动输出没有 READY")
    print(dut.run("printf 'CHECKED\\n'"))

    check = console.execute("status")
    if check.status == CommandStatus.RESULT_UNKNOWN:
        if "READY" not in check.output:
            return check.with_failure("串口输出无法证明 READY")
        print("串口 READY 业务判定通过；协议退出码未知")
    elif check.status != CommandStatus.SUCCESS:
        return check

    console.mark_output()
    reboot = dut.execute("reboot", expect_disconnect=True)
    if reboot.status != CommandStatus.SUCCESS:
        return reboot
    print(console.telnet_wait_exec("Press Ctrl+B", b"\x02"))
    # 模拟设备约定 Ctrl+B 后发 BOOT READY；真实设备请替换为自己的模式/启动标志。
    print(console.wait_reboot("BOOT READY"))
    print(console.read())
    dut.retry(1, 5)
    ftp.retry(1, 3)
    console.close()
    ftp.close()
    dut.close()
    paths.close()  # CLI 宿主会完成结果落盘并最终关闭 recorder；独立调用也可显式释放。


if __name__ == "__main__":
    import sys
    from autoenv.cli import main as cli_main

    raise SystemExit(cli_main(["run", __file__, *sys.argv[1:]]))
