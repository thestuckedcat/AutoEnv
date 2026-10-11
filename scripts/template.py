"""复制此文件，替换白名单与流程；无需装饰器或 ctx。"""

from autoenv import CommandStatus, create_file, get_remote_info, init_script

SCRIPT_ARGUMENTS = [
    {"name": "remote_dir", "type": "str", "default": "/tmp/autoenv", "description": "远端目录"},
]


def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("示例组网", "设备-ssh")])
    file = create_file(paths.runtime_path, "deploy.sh", "#!/bin/sh\nprintf 'READY\\n'\n")
    dut.upload(file, paths.arguments["remote_dir"])
    result = dut.execute("printf 'READY\\n'")
    if result.status != CommandStatus.SUCCESS:
        return result
    if "READY" not in result.output:
        return result.with_failure("输出没有 READY")
    print("部署流程完成")


if __name__ == "__main__":
    import sys
    from autoenv.cli import main as cli_main

    raise SystemExit(cli_main(["run", __file__, *sys.argv[1:]]))
