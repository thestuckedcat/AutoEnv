# AutoEnv

一个用于**按环境模板自动拉取驱动包、渲染安装脚本并上传到目标服务器**的工具。

## 快速开始

1. 配置 `config.json` 中的镜像规则（name/link/base_link/image_name）。
2. 在 `env_config.py` 注册环境脚本模板与依赖镜像变量。
3. 运行：

```bash
python3 main.py
```

程序会列出所有可用环境，选择后自动：
- 根据 `name` 找到包规则；
- 若 `link` 为空则自动走 `newest` 目录；
- 正则匹配真实包名并下载；
- 将脚本模板中的 `${A1_image}` 变量替换为真实包名；
- 生成并上传 `.sh` + 包到目标机器 `/root/autoEnv`。
- 每次执行都会在 `runtime/YYYYMMDD_HHMMSS/` 下保存本次脚本和下载包；
- `runtime/` 总容量超过 1GB 时，会自动删除最早的历史执行目录（保留本次目录）；
- `logs/autoenv_YYYYMMDD_HHMMSS.log` 与 `runtime/YYYYMMDD_HHMMSS/` 使用同一 run_id，便于一一对应排查；

### link / base_link 优先级交互示例

```text
- A1 路径已配置 link=/a/b/c（直接回车保持现状）
- A1 路径 [默认: /a/b/c]:
# 直接回车 => 继续使用 link=/a/b/c（优先级最高）

- A2 路径 [默认: <自动 newest from base_link=/x/y/z>]:
# 直接回车 => 使用 base_link=/x/y/z，自动在 newest 目录内匹配包

- A3 路径 [默认: <必填: link/base_link 均为空>]:
# 直接回车 => 报错并阻止继续，必须手工输入路径
```

规则总结：
- 手工输入路径 > `link` > `base_link(newest 自动解析)`；
- 当 `link` 和 `base_link` 都为空时，程序会在输入阶段立即阻止继续。

## 目录结构

- `main.py`：交互入口。
- `composite_runner.py`：按数组顺序组合执行多个已注册环境。
- `models.py`：核心数据结构。
- `config_loader.py`：加载并校验 `config.json`。
- `env_config.py`：用户注册环境脚本。
- `renderer.py`：脚本变量替换与渲染。
- `tools.py`：WebHDFS 拉包与 SCP 上传。
- `logger.py`：统一日志初始化。
- `config.json`：镜像规则配置。
- `logs/`：运行日志目录。

## 依赖

```bash
pip install requests urllib3 paramiko scp
```

## 组合环境执行

当你希望把多个已有环境按顺序串起来执行时，可使用 `composite_runner.py`：

```bash
python3 composite_runner.py
```

也可以在代码里直接调用：

```python
from composite_runner import run_composite_environments
run_composite_environments(["A_ENV_RUN", "B_ENV_RUN"])
```

注意：组合执行时，每个子环境都会分别询问包路径（link）和目标服务器。
- `env_config.py` 中提供了组合环境示例 `A_B_CHAIN_RUN = ["A_ENV_RUN", "B_ENV_RUN"]`。


## SSH 默认值配置

可在 `env_config.py` 配置全局或按环境的 SSH 默认值：

- `SSH_DEFAULTS`：全局默认用户名/密码/端口
- `ENV_SSH_DEFAULTS`：按环境覆盖默认值（如用户名/密码）

执行时在输入目标服务器后，会继续询问用户名、密码、端口；如果直接回车，就使用上述默认值。

> 说明：底层上传接口 `upload_files_via_scp` 无默认凭据，调用方必须显式传入 `username` 和 `password`，以避免凭据来源歧义。

## target_file 解包提取

`config.json` 的每个镜像规则可以额外配置 `target_file`，支持字符串或字符串数组：

```json
{
  "name": "UnionS_SDK_Drv",
  "link": "",
  "base_link": "/compilepackage/CI_Version/torino/br_hisi_trunk_ai",
  "image_name": "^HN922-driver-[a-zA-Z0-9.]+-rtos[a-zA-Z0-9.]+\\.aarch64-debug\\.run$",
  "target_file": ["file1", "file2"]
}
```

当 `target_file` 非空时，程序会先按 `image_name` 下载包，再使用 `unextract.py` 调用 Git `sh.exe`/`sh` 解包：
- `.run`：执行 `sh <xxx.run> --noexec --extract=<runtime>/run_tmp`；
- `.tar.gz`/`.tgz`：执行 `tar -xzf <包> -C <runtime>/tar_tmp`；
- 从临时目录递归查找并复制目标文件到本次 `runtime/YYYYMMDD_HHMMSS/` 目录；
- 提取完成后删除临时目录。

## Telnet 串口执行与 FTP 发包

