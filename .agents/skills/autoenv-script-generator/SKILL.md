---
name: autoenv-script-generator
description: Generate, adapt, or review an AutoEnv Python workflow from a supplied script or test procedure, using explicit environment objects and file handlers. Applies to scripts/*.py and their environment/package configuration; do not use for unrelated Python utilities.
---

# 快速生成 AutoEnv Python 流程

用户已经提供脚本、步骤或明确要求实现时，直接完成可审查的 Python 文件。沿用已确认的地址、顺序、成功条件和授权，不再要求固定“草案审批”。仅询问实际缺失、会改变执行结果的信息；不知道真实地址或凭据时使用清晰占位并列出缺项。静态生成与本地模拟不等于真实设备执行授权。

## 最简实现

先读取 `docs/QUICK_START.md`、`scripts/example.py`、`autoenv/__init__.py`。需要全部接口时读取 `scripts/all_interfaces.py` 和 `docs/SCRIPT_API_ANALYSIS.md`；环境配置格式见 `docs/ENVIRONMENT_REGISTRATION_GUIDE.md`。当前实现与测试优先于旧记录。

在 `scripts/<name>.py` 写普通零参数 `main()`，不使用 `@register_script`、`ctx`、资源标签或 `S{...}` 占位。CLI 通过文件名发现脚本，不导入或执行它以发现声明。

具体业务例子为 `scripts/UDK_V100.py` 和 `scripts/UDK_V100_Firmware.py`，共用 `SingleUnions_S3`；配置/执行/串口判据见 `docs/UDK_V100_WORKFLOWS.md`。启动 shell 当前按用户要求占位，不能声称它已完成真实安装。

```python
from autoenv import create_file, get_remote_info, init_script

SCRIPT_ARGUMENTS = [{"name": "remote_dir", "type": "str", "default": "/tmp/demo"}]

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("测试组网", "设备-ssh")])
    file = create_file(paths.runtime_path, "start.sh", "#!/bin/sh\nprintf 'READY\\n'\n")
    dut.upload(file, paths.arguments["remote_dir"])
    print(dut.run("printf 'READY\\n'"))
```

第一条流程语句申请 `init_script()`。随后在初始化区直接赋值各固定 `get_remote_info("ssh"|"telnet"|"ftp", [("一级组网", "连接名"), ...])`，不放入条件、循环或 helper，不运行时拼白名单。赋值变量是 CLI `--bind` 角色；每个角色必须有自己的允许连接数组。一级组网取全部角色可用组网的交集，本次只选一次。地址、账号、密码和 SSH `upload_type` 来自集中登记，不写进脚本。

业务入参使用顶层字面量 `SCRIPT_ARGUMENTS` 数组，字段为 `name/type/required/default/description`；类型为 str、bool、int、float、dict、list。通过 `paths.arguments[name]` 读取，不直接 input、不改 CLI、不为传参增加装饰器。

## 文件与命令规则

- `get_file(path, filename)` 精确获取本地文件，不用 glob/正则。`create_file(path, filename, str|bytes)` 原样创建，保留完整 shell 文本与换行，不擅自加 `set -e`。
- 本地搜索使用 `fuzz_search(path, pattern, recursive=False)`（关键词子串/glob）、`accurate_search(...)`（精确名/首个 glob）、`get_handler(path, recursive=False)`；结果按文件名字典序，handler 惰性缓存。`find_subdir(path, "diag/slot62")` 必须唯一。`handler.unzip(dest)` 安全自动识别 ZIP/TAR/GZ/RAR，只展开当前一层，每包独立空目录；RAR 数据需要本机 bsdtar（Windows 系统 tar.exe），后端限制见产品日志 README。`move_to(dest)` 是复制并返回新 handler，`rename(basename)` 真实重命名并返回新 handler；冲突报错。产品日志的完整嵌套准备由独立 workflow 编排。
- 特殊本地产品日志功能独立在 `product_log/`，通过 `python -m product_log` 或同一 Web 服务 `/product-logs/` 使用；规则见 `product_log/README.md`，不需要新增设备声明或复制解析器。
- `hdfs_download(local_path, "配置名")` 独立读取项目 `config.json`，配置名必须字面量；不依赖 ctx，不把 HDFS 路径传成 local_path。远端 HDFS 选择由 CLI `--package 配置名=路径` 指定。增加包配置仅在任务需要时做。
- 三种文件来源返回同一种本地 `FileHandle`，可直接 `ssh.upload(file, remote_dir)` 或 `ftp.upload(file, remote_dir)`。上传返回 None，失败抛分类异常。`ssh.download(remote_dir, filename)`、`ftp.download(...)` 返回本地 handler；不创建远端 handler。
- SSH 上传方式只在该环境的 `upload_type: scp|sftp` 配置一次。不得为了方便改动已经验证的 Paramiko、SCP、SFTP、FTP 传输流程。
- `run(command) -> str` 用于直线流程，失败或退出状态未知抛异常；`execute(command, timeout=None, expect_disconnect=False) -> CommandResult` 用于分支，按 status/exit_code/output 判断。
- SSH 退出码 0 为成功，非零为已知命令失败；Telnet POSIX 用独立返回码标记，prompt 模式为 RESULT_UNKNOWN。`success=False` 也可能是超时、断连或未知，不等价于远端明确非零。
- 只有明确的 SUCCESS 才默认继续。RESULT_UNKNOWN 仅在用户提供设备业务关键词判据时按输出决定；保留协议未知记录，不伪造 exit_code=0。业务失败用 `result.with_failure("明确原因")`；返回失败结果结束脚本。
- SSH 每条命令是独立通道，持久 cd/export 需要放同一命令；不要改变用户原命令、管道和重启语义。
- Telnet 不传文件；FTP 不执行命令。TCP 串口网关不等于本地 COM 驱动。
- Telnet 登录凭据集中登记，按设备真实 login_prompt/password_prompt 设置；已显示 shell 提示符时不重复发送凭据，用户名后直接进入 shell 也接受，只有实际出现密码提示才发送密码；Last login 横幅不触发认证。不要将登录写成普通业务 execute，也不猜未知提示。
- Telnet 一般用 shell_mode=auto 探测能力；posix 强制返回码，prompt_only 不发 POSIX 探测。非 POSIX 的 probe_command/probe_keyword 可同时留空，仅确认有效提示符，或成对配置设备明确允许的业务探针；不猜 status/help 等命令。self_check_result 的 check_scope=command/keyword/prompt 区分退出码、业务响应与提示符检查，exit_status_available 仅 POSIX 为 true。prompt_only 的 available 只表示就绪，execute 仍 RESULT_UNKNOWN，不能伪造 exit_code=0；UDK_V100_Firmware 仍须已知退出 0 后再检查 success。
- 串口被动监听用 `read()`、`wait_reboot(keyword)`，后者不发送 reboot；关键词动作 `telnet_wait_exec(keyword, b"\x02")` 精确发送 Ctrl+B 并保留连接，bytes 不自动补回车，str 是普通命令。
- 在其他对象发 reboot 前先 `console.mark_output()`，防止历史关键词触发。网口 `retry(interval, max_try_times)` 只重连和自检，不能重放升级/重启/上传。
- `print()`、结果与远端输出进入完整 run.log；每步结果另写简洁 output.log。CLI run/rerun 完成只输出绝对 log 路径，通过退出码 0/1/2 判断成功/运行失败/准备失败。

## 从已有脚本或测试流程转换

保留用户步骤顺序，先抽取必须的连接角色、包配置、本地文件、固定命令与成功标准，再填入模板。已有 shell 可以整段生成 `.sh` 后上传执行；通过本地 handler 的 filename 和明确远端目录构造路径，按 shell 规则引用，避免全局隐式上传映射。没有明确失败恢复步骤时直接失败停止；用户确认的失败分支可使用 execute，不重复执行有副作用的命令。

只复用 `autoenv` 当前公共接口，不直接连接 Paramiko/socket/FTP 或手写日志。历史 RunContext/日志采集组件仍供已有高级工具使用，但不能成为新业务脚本的默认模板。需要新 Web 操作时优先普通脚本：页面根据静态描述生成输入，经真实 CLI 启动；现有专用 Tool 才使用 autoenv-web-tool skill。

## 验证与交付

```powershell
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py scripts/<name>.py
python -X utf8 -m compileall autoenv scripts
python -X utf8 -m autoenv.cli describe <name> --json
python -X utf8 -m pytest tests/test_generated_script_contract.py
```

describe/list 是静态检查。需要本地流程验收时按 `docs/LOCAL_SIMULATION.md` 使用 loopback 模拟器与生产客户端，文件传输必须真实传字节，不打桩。真实设备执行必须有用户授权及明确目标。交付脚本位置、运行命令、已验证结果和实际未验证的 HDFS/设备路径；不声称未运行流程已成功。

需要支持 `python scripts/name.py --non-interactive ...` 时，文件末尾使用同一 CLI 宿主，保证输出捕获与释放：

```python
if __name__ == "__main__":
    import sys
    from autoenv.cli import main as cli_main
    raise SystemExit(cli_main(["run", __file__, *sys.argv[1:]]))
```

不要在 guard 直接 main() 后遗漏宿主。仅当用户明确需要 import 后独立调用时，用 with init_script()/try-finally 管理 paths.close；普通网页/CLI由宿主统一关闭。
