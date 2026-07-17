# AutoEnv

AutoEnv 是一个面向 Windows 的顺序式远端环境启动工具。环境脚本使用普通 Python 代码组织执行顺序，通用层提供 WebHDFS 下载、显式文件/目录提取、SCP/SFTP 单文件上传、SSH/Telnet 命令执行、按输出关键词发送原始字节、启动后固定 func 菜单、统一结果、自动日志和上次参数复用。

AutoEnv 不包含工作流 DAG、Step 依赖或并发调度。脚本中的代码顺序就是实际执行顺序。

## 安装

要求 Python 3.11 或更高版本。

```powershell
python -m pip install -e .
```

开发和测试：

```powershell
python -m pip install -e ".[test]"
python -m pytest
```

每类 UT 的简单实现、测试目标和失败排查入口见 [`tests/README.md`](tests/README.md)。

## 运行

显示脚本菜单：

```powershell
python main.py
```

普通运行会加载上次参数作为默认值，并允许逐项修改：

```powershell
python main.py run example_host_environment
```

确认 package 远端目录时，直接回车沿用显示的上次值；只要对应 `config.json` 项定义了 `base_link`，提示中就始终提供 `!newest`，输入它会忽略上次手工路径并立即按 `base_link` 重新解析 automatic newest 包。

无参数确认地复用该脚本的上次参数；如果脚本注册了启动后 func，主流程成功后仍会显示 func 菜单：

```powershell
python main.py rerun example_host_environment
```

安装后也可以使用等价入口：

```powershell
autoenv
autoenv run example_host_environment
autoenv rerun example_host_environment
```

## 注册一个环境

在 `scripts/` 下添加 Python 文件：

```python
from autoenv import SSHDefaults, package, register_func, register_script


@register_script(name="start_demo", description="启动演示环境")
def start_demo(ctx):
    # 文件选择器和连接对象集中声明，后续流程只复用这些变量。
    demo_package = package("A1")
    host = ctx.register_ssh_host(
        "server",
        defaults=SSHDefaults(
            host="192.168.1.100",
            username="root",
            password="root",
        ),
    )

    result = ctx.download_package(demo_package)
    if not result.success:
        return result

    result = host.scp_upload(
        local_file=demo_package,
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    result = host.execute("bash /root/autoEnv/install.sh", timeout=600)
    if not result.success:
        return result

    @register_func(name="check_status", description="检查环境状态")
    def check_status(_func_ctx):
        return host.execute("cat /tmp/env_status", timeout=30)

    return result
```

完整可复制示例见 `scripts/example.py`。

`register_func()` 必须在正在运行的 `@register_script` 函数内部、主流程末尾使用。主流程成功后，AutoEnv 循环显示所有已注册 func 和 `0. exit`；每次执行后回到菜单。func 接收同一个 `RunContext`，可使用与主流程相同的 API，也可通过闭包复用已经注册的 Host、Telnet 和选择器。退出菜单后本次上下文和连接才关闭。

func 返回失败结果或抛异常时会记录到 `run.log` 和 `result.json.func_runs`，菜单继续可用；它不会把已经成功的环境主流程改判失败。主流程失败时不显示 func 菜单。

## 文件来源

所有上传和提取源文件都来自本次运行自动创建的 `logs/<run>/packages/`：

- `package("A1")`：根据 `config.json` 中 `name=A1` 的 `image_name` 匹配本地文件。
- `extra_file("firmware.bin")`：使用包目录中的明确文件名。
- `match(r"^firmware-.*\.bin$")`：正则匹配并按稳定顺序选择第一个文件。

上传和提取不会隐式下载。需要从 HDFS 下载时必须显式调用 `ctx.download_package(package("A1"))`。

## 运行记录

每个注册脚本调用都有独立目录：

```text
logs/<run_id>/
├── run.log
├── params.json
├── result.json
└── packages/
```

`state/last_runs/<script>.json` 保存该脚本上一次完整参数。密码按项目需求允许明文存档，但终端和 `run.log` 会脱敏。请保护本机的 `state/` 和 `logs/` 访问权限。

终端面向人工阅读，会用带空行的摘要块突出操作状态、源/目标、命令结果和错误；`run.log` 仍保留单行 JSON 操作记录，方便搜索和自动分析。SSH/Telnet 在命令运行期间持续读取但不逐段打印，`execute()` 结束后统一在摘要的 `output` 区块显示完整输出；该内容与 `result.output` 相同，不会重复打印。

## 安全说明

