# AutoEnv

## 最简实现：快速开始

AutoEnv 用普通 Python 顺序流程操作 SSH、Telnet、FTP 与文件。脚本显式申请环境对象和本地 handler，无需注册装饰器或 ctx。

```powershell
python -m pip install -e ".[test]"
python -X utf8 -m autoenv.cli list --json
python -X utf8 startWeb.py
```

网页固定 `http://127.0.0.1:8765/`，登记环境、选择脚本/组网并绑定连接后运行。页面将表单封装为实际 CLI 命令，实时显示每条操作的简洁结果。

```python
from autoenv import create_file, get_remote_info, init_script

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("示例组网", "设备-ssh")])
    file = create_file(paths.runtime_path, "hello.sh", "#!/bin/sh\necho READY\n")
    dut.upload(file, "/tmp/demo")
    print(dut.run("echo READY"))
```

保存为 `scripts/demo.py`，无需 @register_script。CLI 可以一次传完所有参数：

```powershell
python -X utf8 -m autoenv.cli describe demo --json
python -X utf8 -m autoenv.cli run demo --non-interactive --topology "示例组网" --bind "dut=设备-ssh"
```

run/rerun 结束只输出完整 run.log 的绝对路径，退出码 0 成功、1 执行失败、2 准备失败。完整日志记录源码位置、动作、执行结果、远端输出和 print；同目录 output.log 是简洁逐步结果，供网页实时展示。

## 补充功能

四类业务接口：环境对象；精确本地文件 `get_file(path, filename)`；独立 HDFS `hdfs_download(localpath, config_name)`；原样文件生成 `create_file(path, filename, str或bytes)`。SSH/FTP 上传接收 handler，下载返回 handler；SSH 上传方式在环境配置的 upload_type 中选 scp/sftp。Telnet 支持命令、串口读取、关键词等待和精确 Ctrl+B，FTP 支持真实上传/下载。

环境配置集中在 `environments/topologies/<组网>/connections.json`，每个 name 是一个连接。脚本各对象给定类型和白名单，本次一级组网只选一次，各槽位独立绑定；回车继承有效历史。环境申请时自检，retry 只重连自检。SSH/Telnet execute 返回状态、退出码和输出用于分支；run 返回字符串且失败/未知抛异常。

最小示例：[example.py](scripts/example.py)；模板：[template.py](scripts/template.py)；全量用法：[all_interfaces.py](scripts/all_interfaces.py)。详细用法见 [快速开始](docs/QUICK_START.md)、[环境登记](docs/ENVIRONMENT_REGISTRATION_GUIDE.md)、[接口表](docs/SCRIPT_API_ANALYSIS.md) 和 [重构计划](docs/REFACTOR_PLAN_FOUR_API_CATEGORIES.md)。Agent 生成 Python 的规则见 [脚本 skill](.agents/skills/autoenv-script-generator/SKILL.md)。

## 组件与测试

```powershell
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py
python -X utf8 -m compileall autoenv scripts webPage
python -X utf8 -m pytest
```

[本地模拟搭建与命令结果](docs/LOCAL_SIMULATION.md) 使用 loopback SSH/SCP/SFTP、Telnet 与 FTP 真协议，命令只记录，文件实际传输/落盘，不打桩；本轮模拟不访问 HDFS。测试入口/排查见 [tests/README.md](tests/README.md)。原高级日志采集与提取组件保留，但新的常用流程统一通过普通脚本和 CLI。