`env_config.py` 现在提供：
- `TELNET_DEFAULTS` / `ENV_TELNET_DEFAULTS`：Telnet 串口服务器默认 host/port/timeout；
- `FTP_DEFAULTS` / `ENV_FTP_DEFAULTS`：通过 FTP 向 Telnet 服务器可访问目录发包的默认配置；
- `EnvironmentSpec.telnet_commands`：可配置逐条发送的 Telnet 命令，命令中支持 `${script_name}` 占位符；
- `EnvironmentSpec.upload_protocol`：默认发包方式，支持 `scp` 或 `ftp`。

`telnet.py` 提供 `run_telnet_commands()`：连接 Telnet 串口后先发送三次回车，取最后一次输出的非空最后行作为当前 shell 开头；随后逐条发送命令，并在输出中再次看到该 shell 开头时判定本条命令结束，输出会写入指定 log。

## Debug 调试接口

新增功能对应的调试入口统一放在 `debug/` 目录，每个文件既可以 import 其中的 `debug_*` 函数，也可以直接命令行运行：

```bash
# 调试 config.json 中 target_file 的解析，并查看指定环境 Telnet/FTP 默认值
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

## 环境注册与可扩展 process

`env_config.py` 中的 `EnvironmentSpec.image_vars` 支持两种常用写法：

```python
image_vars={
    # 两字段：脚本变量名 -> config.json 中的 name，脚本里 ${hn922_pkg} 渲染为下载包名
    "hn922_pkg": "UnionS_SDK_Drv",

    # 三字段：脚本变量名 -> (config.json 中的 name, name 下的 target_file)
    # 下载并解包 UnionS_SDK_Drv 后，脚本里 ${hn922_drv} 渲染为 file1 的文件名
    "hn922_drv": ("UnionS_SDK_Drv", "file1"),
}
```

也可以用显式字典写法：`{"name": "UnionS_SDK_Drv", "target_file": "file1"}`。
如果第三个字段指定了 `target_file`，该变量会使用提取出的目标文件名渲染脚本；未指定时仍使用原始下载包名。

`script_template` 已升级为 `script_templates` 字典，每个模板都有名字索引，便于一个环境生成多个脚本：

```python
script_templates={
    "prepare": "... ${hn922_drv} ...",
    "install": "...",
}
```

`EnvironmentSpec.process` 是环境专属处理函数指针，默认可以使用 `default_environment_process`；自定义 process 会收到 `EnvironmentProcessContext`，可按环境需要分步编排：

- `context.upload_files_scp(...)` / `context.upload_files_ftp(...)`：上传已准备的 image 和脚本；
- `context.upload_file_to_telnet_path(...)`：通过 FTP 把单个指定文件发送到 Telnet 服务器可访问的指定远端路径；
- `context.send_telnet_commands(...)` / `context.send_ssh_commands(...)`：向远端逐条发送命令并获取输出；
- `context.download_image_var(...)`：在 process 中继续从 WebHDFS 拉取 image_vars 相关包，并按需提取 target_file；
- `context.extract_package_targets(...)`：对已有包继续解压并提取目标文件；
- `context.render_template(...)`：在 process 中额外打包模板为 `.sh`。

这样后续新增环境时，可以在自己的 process 里根据 SSH/Telnet 输出决定下一步上传哪些文件、执行哪些命令或生成哪些脚本。

## ENV 目录拆分

环境定义已经从 `env_config.py` 拆分到 `ENV/` 目录：

- `ENV/<env_name>.py`：单个环境的注册文件，只描述该环境的 `image_vars`、`script_templates`、`process` 等；
- `ENV/composite_envs.py`：组合环境注册入口；
- `ENV/_template_env.py`：新增环境模板，文件名以下划线开头，不会被自动导入注册；
- `env_common.py`：环境文件常用公共导入，包括 `ENV_REGISTER`、`EnvironmentSpec`、`default_environment_process`；
- `env_config.py`：只保留注册表、自动加载 `ENV/*.py` 和默认值查询接口。

新增环境建议复制 `ENV/_template_env.py` 为 `ENV/<your_env>.py`，然后通过：

```python
from env_common import ENV_REGISTER, EnvironmentSpec, default_environment_process

@ENV_REGISTER("MY_ENV_RUN")
def build_env(env_name: str) -> EnvironmentSpec:
    return EnvironmentSpec(
        env_name=env_name,
        image_vars={
            "pkg_var": "ConfigJsonName",
            "target_file_var": ("ConfigJsonName", "file_inside_package"),
        },
        script_templates={"main": "... ${pkg_var} ${target_file_var} ..."},
        process=default_environment_process,
        ssh_defaults={"host": "192.168.1.100", "username": "root", "password": "root"},
        telnet_defaults={"host": "192.168.1.100", "port": 23},
        ftp_defaults={"username": "root", "password": "root"},
    )
```

如果需要调整某个环境默认连接参数，直接改对应的 `ENV/<env_name>.py`，让配置和环境绑定在一起；如果需要新增组合环境，则在 `ENV/composite_envs.py` 调用 `register_composite_env(...)`。
