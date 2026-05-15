# 从零配置一个环境、底层组件接口与运行时调用链

本文档展示如何从空白配置开始新增一个环境，并说明 AutoEnv 各底层组件的接口用途与一次运行的调用链。

## 1. 配置目标

假设要新增环境 `UDK_ENV_RUN`，它需要三个包：

- `UnionS_SDK_Drv`：HN922 驱动包；
- `Ubengine_Drv`：UBEngine 驱动包；
- `Ubengine_TestCase`：UBEngine 测试用例包。

目标机默认连接参数：

```python
{"username": "root", "password": "root", "host": "141.131.72.195", "port": 22}
```

这些默认连接参数应直接写入 `EnvironmentSpec.ssh_defaults`，不要再创建独立的 `ENV_SSH_DEFAULTS` 映射。

## 2. 第一步：配置 config.json 包规则

在 `config.json` 中新增或确认存在如下规则。`name` 必须和环境的 `image_vars` 引用一致。

```json
[
  {
    "name": "UnionS_SDK_Drv",
    "link": "",
    "base_link": "/compilepackage/CI_Version/torino/br_hisi_trunk_ai",
    "image_name": "^HN922-driver-[\\w.-]+-rtos[\\w.-]+\\.aarch64-debug\\.run$",
    "target_file": []
  },
  {
    "name": "Ubengine_Drv",
    "link": "",
    "base_link": "/compilepackage/CI_Version/ubengine/br_hisi_trunk_ai",
    "image_name": "^UBEngine-mgmt-driver-[\\w.-]+-rtos[\\w.-]+\\.aarch64\\.run$"
  },
  {
    "name": "Ubengine_TestCase",
    "link": "",
    "base_link": "/compilepackage/CI_Version/ubengine/br_hisi_trunk_ai",
    "image_name": "^UBEngine-testcase-[\\w.-]+-rtos[\\w.-]+\\.aarch64\\.tar\\.gz$"
  }
]
```

字段说明：

- `name`：环境引用的稳定名称；
- `link`：显式远端目录；为空时使用 `base_link` 自动 newest；
- `base_link`：远端基础目录；程序会找最新日期目录中的 `newest`；
- `image_name`：匹配真实包文件名的正则；
- `target_file`：可选，下载后需要从包内提取的文件/目录。

## 3. 第二步：新增 ENV/udk_env_run.py

新建 `ENV/udk_env_run.py`：

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

echo "HN922 driver: ${hn922_drv}"
echo "MAMI driver: ${mami_drv}"
echo "MAMI testcase: ${mami_testcase}"

