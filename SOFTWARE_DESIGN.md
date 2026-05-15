# AutoEnv 软件设计说明

## 1. 目标与范围

AutoEnv 将“拉包 + 解包/提取 + 脚本渲染 + 上传服务器 + 可选远端执行”流程通用化，使用户通过 `config.json` 和 `ENV/<env_name>.py` 即可新增环境。

核心能力：

- 通过 `config.json` 管理镜像来源、正则匹配规则和 `target_file`；
- 通过 `ENV/<env_name>.py` 注册 `EnvironmentSpec`，把环境依赖、脚本模板、process 和连接默认值绑定在一起；
- 执行时自动选择环境、解析依赖、下载真实包、按需提取目标文件、替换模板变量、上传文件；
- 支持 SCP/FTP 发包，支持 SSH/Telnet 命令执行；
- 支持组合环境按顺序串行执行。

## 2. 总体架构

### 2.1 配置层

- `config.json`：包规则清单，字段包括 `name`、`link`、`base_link`、`image_name`、`target_file`。
- `ENV/<env_name>.py`：单环境定义文件，通过 `@ENV_REGISTER(...)` 返回 `EnvironmentSpec`。
- `ENV/composite_envs.py`：组合环境注册文件。
- `env_config.py`：注册表、自动导入 `ENV/*.py`、全局兜底默认值、默认值合并接口。
- `env_common.py`：环境定义文件常用导入聚合。

### 2.2 核心逻辑层

- `main.py`：交互入口，选择环境并执行。
- `composite_runner.py`：组合环境执行入口。
- `config_loader.py`：读取并校验包规则。
- `env_executor.py`：主流程编排、runtime 管理、process 上下文封装。
- `renderer.py`：模板变量替换与未替换变量检查。
- `env_processes.py`：默认 process。

### 2.3 基础设施层

- `tools.py`：WebHDFS 客户端、SCP/FTP 上传、SSH 命令执行。
- `telnet.py`：Telnet 串口命令客户端。
- `unextract.py`：`.run` / `.tar.gz` / `.tgz` 解包和目标文件提取。
- `logger.py`：日志初始化。

## 3. 数据模型

`models.py` 定义主要模型：

- `ImageSpec`：单个包规则（`name`、`link`、`image_name`、`base_link`、`target_file`）。
- `ImageVarRef`：环境脚本变量到包规则和可选 `target_file` 的映射。
- `EnvironmentSpec`：环境定义，包含：
  - `env_name`：环境名；
  - `image_vars`：脚本变量到 `config.json` 包规则的映射；
  - `script_templates` / `script_template`：脚本模板；
  - `process`：环境专属处理函数；
  - `telnet_commands`：默认 Telnet 命令；
  - `upload_protocol`：默认上传协议；
  - `ssh_defaults`：环境级 SSH 默认值；
  - `telnet_defaults`：环境级 Telnet 默认值；
  - `ftp_defaults`：环境级 FTP 默认值。
- `DownloadedImage`：运行期每个变量最终解析到的下载包、提取文件和脚本渲染文件名。
- `RuntimeContext`：预留的运行上下文模型。

## 4. 配置设计

### 4.1 config.json

格式为数组，每项：

- `name`：必填且唯一；
- `link`：可空，不为空时优先使用；
- `base_link`：可空，当 `link` 为空时用于自动查找最新日期目录中的 `newest`；
- `image_name`：必填，正则表达式；
- `target_file`：可选，字符串或字符串数组。

路径优先级：手工输入路径 > `link` > `base_link(newest 自动解析)`。

### 4.2 ENV/<env_name>.py

每个环境文件只描述一个环境的 `EnvironmentSpec`。按环境的连接默认值必须放在 `EnvironmentSpec.ssh_defaults` / `telnet_defaults` / `ftp_defaults` 内，不再维护独立的 `ENV_SSH_DEFAULTS`、`ENV_TELNET_DEFAULTS`、`ENV_FTP_DEFAULTS` 映射。

