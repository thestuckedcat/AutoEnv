# AutoEnv 快速入门

这份文档用于在几分钟内完成三件事：运行 AutoEnv、编写一个最小环境脚本、在不连接真实设备的情况下快速验证修改。完整接口和设计语义请使用文末导航。

## 1. 安装

要求 Python 3.11 或更高版本。在仓库根目录执行：

```powershell
python -m pip install -e ".[test]"
```

安全地检查脚本是否能被发现，不执行 HDFS、SSH 或 Telnet 操作：

```powershell
python -X utf8 -c "from autoenv.registry import list_scripts; print([item.name for item in list_scripts()])"
```

## 2. 快速运行已有环境

> 本节命令会在选定环境后执行真实流程。先确认目标地址、账号和包来源；如果只想检查脚本能否加载，请使用上一节的安全发现命令。

显示环境列表：

```powershell
python main.py
```

运行指定环境：

```powershell
python main.py run example_host_environment
```

复用上次确认的参数：

```powershell
python main.py rerun example_host_environment
```

`run` 会逐项确认参数；`rerun` 不重新确认参数。如果环境注册了启动后 func，两种模式都会在主流程成功后显示 func 菜单。以上命令可能下载包、连接设备、上传文件并执行远端命令，只能在确认目标地址和包来源后运行。

## 3. 快速创建一个环境脚本

在 `scripts/` 下创建 `start_demo.py`。下面示例展示推荐顺序：集中声明、下载、上传、执行、注册启动后检查。

```python
from autoenv import SSHDefaults, package, register_func, register_script


@register_script(name="start_demo", description="启动演示环境")
def start_demo(ctx):
    # 1. 文件选择器和连接对象集中声明。
    demo_package = package("A1")
    host = ctx.register_ssh_host(
        "demo_host",
        defaults=SSHDefaults(
            host="192.168.1.100",
            port=22,
            username="root",
            password="",
            connect_timeout=30,
        ),
    )

    # 2. 下载、上传和执行严格按代码顺序发生。
    result = ctx.download_package(demo_package)
    if not result.success:
        return result

    result = host.sftp_upload(
        local_file=demo_package,
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    # S{A1} 会替换为本次成功上传的实际包名。
    result = host.execute(
        "chmod +x /root/autoEnv/S{A1} && /root/autoEnv/S{A1}",
        timeout=600,
    )
    if not result.success:
        return result

    # 3. 可选：主流程成功后提供循环选择的固定检查。
    @register_func(name="check_status", description="检查环境 READY 状态")
    def check_status(_func_ctx):
        status = host.execute("cat /tmp/env_status", timeout=30)
        if status.success and "READY" not in status.output:
            return status.with_failure("environment status is not READY")
        return status

    return result
```

使用 `package("A1")` 前，确认 [`config.json`](../config.json) 中存在同名配置，且 `image_name` 能唯一匹配目标包。上传和提取不会自动下载；每一步都必须显式调用并立即检查结果。

也可以直接让仓库 skill 引导生成：向 agent 提出“使用 `autoenv-script-generator` 生成环境脚本”，或粘贴一整段已有 shell 脚本。agent 会先确认包来源、连接、操作顺序和成功条件，再写入 `scripts/`。

## 4. 快速复制已有 shell 脚本

如果要用已有 shell 脚本替换上一节的直接执行步骤，应把它作为一整段文本传入，不拆成 Python 命令列表。下面片段假定 `demo_package`、`install_script` 和 `host` 已在函数开头声明：

```python
# demo_package = package("A1")
# install_script = extra_file("install.sh")
# host = ctx.register_ssh_host(...)

result = host.sftp_upload(demo_package, "/root/autoEnv")
if not result.success:
    return result

# A1 已成功上传；生成时只替换实际文件名。
ctx.generate_sh_file(
    "install.sh",
    """#!/bin/sh
set -eu
cd /root/autoEnv
tar -xf "S{A1}"
./install
""",
)

result = host.sftp_upload(install_script, "/root/autoEnv")
if not result.success:
    return result

return host.execute("bash /root/autoEnv/S{install.sh}", timeout=600)
```