chmod +x ${hn922_drv} ${mami_drv}
# TODO: 按真实产品命令替换以下示例
# ./${hn922_drv} --install
# ./${mami_drv} --install
# tar -xzf ${mami_testcase}
"""
        },
        process=default_environment_process,
        upload_protocol="scp",
        ssh_defaults={"username": "root", "password": "root", "host": "141.131.72.195", "port": 22},
        telnet_defaults={"host": "141.131.72.195", "port": 23, "timeout": 30.0},
        ftp_defaults={"username": "root", "password": "root", "port": 21, "remote_path": "/root/autoEnv"},
    )
```

关键点：

- `@ENV_REGISTER("UDK_ENV_RUN")` 会在 `env_config.load_env_modules()` 自动导入模块时完成注册；
- `image_vars` 的值必须能在 `config.json` 中找到；
- `script_templates` 中的 `${hn922_drv}` 等占位符必须与 `image_vars` 的键一致；
- `ssh_defaults` / `telnet_defaults` / `ftp_defaults` 都属于当前 `EnvironmentSpec`，配置跟环境放在同一个文件内。

## 4. 第三步：运行并验证

```bash
python3 debug/config_debug.py --config config.json --env UDK_ENV_RUN
```

该命令会输出：

- `image_specs`：`config.json` 解析结果；
- `transport_defaults.ssh`：全局 SSH 默认值与 `UDK_ENV_RUN.ssh_defaults` 合并后的结果；
- `transport_defaults.telnet`：Telnet 合并默认值；
- `transport_defaults.ftp`：FTP 合并默认值。

正式运行：

```bash
python3 main.py
```

选择 `UDK_ENV_RUN` 后，按提示确认包路径、上传协议和目标连接信息即可。

## 5. 底层组件接口用法

### 5.1 环境注册与默认值

```python
from env_config import get_env, get_ssh_defaults, get_telnet_defaults, get_ftp_defaults

env = get_env("UDK_ENV_RUN")
ssh_defaults = get_ssh_defaults("UDK_ENV_RUN")
telnet_defaults = get_telnet_defaults("UDK_ENV_RUN")
ftp_defaults = get_ftp_defaults("UDK_ENV_RUN")
```

说明：

- `get_env()` 返回 `EnvironmentSpec`；
- 默认值查询接口会执行“全局兜底 + 环境覆盖”的合并；
- 若底层上传/命令接口需要凭据，应从这些合并结果中取值并显式传入。

### 5.2 config.json 加载

```python
from config_loader import load_image_specs

image_specs = load_image_specs("config.json")
spec = image_specs["UnionS_SDK_Drv"]
```

`load_image_specs()` 会校验 JSON 顶层数组、必填字段与 `name` 唯一性，并返回 `Dict[str, ImageSpec]`。

### 5.3 WebHDFS 拉包

```python
from tools import HDFSClient, fetch_and_download_image

client = HDFSClient(base_url="https://hdfs-ngx1.turing-ci.hisilicon.com", verify_ssl=False)
real_name = fetch_and_download_image(client, spec, "runtime/debug_run")
```

`fetch_and_download_image()` 会：

1. 创建下载目录；
2. 若 `spec.link` 非空，直接在该目录按 `spec.image_name` 匹配；
3. 否则根据 `spec.base_link` 查找 newest 候选目录；
4. 下载命中的最新文件；
5. 返回真实文件名。

### 5.4 解包与 target_file 提取

```python
from unextract import extract_target_files

extracted = extract_target_files("runtime/debug_run/pkg.tar.gz", "runtime/debug_run", ["file1", "file2"])
```

支持 `.run`、`.tar.gz`、`.tgz`。提取结果会复制到 runtime 目录，并删除临时解包目录。

### 5.5 模板渲染

```python
from renderer import render_script

script = render_script(
    "echo ${hn922_drv}",
    [("hn922_drv", "UnionS_SDK_Drv", "HN922-driver-demo.run")],
)
```

`render_script()` 会替换 `${var_name}`。如果模板中仍有未替换变量，会抛 `ValueError`。

### 5.6 SCP/FTP 上传

```python
from tools import upload_files_via_scp, upload_files_via_ftp

upload_files_via_scp(
    host="141.131.72.195",
    local_files=["runtime/debug_run/UDK_ENV_RUN_123.sh"],
    username="root",
    password="root",
    remote_path="/root/autoEnv",
    port=22,
)

upload_files_via_ftp(
    host="141.131.72.195",
    local_files=["runtime/debug_run/UDK_ENV_RUN_123.sh"],
    username="root",
    password="root",
    remote_path="/root/autoEnv",
    port=21,
)
```

底层上传接口不读取全局默认值，必须显式传入 host、账号、密码、端口等参数。

### 5.7 SSH/Telnet 命令执行

```python
from tools import run_ssh_commands
from telnet import run_telnet_commands

ssh_outputs = run_ssh_commands(
    host="141.131.72.195",
    commands=["ls /root/autoEnv"],
    username="root",
    password="root",
    port=22,
)

telnet_outputs = run_telnet_commands(
    host="141.131.72.195",
    commands=["source /root/autoEnv/UDK_ENV_RUN_123.sh"],
    port=23,
    timeout=30.0,
    log_path="runtime/debug_run/telnet_commands.log",
)
```

### 5.8 process 上下文接口

自定义 process 示例：

```python
def custom_process(context) -> None:
    ssh = {"host": "141.131.72.195", "username": "root", "password": "root", "port": 22}
    context.upload_files_scp(ssh["host"], ssh["username"], ssh["password"], port=ssh["port"])
    outputs = context.send_ssh_commands(
        ssh["host"],
        [f"bash /root/autoEnv/{context.main_script_name}"],
        ssh["username"],
        ssh["password"],
        port=ssh["port"],
    )
    context.logger.info("remote output: %s", outputs)
```

`EnvironmentProcessContext` 常用属性与方法：

- `context.env` / `context.env_name`：当前环境定义和名称；
- `context.run_dir`：本次 runtime 目录；
- `context.downloaded_images`：变量名到 `DownloadedImage` 的映射；
- `context.script_paths`：模板名到生成脚本路径的映射；
- `context.upload_files`：默认将上传的本地文件列表；
- `context.main_script_name`：主脚本文件名；
- `context.download_image_var(...)`：process 内继续下载包；
- `context.extract_package_targets(...)`：提取已有包内文件；
- `context.render_template(...)`：额外渲染脚本并加入上传列表；
- `context.upload_files_scp(...)` / `context.upload_files_ftp(...)`：上传文件；
- `context.send_ssh_commands(...)` / `context.send_telnet_commands(...)`：执行远端命令。

## 6. 整体运行时调用链

一次 `python3 main.py` 的主要调用链如下：

1. `main.main()`：程序入口。
2. `main.choose_environment()`：调用 `list_env_names()`，展示已注册环境并读取用户选择。
3. `env_config.list_env_names()`：触发 `load_env_modules()` 自动导入 `ENV/*.py`，执行每个模块中的 `@ENV_REGISTER(...)`。
4. `env_executor.execute_environment(env_name)`：创建 run_id、初始化 logger、加载镜像规则并获取环境定义。
5. `config_loader.load_image_specs("config.json")`：读取并校验包规则。
6. `env_config.get_env(env_name)`：返回 `EnvironmentSpec`。
7. `normalize_image_var_ref(...)`：把 `image_vars` 的字符串/元组/字典统一成 `ImageVarRef`。
8. `ask_package_link_overrides(...)`：按用到的 `spec_name` 询问是否覆盖下载路径。
9. `HDFSClient(...)`：构造 WebHDFS 客户端。
10. 对每个 `image_var` 调用 `fetch_and_download_image(...)`：查找远端包、下载到 runtime。
11. 如配置了 `target_file`，调用 `extract_target_files(...)`：解包并复制目标文件到 runtime。
12. 汇总渲染三元组 `(var_name, spec_name, selected_real_name)`。
13. 对每个脚本模板调用 `render_script(...)`：替换占位符，生成可执行 `.sh`。
14. `enforce_runtime_size_limit(...)`：控制 `runtime/` 总容量。
15. 构造 `EnvironmentProcessContext`：把已下载文件、脚本、上传列表和底层接口封装给 process。
16. 调用 `env.process`；若未指定则调用 `default_environment_process(context)`。
17. `default_environment_process()`：先执行 `context.default_upload()`，再执行 `context.default_telnet_run()`。
18. `context.default_upload()`：询问 `scp/ftp`；按协议调用 `get_ssh_defaults()` 或 `get_ftp_defaults()`/`get_telnet_defaults()` 合并默认值，再上传文件。
19. `context.default_telnet_run()`：可选询问是否通过 Telnet 执行脚本；如启用则调用 `run_telnet_commands()`。
20. `execute_environment()` 返回 `(run_dir, main_script_name)`，终端打印后续登录执行提示。

## 7. 默认值合并链路重点

SSH 默认值不再使用独立 `ENV_SSH_DEFAULTS`：

```text
env_config.SSH_DEFAULTS
        +
EnvironmentSpec.ssh_defaults
        ↓
env_config.get_ssh_defaults(env_name)
        ↓
EnvironmentProcessContext.default_upload()
        ↓
ask_target_host() / ask_ssh_credentials()
        ↓
upload_files_via_scp(host, files, username, password, remote_path, port)
```

因此，新增环境时只需要维护 `ENV/<env_name>.py` 里的一个 `EnvironmentSpec`，连接默认值会和环境定义一起注册、一起读取、一起调试。
