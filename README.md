# AutoEnv

AutoEnv 是一个用于**按环境定义自动拉取驱动/测试包、渲染安装脚本、上传目标机并可选执行远端命令**的工具。当前代码采用“镜像规则在 `config.json`、环境定义在 `ENV/<env_name>.py`、运行编排在 `env_executor.py`”的结构。

## 快速开始

1. 在 `config.json` 配置镜像规则（`name`/`link`/`base_link`/`image_name`/`target_file`）。
2. 在 `ENV/<env_name>.py` 注册环境的 `EnvironmentSpec`。
3. 运行：

```bash
python3 main.py
```

程序会列出所有可用环境。选择环境后会自动：

- 根据环境 `image_vars` 引用的 `name` 查找 `config.json` 中的包规则；
- 允许交互式覆盖下载目录；否则按 `link > base_link(newest 自动解析)` 选择远端目录；
- 用 `image_name` 正则匹配真实包名并下载到 `runtime/<run_id>/`；
- 按需解包并提取 `target_file`；
- 将脚本模板中的 `${变量名}` 替换为真实包名或提取文件名；
- 生成 `.sh` 脚本并上传脚本 + 包到目标机器；
- 每次执行都会写入 `logs/autoenv_<run_id>.log` 与对应 `runtime/<run_id>/`；
- `runtime/` 总容量超过 1GB 时会删除最早历史执行目录（保留本次目录）。

## 目录结构

- `main.py`：单环境交互入口。
- `composite_runner.py`：按数组顺序组合执行多个已注册环境。
- `models.py`：核心数据结构，包括 `EnvironmentSpec`、`ImageSpec`、`DownloadedImage`。
- `config_loader.py`：加载并校验 `config.json`。
- `env_config.py`：注册表、`ENV/*.py` 自动导入、全局兜底默认值与默认值合并接口。
- `ENV/`：每个环境的独立注册文件。
- `env_common.py`：环境文件常用公共导入。
- `env_executor.py`：执行主流程与 `EnvironmentProcessContext` 扩展接口。
- `renderer.py`：脚本变量替换与未替换变量检查。
- `tools.py`：WebHDFS 拉包、SCP/FTP 上传、SSH 命令执行。
- `telnet.py`：Telnet 串口逐条命令发送。
- `unextract.py`：`.run` / `.tar.gz` 解包与 `target_file` 提取。
- `logger.py`：统一日志初始化。
- `debug/`：调试入口。
- `docs/ENVIRONMENT_SETUP.md`：从零配置环境、底层组件接口与运行时调用链说明。

## 依赖

```bash
pip install requests urllib3 paramiko scp
```

## 环境定义与连接默认值

环境定义已经从 `env_config.py` 拆分到 `ENV/` 目录。`env_config.py` 只保留全局兜底默认值和合并逻辑，不再维护 `ENV_SSH_DEFAULTS`、`ENV_TELNET_DEFAULTS` 或 `ENV_FTP_DEFAULTS` 这类按环境的外置映射。

按环境覆盖值应直接写进对应 `EnvironmentSpec`：

```python
from __future__ import annotations

from env_common import ENV_REGISTER, EnvironmentSpec, default_environment_process


@ENV_REGISTER("UDK_ENV_RUN")
def build_env(env_name: str) -> EnvironmentSpec:
    return EnvironmentSpec(
        env_name=env_name,
        image_vars={
            "hn922_drv": "UnionS_SDK_Drv",
            "mami_drv": "Ubengine_Drv",
            "mami_testcase": "Ubengine_TestCase",
        },
        script_templates={
            "main": """#!/bin/bash
set -e
cd /root/autoEnv

echo "install ${hn922_drv} ${mami_drv} ${mami_testcase}"
"""
        },
        process=default_environment_process,
        upload_protocol="scp",
        ssh_defaults={"username": "root", "password": "root", "host": "141.131.72.195", "port": 22},
        telnet_defaults={"host": "141.131.72.195", "port": 23, "timeout": 30.0},
        ftp_defaults={"username": "root", "password": "root", "port": 21, "remote_path": "/root/autoEnv"},
    )
```

默认值合并规则：

- `env_config.SSH_DEFAULTS`、`TELNET_DEFAULTS`、`FTP_DEFAULTS` 是全局兜底；
- `EnvironmentSpec.ssh_defaults`、`telnet_defaults`、`ftp_defaults` 是环境级覆盖；
- `get_ssh_defaults(env_name)`、`get_telnet_defaults(env_name)`、`get_ftp_defaults(env_name)` 会先复制全局兜底，再覆盖环境级字段；
- 底层 `upload_files_via_scp` / `upload_files_via_ftp` / `run_ssh_commands` 不读取任何全局默认值，调用方必须显式传入连接参数，避免凭据来源歧义。

## config.json 包规则

`config.json` 顶层必须是数组。每项常用字段：

- `name`：必填且唯一，供 `EnvironmentSpec.image_vars` 引用；
- `link`：显式远端目录，优先级最高；
- `base_link`：当 `link` 为空时，从该路径下自动找最新日期目录中的 `newest`；
- `image_name`：必填，匹配真实包名的正则；
- `target_file`：可选字符串或字符串数组，表示下载后要从包中提取的文件/目录。