```python
@ENV_REGISTER("UDK_ENV_RUN")
def build_env(env_name: str) -> EnvironmentSpec:
    return EnvironmentSpec(
        env_name=env_name,
        image_vars={
            "hn922_drv": "UnionS_SDK_Drv",
            "mami_drv": "Ubengine_Drv",
            "mami_testcase": "Ubengine_TestCase",
        },
        script_templates={"main": "... ${hn922_drv} ${mami_drv} ${mami_testcase} ..."},
        process=default_environment_process,
        upload_protocol="scp",
        ssh_defaults={"username": "root", "password": "root", "host": "141.131.72.195", "port": 22},
        telnet_defaults={"host": "141.131.72.195", "port": 23, "timeout": 30.0},
        ftp_defaults={"username": "root", "password": "root", "port": 21, "remote_path": "/root/autoEnv"},
    )
```

### 4.3 默认值合并

`env_config.py` 提供全局兜底默认值：`SSH_DEFAULTS`、`TELNET_DEFAULTS`、`FTP_DEFAULTS`。

运行期查询时：

1. 复制全局兜底默认值；
2. 读取 `get_env(env_name)` 得到 `EnvironmentSpec`；
3. 用 `EnvironmentSpec.*_defaults` 覆盖同名字段；
4. 将合并结果交给交互函数展示默认值；
5. 用户最终确认后的值显式传入底层上传或命令接口。

底层接口不直接读取默认值，避免凭据来源歧义。

## 5. 核心流程

1. `main.py` 启动后调用 `list_env_names()`。
2. `env_config.load_env_modules()` 自动导入 `ENV/*.py`，触发环境注册。
3. 用户选择环境。
4. `execute_environment()` 初始化日志和 runtime 目录。
5. `load_image_specs()` 读取 `config.json`。
6. `get_env()` 获取 `EnvironmentSpec`。
7. 标准化 `image_vars` 为 `ImageVarRef`。
8. 询问包路径覆盖。
9. 使用 `HDFSClient` 和 `fetch_and_download_image()` 下载包。
10. 按需调用 `extract_target_files()` 提取 `target_file`。
11. 构造渲染三元组并调用 `render_script()` 生成脚本。
12. 执行 runtime 容量控制。
13. 构造 `EnvironmentProcessContext`。
14. 调用环境自定义 `process`，没有时使用 `default_environment_process()`。
15. 默认 process 先上传，再可选 Telnet 执行。
16. 返回本次 runtime 目录和主脚本名。

## 6. 错误处理

- `config.json` 顶层不是数组、缺字段或 `name` 重复时，`load_image_specs()` 抛 `ValueError`。
- 环境引用不存在的 `name` 时，`execute_environment()` 抛 `KeyError`。
- `link` 和 `base_link` 都为空且用户未输入路径时，输入阶段持续阻止继续。
- 正则非法或远端目录不可访问时，HDFS 查找/下载阶段抛出相关异常。
- 找不到匹配包时抛 `FileNotFoundError`。
- 解包类型不支持时，`extract_target_files()` 抛 `ValueError`。
- 目标文件不存在时，`extract_target_files()` 抛 `FileNotFoundError`。
- 脚本变量未替换完时，`render_script()` 抛 `ValueError`。
- 上传或远端命令失败时，底层接口抛出连接、认证或命令异常并记录日志。

## 7. 日志与 runtime

- 日志输出到控制台和 `logs/autoenv_<run_id>.log`。
- 本次运行文件保存到 `runtime/<run_id>/`。
- 组合环境会在 run_id 后追加子环境 suffix，便于区分。
- runtime 总容量超过 1GB 时删除最早历史执行目录，保留本次目录。

## 8. 扩展点

- 新增环境：复制 `ENV/_template_env.py`，填写 `EnvironmentSpec`。
- 新增组合环境：在 `ENV/composite_envs.py` 调用 `register_composite_env(...)`。
- 自定义 process：为 `EnvironmentSpec.process` 传入同签名函数，使用 `EnvironmentProcessContext` 编排底层接口。
- 新增底层传输方式：扩展 `EnvironmentProcessContext` 和 `default_upload()`。
- 非交互 CLI：未来可在 `main.py`/`env_executor.py` 上增加参数入口。

## 9. 交付物

- 代码：`main.py`、`composite_runner.py`、`env_executor.py`、`env_config.py`、`models.py`、`tools.py`、`telnet.py`、`unextract.py` 等。
- 环境示例：`ENV/a_env_run.py`、`ENV/b_env_run.py`、`ENV/_template_env.py`。
- 文档：`README.md`、`SOFTWARE_DESIGN.md`、`FUNCTION_IO_BOUNDARIES.md`、`docs/ENVIRONMENT_SETUP.md`。
