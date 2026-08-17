# AutoEnv

本分支提供本地开发者 Web 控制台：唯一启动命令是 `python -X utf8 startWeb.py`，固定使用 `http://127.0.0.1:8765/`，不接受 host/port 参数，也不会在冲突时回退其他端口。控制台可注册环境、非交互拉起脚本、使用动态 Tools，以及启动支持文件路径转换的 Agent CLI。环境页和后端校验使用同一份 `autoenv/resource_labels.json` 资源标签目录。快速使用见 `webPage/QUICK_START.md`，后续开发接手见 `docs/WEB_ARCHITECTURE_AND_HANDOFF.md`。

AutoEnv 是一个面向 Windows 的顺序式远端环境启动工具。环境脚本使用普通 Python 代码组织执行顺序，通用层提供 WebHDFS 包下载、SCP/SFTP 远端文件下载、显式文件/目录及 ZIP 提取、SCP/SFTP/FTP 单文件上传、SSH/Telnet 命令执行、按输出关键词发送原始字节、结构化非交互启动、启动后固定 func 菜单、统一结果、自动日志和上次参数复用。Web Tools 另有独立注册表，既支持原有本地 JSON 小工具，也支持绑定环境资源、在独立子进程中运行的 `RunContext` workflow。

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

需要查看 HDFS、选择器、提取、SSH/SCP/SFTP、FTP、Telnet、结果判断、后置
`register_func`、组合脚本以及 Web 元数据的完整写法时，参考
[`scripts/template.py`](scripts/template.py)；下面保留最小示例。

```python
from autoenv import SSHDefaults, package, register_func, register_script


@register_script(
    name="start_demo",
    description="启动演示环境",
)
def start_demo(ctx):
    # 文件选择器和连接对象集中声明，后续流程只复用这些变量。
    demo_package = package(
        "A1",
        alias="A1 主安装包",
        description="从 HDFS 下载并上传到演示服务器的安装包。",
    )
    host = ctx.register_ssh_host(
        "server",
        resource_label="1260网口",
        alias="演示服务器管理网口",
        description="用于上传安装包并执行启动命令。",
        defaults=SSHDefaults(
            host="192.0.2.10",
            username="root",
            password="",
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

SCP/SFTP 下载使用已声明的 SSH Host，可指定精确远端文件，或在一个远端目录中用正则进行唯一匹配。下载成功的 `RemoteDownloadResult` 会注册到本次 `packages/`，可直接传给 SCP、SFTP 或 FTP 上传；远端模糊匹配没有 HDFS `newest` 语义，零匹配和多匹配都会失败。完整示例见[环境注册指南](docs/ENVIRONMENT_REGISTRATION_GUIDE.md#22-scp-sftp-下载与结果复用)。

日志 workflow 使用 `SSHHost.scp_download_many()` 按 glob 下载远端目录中的全部匹配文件；内置页面支持每行填写一个远端目录，并将各目录隔离到 `source-NNN` 后再统一分析。远端枚举只使用 BusyBox 可提供的 ash glob、`test`、`wc`、`stat` 和 `printf`，不依赖 GNU `find -printf`。`ctx.create_log_collection()` 随后完成递归安全解压、稳定分组、line/block 解析、带时间前缀目标日志、SQLite 索引和批次 manifest。内置 `webPage/tools/log_collection.py` 固定处理已确认的 `cpdt_*` 样例规则；它只出现在 Tools 页签，不进入环境启动脚本列表。

新增 local Web Tool 时，复制 [`webPage/tools/_template.py`](webPage/tools/_template.py) 为非下划线 `.py` 文件并替换全部占位符；workflow Tool 使用 `.agents/skills/autoenv-web-tool/scripts/scaffold_tool.py --kind workflow` 生成。下划线模板不会被自动发现，正式文件则会自动注册到 Tools 页，无需修改 Web 核心页面。完整流程见 [Web 快速入门](webPage/QUICK_START.md#添加-tool)。

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

终端面向人工阅读，会用带空行的摘要块突出操作状态、源/目标、命令结果和错误；`run.log` 仍保留单行 JSON 操作记录，方便搜索和自动分析。SSH/Telnet 长短命令都在接收时实时显示远端输出，并将清理后的完整正文保存在 `result.output`；`execute()` 结束摘要不重复打印正文。

## 安全说明

当前版本面向可信实验室内网：WebHDFS 默认不校验 TLS 证书，SSH 自动接受未知主机密钥，Telnet 使用明文传输，`.run` 提取会执行包内程序。只应连接可信目标并使用可信构建产物；详细边界见重构设计文档。

## 文档

- [快速入门](docs/QUICK_START.md)
- [重构详细设计](docs/AutoEnv-Refactor-Detailed-Design.md)
- [环境注册指南](docs/ENVIRONMENT_REGISTRATION_GUIDE.md)
- [UT 目标与排查指南](tests/README.md)
- [Web 快速入门](webPage/QUICK_START.md)
- [Web 与 adapt interface 使用说明](docs/web_usage.md)
- [Web 架构与接手说明](docs/WEB_ARCHITECTURE_AND_HANDOFF.md)
- [文档一致性审计](docs/DOCUMENTATION_AUDIT.md)
- [通用底层软件 SDD 技能包](sdd/README.md)

第一次使用先看快速入门；详细设计是接口语义和验收基线；环境注册指南包含全部可选参数、结果状态、完整示例和提交前检查清单。

## 使用 Agent 交互生成环境脚本

仓库内置了 [`autoenv-script-generator`](.agents/skills/autoenv-script-generator/SKILL.md) 项目级 skill。Codex 和 OpenCode 都能从仓库的 `.agents/skills/` 自动发现它，不需要分别安装或维护副本。

从仓库目录启动 agent 后，可以直接提出“使用 autoenv-script-generator 生成一个环境脚本”，也可以粘贴一整段已有 shell 脚本让它转换。skill 会逐项确认连接对象、包来源、脚本中的文件映射、提取/上传、命令顺序、超时、成功条件和组合关系；得到最终确认后才写入 `scripts/`，随后进行统一脚本契约、编译、注册发现和单元测试检查。

## 使用 Agent 定位日志错误

把日志文件或目录交给 Agent，并要求使用 `log-error-triage`，即可提取 `[ERROR]` 及上下文、按时间和关联 ID 聚类、在本地源码中追踪错误发出点，并区分触发组件、实际失败组件与报告/受害组件。Skill 默认只读分析；只有人工明确确认后，才会把可复用结论写入 [`logKnowledge/`](logKnowledge/README.md)。Skill 定义和辅助脚本位于 [`.agents/skills/log-error-triage/`](.agents/skills/log-error-triage/SKILL.md)。

根目录 `AGENTS.md` 同时提供加载入口，便于其他支持项目指令但不能自动发现 skills 的 agent 读取并遵循同一流程。

## 命令中的上传文件占位符

SSH、Telnet 命令和 `generate_sh_file()` 支持 `S{file_name}`。`file_name` 是
`package()`、`extra_file()` 或 `match()` 的字符串参数，并且对应文件必须已经在本次运行中通过
`scp_upload()` 或 `sftp_upload()` 成功上传。发送或写入命令前，占位符会替换成实际解析出的包文件名：

```python
a1_package = package("A1")
install_script = extra_file("install.sh")
host = ctx.register_ssh_host(
    "server", resource_label="1260网口", defaults=SSHDefaults(...)
)

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