路径优先级：手工输入路径 > `link` > `base_link(newest 自动解析)`。当 `link` 和 `base_link` 都为空时，程序会要求手工输入路径。

## image_vars 与脚本模板

`EnvironmentSpec.image_vars` 支持三种写法：

```python
image_vars={
    # 脚本变量 -> config.json 中的 name；脚本里 ${hn922_pkg} 渲染为下载包名。
    "hn922_pkg": "UnionS_SDK_Drv",

    # 脚本变量 -> (config.json 中的 name, target_file)。
    # 脚本里 ${hn922_drv} 渲染为提取出的 file1 文件名。
    "hn922_drv": ("UnionS_SDK_Drv", "file1"),

    # 显式字典写法。
    "mami_drv": {"name": "Ubengine_Drv", "target_file": "driver.bin"},
}
```

`script_template` 已兼容保留，但推荐使用 `script_templates` 字典：

```python
script_templates={
    "prepare": "... ${hn922_drv} ...",
    "install": "...",
}
```

如果一个环境只有 `main` 模板，生成脚本名形如 `<ENV>_<timestamp>.sh`；多个模板时会在环境名后追加模板名。

## 可扩展 process

`EnvironmentSpec.process` 是环境专属处理函数指针，默认可以使用 `default_environment_process`。自定义 process 会收到 `EnvironmentProcessContext`，可按环境需要编排下载、解包、渲染、上传与命令执行。

常用接口：

- `context.upload_files_scp(...)` / `context.upload_files_ftp(...)`：上传已准备的包和脚本；
- `context.upload_file_to_telnet_path(...)`：通过 FTP 把单个文件发送到 Telnet 服务器可访问的远端路径；
- `context.send_telnet_commands(...)` / `context.send_ssh_commands(...)`：逐条发送命令并获取输出；
- `context.download_image_var(...)`：在 process 中继续从 WebHDFS 拉取包，并按需提取 `target_file`；
- `context.extract_package_targets(...)`：对已有包继续解压并提取目标文件；
- `context.render_template(...)`：在 process 中额外渲染并注册 `.sh` 脚本。

## Telnet 串口执行与 FTP 发包

`EnvironmentSpec` 中与 Telnet/FTP 有关的字段：

- `telnet_commands`：可配置逐条发送的 Telnet 命令，命令中支持 `${script_name}` 占位符；
- `upload_protocol`：默认发包方式，支持 `scp` 或 `ftp`；
- `telnet_defaults`：Telnet 串口服务器默认 `host`/`port`/`timeout`；
- `ftp_defaults`：FTP 默认 `username`/`password`/`port`/`remote_path`。

`telnet.py` 的 `run_telnet_commands()` 会连接 Telnet 串口，先发送三次回车并识别 shell 提示符，再逐条发送命令；命令输出可写入指定 log。

## 组合环境执行

组合环境用于把多个已注册单环境串起来执行：

```bash
python3 composite_runner.py
```

也可以在代码中调用：

```python
from composite_runner import run_composite_environments

run_composite_environments(["A_ENV_RUN", "B_ENV_RUN"])
```

组合环境注册示例在 `ENV/composite_envs.py`：

```python
register_composite_env("A_B_CHAIN_RUN", ["A_ENV_RUN", "B_ENV_RUN"])
```

组合执行时，每个子环境都会分别询问包路径、上传协议和目标连接信息。

## Debug 调试接口

```bash
# 调试 config.json 中 target_file 的解析，并查看指定环境 SSH/Telnet/FTP 默认值
python3 debug/config_debug.py --config config.json --env A_ENV_RUN

# 调试 .run 解包
python3 debug/unextract_debug.py run path/to/pkg.run runtime/debug_unextract

# 调试 .tar.gz 解包
python3 debug/unextract_debug.py tar-gz path/to/pkg.tar.gz runtime/debug_unextract

# 调试 target_file 提取
python3 debug/unextract_debug.py target path/to/pkg.tar.gz runtime/debug_unextract file1 file2

# 调试 FTP 发包
python3 debug/ftp_debug.py --host 192.168.1.100 --username root path/to/file1 path/to/file2

# 调试 Telnet 串口逐条命令发送
python3 debug/telnet_debug.py --host 192.168.1.100 --command "pwd" --command "ls /root/autoEnv"
```

## 新增环境建议

1. 复制 `ENV/_template_env.py` 为 `ENV/<your_env>.py`。
2. 取消 `@ENV_REGISTER("...")` 注释并改成真实环境名。
3. 在 `image_vars` 中引用 `config.json` 已存在的 `name`。
4. 在 `script_templates` 中使用 `${变量名}` 引用 `image_vars`。
5. 在同一个 `EnvironmentSpec` 内配置 `ssh_defaults` / `telnet_defaults` / `ftp_defaults`。
6. 如需组合环境，在 `ENV/composite_envs.py` 调用 `register_composite_env(...)`。
7. 详细从零示例、底层接口与运行链路见 `docs/ENVIRONMENT_SETUP.md`。
