# AutoEnv 快速开始

## 最简实现

要求 Python 3.11+，在仓库根目录安装：

```powershell
python -m pip install -e ".[test]"
python -X utf8 -m autoenv.cli list --json
```

打开常用网页：`python -X utf8 startWeb.py`，固定访问 `http://127.0.0.1:8765/`。先登记一级组网和各 SSH/Telnet/FTP 连接，再选择脚本及绑定，页面会按最新静态声明提供输入，并调用真实 CLI。

在 `scripts/demo.py` 创建普通文件：

```python
from autoenv import create_file, get_remote_info, init_script

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("单机直连", "设备-ssh")])
    file = create_file(paths.runtime_path, "hello.sh", "#!/bin/sh\necho READY\n")
    dut.upload(file, "/tmp/demo")
    print(dut.run("echo READY"))
```

不用注册装饰器。先静态检查，再完整指定执行参数：

```powershell
python -X utf8 -m autoenv.cli describe demo --json
python -X utf8 -m autoenv.cli run demo --non-interactive --topology "单机直连" --bind "dut=设备-ssh"
```

执行会访问绑定的设备。完成 stdout 只有 run.log 绝对路径，退出码 0/1/2 分别为成功/执行失败/准备失败。`run.log` 包含脚本源码位置、动作、结果、远端输出和 print；同目录 `output.log` 只记录每条动作的简洁结果，网页实时显示它。

## 补充功能

- 本地文件：`get_file(directory, filename)`；生成：`create_file(directory, filename, str或bytes)`；HDFS：`hdfs_download(local_directory, "A1")`。三者返回统一 FileHandle。
- SSH/FTP 上传：`remote.upload(handle, remote_directory)`，成功返回 None；下载：`remote.download(remote_directory, filename)` 返回本地 handler。SSH `upload_type` 在环境配置中选 scp/sftp。
- SSH/Telnet `run(command)` 成功返回 str，失败/未知抛异常；需要分支使用 `execute()` 的 status/exit_code/output。Telnet prompt 模式无标准退出码，应根据明确业务关键词判断。
- 串口关键词：`console.telnet_wait_exec("Press Ctrl+B", b"\x02")`；`wait_reboot(keyword)` 被动等待；`read()` 读未消费输出。外部重启前 `mark_output()`，网口 `retry(interval, max_try_times)` 只重连自检。
- 参数：顶层 `SCRIPT_ARGUMENTS` 声明，`paths.arguments[name]` 读取；CLI `--arg name=value`。多个远端对象各有白名单，本次一级组网只选一次；交互回车仅继承有效历史。

最小例子：[example.py](../scripts/example.py)；可复制模板：[template.py](../scripts/template.py)；全部接口：[all_interfaces.py](../scripts/all_interfaces.py)。

发布示例的 `if __name__ == "__main__"` 转发同一个 CLI，因此 `python scripts/example.py --non-interactive ...` 也有完整日志捕获/释放。CLI执行脚本时跳过这个guard再调用main，不递归。

## 组件与测试

```powershell
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py
python -X utf8 -m compileall autoenv scripts webPage
python -X utf8 -m pytest
```

无需设备的实际协议演示与命令结果见 [LOCAL_SIMULATION.md](LOCAL_SIMULATION.md)。环境配置见 [ENVIRONMENT_REGISTRATION_GUIDE.md](ENVIRONMENT_REGISTRATION_GUIDE.md)；接口/入参出参见 [SCRIPT_API_ANALYSIS.md](SCRIPT_API_ANALYSIS.md)；整体决定见 [重构计划](REFACTOR_PLAN_FOUR_API_CATEGORIES.md)；Agent 生成规则见 [脚本 skill](../.agents/skills/autoenv-script-generator/SKILL.md)。