当前版本面向可信实验室内网：WebHDFS 默认不校验 TLS 证书，SSH 自动接受未知主机密钥，Telnet 使用明文传输，`.run` 提取会执行包内程序。只应连接可信目标并使用可信构建产物；详细边界见重构设计文档。

## 文档

- [快速入门](docs/QUICK_START.md)
- [重构详细设计](docs/AutoEnv-Refactor-Detailed-Design.md)
- [环境注册指南](docs/ENVIRONMENT_REGISTRATION_GUIDE.md)
- [UT 目标与排查指南](tests/README.md)

第一次使用先看快速入门；详细设计是接口语义和验收基线；环境注册指南包含全部可选参数、结果状态、完整示例和提交前检查清单。

## 使用 Agent 交互生成环境脚本

仓库内置了 [`autoenv-script-generator`](.agents/skills/autoenv-script-generator/SKILL.md) 项目级 skill。Codex 和 OpenCode 都能从仓库的 `.agents/skills/` 自动发现它，不需要分别安装或维护副本。

从仓库目录启动 agent 后，可以直接提出“使用 autoenv-script-generator 生成一个环境脚本”，也可以粘贴一整段已有 shell 脚本让它转换。skill 会逐项确认连接对象、包来源、脚本中的文件映射、提取/上传、命令顺序、超时、成功条件和组合关系；得到最终确认后才写入 `scripts/`，随后进行统一脚本契约、编译、注册发现和单元测试检查。

根目录 `AGENTS.md` 同时提供加载入口，便于其他支持项目指令但不能自动发现 skills 的 agent 读取并遵循同一流程。

## 命令中的上传文件占位符

SSH、Telnet 命令和 `generate_sh_file()` 支持 `S{file_name}`。`file_name` 是
`package()`、`extra_file()` 或 `match()` 的字符串参数，并且对应文件必须已经在本次运行中通过
`scp_upload()` 或 `sftp_upload()` 成功上传。发送或写入命令前，占位符会替换成实际解析出的包文件名：

```python
a1_package = package("A1")
install_script = extra_file("install.sh")
host = ctx.register_ssh_host("server", defaults=SSHDefaults(...))

result = host.sftp_upload(a1_package, "/root/autoEnv")
if not result.success:
    return result

# 例如实际上传的是 A1-20260717.tgz，生成文件中会写入该真实文件名。
ctx.generate_sh_file(
    "install.sh",
    """#!/bin/sh
set -e
cd /root/autoEnv
tar -xf S{A1}
./install
""",
)

result = host.sftp_upload(install_script, "/root/autoEnv")
if not result.success:
    return result
return host.execute("sh /root/autoEnv/S{install.sh}")
```

未上传、上传失败或无法唯一对应实际文件名的占位符会在命令发送前报错。

上传记录按 SSH Host 隔离。SSH 命令只能使用已上传到该 Host 的文件；多个占位符生成同一个 shell 文件时，它们必须曾成功上传到同一个 Host。Telnet 若要引用经 SSH 上传的文件，注册时使用 `uploaded_files_from="ssh_host_name"` 明确来源；未指定时只允许唯一可推断的来源。

`generate_sh_file()` 的第二个参数是一整段 shell 文本，第一个参数必须是当前 `packages` 根目录下的 `.sh` 文件名。函数不会把脚本拆成命令列表，也不会自动添加 shebang 或错误处理选项；除替换 `S{...}` 外，输入文本会原样写入。

所有 `scripts/*.py` 可使用同一个离线契约检查：

```bash
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py
python -X utf8 -m pytest tests/test_generated_script_contract.py
```

## 按输出关键词发送数据

SSH Host 和 Telnet 对象均提供阻塞式 `execute_on_output()`。它先执行命令，再持续读取输出；关键词出现后立即向同一通道发送原始字节并返回：

```python
result = console.execute_on_output(
    "reboot",
    keyword="Press Ctrl+B",
    send_data=b"\x02",
    timeout=90,
)
```

`b"\x02"` 是 Ctrl+B 的实际字节，不是文本 `b"ctrl+b"`。`send_data` 不会自动追加回车或换行；普通命令需显式写成例如 `b"boot\r\n"`。完整状态语义、串口连接重置规则和常用控制字符对照见[环境注册指南](docs/ENVIRONMENT_REGISTRATION_GUIDE.md#121-按输出关键词发送数据)。普通 SSH 在 reboot 后通常断开，无法观察 BIOS、BootROM 或 Bootloader 输出；进入启动模式的场景通常应使用串口映射的 Telnet 连接。
