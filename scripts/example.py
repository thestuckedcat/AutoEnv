"""最小 SSH 流程；先在环境页登记 示例组网/设备-ssh。"""

from autoenv import create_file, get_remote_info, init_script

SCRIPT_ARGUMENTS = [
    {"name": "remote_dir", "type": "str", "default": "/tmp/autoenv", "description": "远端上传目录"},
]


def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("示例组网", "设备-ssh")])
    script = create_file(paths.runtime_path, "hello.sh", "#!/bin/sh\nprintf 'READY\\n'\n")
    dut.upload(script, paths.arguments["remote_dir"])
    print(dut.run("printf 'READY\\n'"))


if __name__ == "__main__":
    import sys
    from autoenv.cli import main as cli_main

    raise SystemExit(cli_main(["run", __file__, *sys.argv[1:]]))
