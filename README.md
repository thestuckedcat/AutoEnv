# AutoEnv

AutoEnv 是一个面向 Windows 的顺序式远端环境启动工具。环境脚本使用普通 Python 代码组织执行顺序，通用层提供 WebHDFS 下载、显式文件/目录提取、SCP/SFTP 单文件上传、SSH/Telnet 命令执行、统一结果、自动日志和上次参数复用。

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

## 运行

显示脚本菜单：

```powershell
python main.py
```

普通运行会加载上次参数作为默认值，并允许逐项修改：

```powershell
python main.py run example_host_environment
```

无交互复用该脚本的上次参数：

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
from autoenv import SSHDefaults, package, register_script


@register_script(name="start_demo", description="启动演示环境")
def start_demo(ctx):
    host = ctx.register_ssh_host(
        "server",
        defaults=SSHDefaults(
            host="192.168.1.100",
            username="root",
            password="root",
        ),
    )

    result = ctx.download_package(package("A1"))
    if not result.success:
        return result

    result = host.scp_upload(
        local_file=package("A1"),
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    return host.execute("bash /root/autoEnv/install.sh", timeout=600)
```

完整可复制示例见 `scripts/example.py`。

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

## 安全说明

当前版本面向可信实验室内网：WebHDFS 默认不校验 TLS 证书，SSH 自动接受未知主机密钥，Telnet 使用明文传输，`.run` 提取会执行包内程序。只应连接可信目标并使用可信构建产物；详细边界见重构设计文档。

## 文档

- [重构详细设计](docs/AutoEnv-Refactor-Detailed-Design.md)
- [环境注册指南](docs/ENVIRONMENT_REGISTRATION_GUIDE.md)

详细设计是接口语义和验收基线；环境注册指南包含全部可选参数、结果状态、完整示例和提交前检查清单。

## 使用 Agent 交互生成环境脚本

仓库内置了 [`autoenv-script-generator`](.agents/skills/autoenv-script-generator/SKILL.md) 项目级 skill。Codex 和 OpenCode 都能从仓库的 `.agents/skills/` 自动发现它，不需要分别安装或维护副本。

从仓库目录启动 agent 后，可以直接提出“使用 autoenv-script-generator 生成一个环境脚本”。skill 会逐项确认连接对象、包来源、提取/上传、命令顺序、超时、成功条件和组合关系；得到最终确认后才写入 `scripts/`，随后进行编译、注册发现和单元测试检查。

根目录 `AGENTS.md` 同时提供加载入口，便于其他支持项目指令但不能自动发现 skills 的 agent 读取并遵循同一流程。