关键规则：

- `S{file_name}` 使用 `package()`、`extra_file()` 或 `match()` 中的字符串，不是 Python 变量名。
- 对应文件必须在本次运行中成功上传；SSH 命令只能使用上传到当前 Host 的文件。
- 一个生成脚本中的所有占位符必须已经上传到同一个 Host。
- `generate_sh_file()` 只替换包名，保留原脚本的 shebang、换行、引号和布局。

## 5. 快速测试

日常修改按从快到慢的顺序执行：

```powershell
# 1. 检查所有环境脚本的集中声明、操作顺序、S{} 和 register_func 契约
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py

# 2. Python 语法和导入编译
python -X utf8 -m compileall -q autoenv scripts

# 3. 统一环境脚本契约 UT
python -X utf8 -m pytest tests/test_generated_script_contract.py -q

# 4. 修改 register_func/运行时后执行聚焦 UT
python -X utf8 -m pytest tests/test_runtime_registry.py -q

# 5. 全量离线 UT
python -X utf8 -m pytest -q
```

这些测试使用 fake HDFS、SSH、SFTP/SCP 和 Telnet Socket，不会连接真实服务器。全量 UT 通过只说明本地框架契约成立，不代表真实环境已经拉起。

测试失败时复制 pytest 输出的完整 node id 单独重跑：

```powershell
python -X utf8 -m pytest tests/test_runtime_registry.py::test_registered_funcs_reuse_context_and_loop_until_exit -vv
```

每条重点 UT 的实现、存在目的和排查入口见 [UT 目标与排查指南](../tests/README.md)。

## 6. 运行结果在哪里

每次运行都会创建独立目录：

```text
logs/<run_id>/
├── run.log       # 完整流程、远端输出、func 执行记录
├── params.json   # 本次确认的连接和包参数
├── result.json   # 主流程结果和 func_runs
└── packages/     # 下载、提取和生成的本地文件
```

排查顺序通常是：终端最后一条错误 → `result.json` 的 `status/error_type/error_message` → `run.log` 中对应操作 → `params.json` 中实际使用的地址和路径。

## 7. 常见问题快速定位

| 现象 | 先检查 |
|---|---|
| `unknown AutoEnv script` | 文件是否位于 `scripts/`、是否使用 `@register_script`、导入是否报错 |
| 包不存在或多匹配 | `config.json.image_name`、本次 `packages` 内容、选择器字符串 |
| `S{...}` 无法替换 | 是否成功上传、上传 Host 是否与执行 Host 一致、Telnet 是否配置 `uploaded_files_from` |
| `register_func` 注册失败 | 是否位于主流程内部末尾、名称是否重复、是否只有一个 ctx 参数 |
| func 不显示 | 主流程是否成功、是否在注册定义前提前 `return` |
| `TIMEOUT`/`DISCONNECTED` | 先看 `phase` 和部分输出；命令可能已执行，不要盲目重发 |

## 8. 详细文档导航

- [环境注册指南](ENVIRONMENT_REGISTRATION_GUIDE.md)：全部公共接口、参数、状态、完整示例和提交检查清单。
- [重构详细设计](AutoEnv-Refactor-Detailed-Design.md)：运行模型、生命周期、结果语义、安全边界和验收标准。
- [UT 目标与排查指南](../tests/README.md)：测试 fake 实现、每条重点 UT 的目标和失败排查。
- [完整可复制示例](../scripts/example.py)：SSH、Telnet、上传、shell 生成、`register_func` 和组合环境。
- [`autoenv-script-generator` skill](../.agents/skills/autoenv-script-generator/SKILL.md)：让 agent 交互澄清并生成环境脚本的流程。
- [Agent 仓库规则](../AGENTS.md)：修改后的关联文件审视和验证要求。
- [项目 README](../README.md)：安装、入口、安全说明和功能概览。
