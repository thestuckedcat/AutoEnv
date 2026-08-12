# AutoEnv 重构详细设计

> 目标分支：`UNIFY_ENV_WITH_BLOCK`（原始重构基线来自 `UNIFY_ENV`）
> 文档状态：当前实现说明；第 1–27 节保留原始重构设计，第 28 节记录后续扩展
> 兼容范围：只兼容现有 `config.json` 格式和 WebHDFS 查包语义，不兼容旧 Python API

## 1. 背景与目标

AutoEnv 用于在 Windows 上快速准备和启动一个或多个 Linux/设备环境。它需要完成包下载、文件提取、远端上传、SSH/Telnet 命令执行、结果判断、运行记录和上次参数复用。

重构前实现已经包含 WebHDFS、SCP、FTP、SSH、Telnet、`.run`/`.tar.gz` 解包、脚本渲染和组合环境等能力，但 `env_executor.py`、`EnvironmentProcessContext` 和环境配置承担了过多职责。底层接口返回值不统一，日志无法作为结构化运行记录，组合环境也需要额外注册表和特殊执行路径。

本次重构的目标不是增加工作流引擎，而是把 AutoEnv 收敛为：

> 一组带统一结果、自动日志、运行存档和交互默认值的顺序执行基础接口；环境脚本通过普通 Python 代码组合这些接口。

## 2. 已确认的设计原则

1. 执行顺序只由 Python 代码顺序决定。
2. 不实现 `Step`、DAG、依赖关系、调度器或并发。
3. 不提供脚本级独立 logging 接口；基础操作自动记录日志。
4. SSH Host 与 Telnet 对象分开注册、分开交互、分开连接。
5. 注册时只确认参数，首次实际操作时才连接。
6. 一次脚本运行内复用连接；断连后不自动重放当前操作。
7. 网络、认证、超时和远端命令失败等运行期问题返回结果对象。
8. 配置无效、类型错误和非法调用等编程问题抛异常。
9. 包下载、文件提取和文件上传互相独立，任何接口都不隐式触发另一个接口。
10. 所有上传和提取源文件只能来自本次运行的 `packages` 目录。
11. 已注册脚本可以像普通函数一样串行调用；每次调用仍是独立脚本运行。
12. `run` 与 `rerun` 共用同一套参数存档逻辑。
13. 第一版只支持 SSH 用户名/密码认证；Telnet 连接后已经进入 Shell，不处理登录提示。

## 3. 两层架构

```text
AutoEnv/
├── autoenv/                         # 通用工具层
│   ├── __init__.py                  # 面向注册脚本的公共 API
│   ├── cli.py                       # 菜单、run、rerun
│   ├── registry.py                  # @register_script 与自动发现
│   ├── runtime.py                   # RunContext、运行目录、last run
│   ├── selectors.py                 # package/extra_file/match
│   ├── command_files.py             # 上传文件映射与 shell 文件生成
│   ├── results.py                   # 所有结果数据结构和枚举
│   ├── package_manager.py           # config.json 与 WebHDFS 下载
│   ├── extractor.py                 # extract_file_from
│   ├── ssh_host.py                  # SSH、SCP、SFTP
│   ├── ftp_host.py                  # 独立普通 FTP 上传
│   ├── interface.py                 # 结构化非交互启动契约
│   ├── web_tools.py                 # Web Tool 注册与发现
│   ├── telnet_client.py             # Telnet 与 Shell 模式探测
│   └── recorder.py                  # 内部日志和 JSON 写入
├── scripts/                         # 脚本注册层
│   ├── __init__.py
│   └── example.py                   # 可复制的完整示例
├── tests/                           # 单元测试
│   └── README.md                    # UT 目标与失败排查
├── docs/
│   ├── QUICK_START.md               # 快速使用、快速测试和文档导航
│   ├── AutoEnv-Refactor-Detailed-Design.md
│   └── ENVIRONMENT_REGISTRATION_GUIDE.md
├── webPage/                         # 当前本地 Web 控制台
├── environments/                    # 本机环境档案，默认 Git 忽略
├── adapt_interface.py               # JSON/参数非交互入口
├── startWeb.py                      # Web 启动入口
├── config.json
├── logs/                            # 自动生成，Git 忽略
├── state/                           # 自动生成，Git 忽略
├── main.py
├── pyproject.toml
└── README.md
```

通用工具层不知道具体环境业务，也不决定下一步执行什么。脚本注册层不直接操作 Paramiko、Socket、文件日志或 WebHDFS 请求。

## 4. 公共 API

注册脚本只从 `autoenv` 顶层导入稳定接口：

```python
from autoenv import (
    CommandPhase,
    CommandProtocol,
    CommandStatus,
    SSHDefaults,
    TelnetDefaults,
    extra_file,
    match,
    package,
    register_func,
    register_script,
)
```

核心调用形式：

```python
package_a1 = package("A1")
driver = extra_file("driver.bin")
install_script = extra_file("install.sh")
host = ctx.register_ssh_host("server", defaults=SSHDefaults(...))
console = ctx.register_telnet(
    "console",
    defaults=TelnetDefaults(...),
    uploaded_files_from="server",
)

ctx.download_package(package_a1)
ctx.extract_file_from(source=package_a1, target_file="driver.bin")

host.scp_upload(local_file=package_a1, remote_dir="/root/autoEnv")
host.sftp_upload(local_file=driver, remote_dir="/root/autoEnv")
ctx.generate_sh_file("install.sh", "#!/bin/sh\ntar -xf S{A1}\n")
host.sftp_upload(local_file=install_script, remote_dir="/root/autoEnv")
host.execute("bash /root/autoEnv/S{install.sh}", timeout=600)
console.execute("source /root/start.sh", timeout=300)
```

环境函数开头集中声明文件选择器与连接对象，后续流程只复用这些变量。`generate_sh_file()` 是 `RunContext` 方法，不作为无上下文的顶层公共 API 导出，因为替换必须读取本次运行的成功上传记录。

## 5. 文件选择器

上传和提取接口不接受裸字符串路径，只接受三种显式选择器：

### 5.1 `package(name)`

```python
package("A1")
```

- 在 `config.json` 中查找 `name == "A1"`。
- 读取该项的 `image_name` 正则。
- 在本次运行的 `packages` 根目录匹配文件。
- 不访问 HDFS，不自动下载。
- 没有匹配时返回本地文件不存在。
- 多个匹配时返回歧义错误，不擅自选择。

### 5.2 `extra_file(filename)`

```python
extra_file("manual_firmware.bin")
```

- 只查找本次 `packages/manual_firmware.bin`。
- 不读取 `config.json`。
- 不允许绝对路径、`..` 或逃离 `packages`。

### 5.3 `match(pattern)`

```python
match(r"^firmware-.*\.bin$")
```

- 正则只匹配本次 `packages` 根目录中的普通文件。
- 不递归搜索。
- 按文件名忽略大小写升序排序后取第一个匹配项，保证结果稳定。
- 正则非法属于配置/编程错误，直接抛异常。

## 6. 脚本注册与独立运行

```python
@register_script(name="start_udk", description="启动 UDK 环境")
def start_udk(ctx):
    ...
```

装饰器负责：

- 注册唯一名称和描述。
- 保留函数的直接可调用能力。
- 每次调用创建独立 `RunContext`。
- 创建运行目录、日志、参数和结果文件。
- 传播当前执行模式 `run` 或 `rerun`。
- 捕获未处理异常、关闭连接并落盘最终结果。

入口函数返回规则：

- 函数体返回 `CommandResult`、`UploadResult`、`DownloadResult` 或 `ExtractResult`：包装器以其 `success` 作为脚本结果。
- 函数体返回 `None`：函数正常结束，脚本成功。
- 抛异常：脚本状态为程序错误，记录异常和堆栈。
- 不把 `True`/`False` 作为合法结果，避免丢失失败原因。

无论函数体返回哪一种合法值，装饰后的注册脚本对外调用都统一返回 `ScriptResult`：

```python
@dataclass(frozen=True)
class ScriptResult:
    run_id: str
    script_name: str
    success: bool
    status: str
    run_dir: str
    package_dir: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    final_operation: object | None
    error_type: str | None
    error_message: str | None
```

这样 `start_udk()` 既可以由 CLI 启动，也可以在组合脚本中稳定使用 `result.success` 判断。

### 6.1 启动后固定 func

主流程可以在成功返回前、函数末尾注册常用检查或维护流程：

```python
@register_script(name="start_udk", description="启动 UDK 环境")
def start_udk(ctx):
    host = ctx.register_ssh_host("udk", defaults=SSHDefaults(...))
    result = host.execute("/root/start.sh", timeout=600)
    if not result.success:
        return result

    @register_func(name="check_status", description="检查环境状态")
    def check_status(_func_ctx):
        return host.execute("cat /tmp/env_status", timeout=30)

    return result
```

- `register_func()` 只能在当前正在执行的注册脚本内部调用，不能放在模块顶层。
- 注册定义位于主流程末尾；主流程失败或注册前提前返回时不显示菜单。
- func 名称在本次脚本运行中唯一，函数必须接收一个 `RunContext` 参数。
- 主流程成功后循环显示全部 func 和 `0. exit`；执行、失败或异常后均回到菜单。
- func 接收与主流程相同的 `RunContext`，可调用相同接口，并可通过 Python 闭包复用主流程的 Host、Telnet、选择器和其他对象。
- 退出菜单后才关闭连接和 recorder。func 失败只记录独立执行摘要，不覆盖成功主流程的 `ScriptResult`。

## 7. 组合脚本语义

组合脚本直接调用已经注册的脚本：

```python
@register_script(name="start_full_env", description="依次启动 UDK 和 MAMI")
def start_full_env(ctx):
    result = start_udk()
    if not result.success:
        return result
    return start_mami()
```

每个子脚本都有独立的：

- `RunContext`、运行目录和 `packages`。
- SSH/Telnet 对象和连接生命周期。
- 参数交互和 last-run 文件。
- 日志、操作编号和最终结果。

组合脚本不共享上下文，不复用同名 Host，也不需要额外适配器或组合注册表。`run start_full_env` 时子脚本分别交互；`rerun start_full_env` 时执行模式向下传播，每个子脚本分别使用自己的 last-run。

若某个被调用子脚本注册了 func，它会在该子脚本主流程成功后先进入自己的 func 菜单；选择退出并关闭子脚本上下文后，组合脚本才继续下一项。

## 8. 运行模式和交互默认值

### 8.1 `run`

```bash
autoenv run start_udk
```

- 读取 `state/last_runs/start_udk.json` 作为优先默认值。
- 没有历史值时使用脚本中的 `SSHDefaults`、`TelnetDefaults` 和 `config.json` 默认路径。
- 逐项展示并允许修改。
- 直接回车使用当前显示的默认值。
- package 定义 `base_link` 时始终显示 `!newest` 快捷方式；输入后忽略历史手工覆盖并切换为 `base_link_newest`。

### 8.2 `rerun`

```bash
autoenv rerun start_udk
```

- 必须存在该脚本的 last-run。
- 完全复用历史参数，不进行参数确认；若脚本注册了 func，主流程成功后仍显示显式操作菜单。
- last-run 不存在时明确报错并退出，不降级成普通 `run`。
- 包路径选择会复用，但 `base_link/newest` 每次仍重新解析最新包，不绑定上次下载的具体文件。

### 8.3 默认值优先级

普通 `run` 的最终值优先级：

```text
用户本次非空输入
    > 当前脚本的 last-run 值
    > 脚本声明的 defaults
    > 字段内置默认值
```

密码按需求允许明文保存在 `params.json` 和 last-run 中；终端和 `run.log` 必须脱敏。

## 9. 运行目录与自动记录

每次注册脚本调用立即创建：

```text
logs/
└── 20260715_101530_123_start_udk/
    ├── run.log
    ├── params.json
    ├── result.json
    └── packages/
```

启动时打印绝对路径：

```text
脚本：start_udk
运行目录：C:\...\logs\20260715_101530_123_start_udk
包目录：C:\...\logs\20260715_101530_123_start_udk\packages
日志文件：C:\...\logs\20260715_101530_123_start_udk\run.log
```

`packages` 同时用于：

- `download_package()` 的下载目标。
- `extract_file_from()` 的源和输出目录。
- SCP/SFTP 的唯一 Windows 取包目录。
- 用户手工添加额外包。

日志操作编号只表示真实调用顺序，不表示依赖关系。不存在并发，因此操作编号也是完整时间顺序。

## 10. 参数与结果存档

### 10.1 `params.json`

按参数确认进度立即更新，典型内容：

```json
{
  "script_name": "start_udk",
  "ssh_hosts": {
    "udk_host": {
      "host": "192.168.1.100",
      "port": 22,
      "username": "root",
      "password": "root"
    }
  },
  "telnet_connections": {
    "udk_console": {
      "host": "192.168.1.200",
      "port": 23,
      "timeout": 30.0,
      "shell_mode": "auto"
    }
  },
  "packages": {
    "A1": {
      "path_mode": "base_link_newest",
      "path_override": null
    }
  }
}
```

### 10.2 `result.json`

```json
{
  "run_id": "20260715_101530_123_start_udk",
  "script_name": "start_udk",
  "success": true,
  "status": "success",
  "started_at": "2026-07-15T10:15:30.123+08:00",
  "finished_at": "2026-07-15T10:18:42.512+08:00",
  "duration_ms": 192389,
  "final_operation_id": "0008",
  "error_type": null,
  "error_message": null,
  "func_runs": [
    {
      "name": "check_status",
      "success": false,
      "status": "command_failed",
      "error_type": "NON_ZERO_EXIT_CODE",
      "error_message": "remote command exited with code 1"
    }
  ]
}
```

`func_runs` 按用户实际选择顺序保存启动后 func 的每次执行摘要；未选择或未注册时为空数组。

### 10.3 last-run 更新规则

```text
state/last_runs/<script_name>.json
```

- 脚本正常成功、返回运行期失败结果，或在完整收集参数后发生异常，都保存本次完整参数。
- 失败运行也成为 last-run，便于修复远端问题后立即重试。
- 进程被强制终止或参数未收集完整时，不覆盖上一份完整 last-run。
- last-run 只保存参数，不保存执行结果和下载得到的具体包文件。

## 11. Host 与 Telnet 注册模型

### 11.1 SSH Host

```python
host = ctx.register_ssh_host(
    "main_server",
    defaults=SSHDefaults(
        host="192.168.1.100",
        port=22,
        username="root",
        password="root",
        connect_timeout=30.0,
    ),
)
```

SSH Host 负责：

- SSH 命令执行。
- SCP 单文件上传。
- SFTP 单文件上传。

### 11.2 Telnet

```python
console = ctx.register_telnet(
    "board_console",
    defaults=TelnetDefaults(
        host="192.168.1.200",
        port=23,
        timeout=30.0,
        shell_mode="auto",
    ),
    uploaded_files_from="main_server",
)
```

Telnet 对象只负责 Telnet 命令，不共享 SSH Host 的连接配置。可选的 `uploaded_files_from` 只声明命令占位符使用哪个已注册 SSH Host 的成功上传记录；它不共享连接，也不会触发上传。省略时，仅在来源唯一可推断时允许使用占位符。

### 11.3 名称与注册行为

- 对象名称在同一个 `RunContext` 内必须唯一。
- 注册时只收集和保存参数，不连接目标。
- 同一脚本运行中重复注册同名对象属于编程错误。
- 不同注册脚本中的同名对象互相独立，分别询问和连接。

## 12. 连接生命周期

### 12.1 SSH

- 首次调用 `execute()`、`scp_upload()` 或 `sftp_upload()` 时连接。
- 成功后在当前脚本运行中复用 SSH Transport。
- 每条 SSH 命令使用独立 Channel，因此工作目录和环境变量不跨命令保留。
- 如果两次操作之间连接失效，下一次操作可重新连接。
- 命令执行中断连时只返回结果，不自动重放命令。

### 12.2 Telnet

- 首次 `execute()` 时连接、识别提示符并探测 Shell 模式。
- 当前脚本运行中保持同一个会话，Shell 状态可跨命令保留。
- 意外断连时当前命令返回 `DISCONNECTED`；下一条命令重新连接并重新探测。
- 重新连接后不保证之前的工作目录和环境变量仍存在。

### 12.3 关闭

脚本结束时，无论成功、失败或抛异常，`RunContext` 都关闭 SSH Transport、SFTP Session、SCP Channel 和 Telnet Socket。脚本无需手工 `close()`。

## 13. 包下载

### 13.1 接口

```python
result = ctx.download_package(package("A1"))
```

只接受 `package()`；`extra_file()` 和 `match()` 没有 HDFS 配置，传入时属于编程错误。

### 13.2 `config.json` 兼容语义

继续使用当前顶层数组格式和字段：

```json
{
  "name": "A1",
  "link": "",
  "base_link": "/compilepackage/CI_Version/torino/br_hisi_trunk_ai",
  "image_name": "^HN922-driver-.*\\.run$",
  "target_file": []
}
```

- `name`：唯一配置名。
- `link`：明确远端目录。
- `base_link`：`link` 为空时作为默认的 `newest` 候选查找根目录；即使同时定义了 `link`，普通 `run` 也始终可以通过 `!newest` 明确选择它。
- `image_name`：匹配远端文件名的正则。
- `target_file`：为兼容旧配置继续允许和解析，但新流程不使用它进行下载或自动提取。

远端目录优先级：

```text
本次输入 !newest（明确选择 base_link/newest）
    > 本次交互路径覆盖
    > config.json.link
    > config.json.base_link 下最新日期目录/newest
```

`base_link` 流程保持当前项目的精确行为：

1. 列出 `base_link` 的直接子目录。
2. 按 WebHDFS `modificationTime` 选择最新子目录，不依赖目录名称排序。
3. 在该目录的直接子项中筛选名称包含 `newest`（忽略大小写）的目录。
4. 将这些候选按 `modificationTime` 倒序检查，使用第一个能匹配到目标包的目录。

同一候选目录中有多个远端文件匹配时，选择 `modificationTime` 最新的文件。

### 13.3 下载过程

1. 校验配置并解析远端目录。
2. 查找匹配的最新远端文件。
3. 下载到 `<filename>.part`。
4. 比较本地大小与 HDFS 返回的文件长度。
5. 计算下载文件 MD5。
6. 校验通过后原子替换最终文件。
7. 记录远端路径、大小、修改时间、本地路径、MD5 和耗时。
8. 不执行解包或提取。

如果最终文件已存在，旧文件在新下载完成前保持不变。下载失败时删除 `.part`，保留旧文件。

### 13.4 `DownloadResult`

主要字段：

| 字段 | 含义 |
|---|---|
| `run_id` / `operation_id` | 运行和操作标识 |
| `success` / `status` | 成功标记和详细状态 |
| `config_name` / `image_pattern` | 配置名和文件正则 |
| `remote_dir` / `remote_file` | 实际远端目录和文件 |
| `remote_size` / `remote_modified_at` | HDFS 元数据 |
| `package_dir` / `local_file` | 本次包目录和最终文件 |
| `local_size` | 下载后大小 |
| `local_existed` | 下载前同名文件是否存在 |
| `local_md5_before` / `local_md5_after` | 覆盖前后摘要 |
| `md5_changed` | 内容是否变化 |
| `size_verified` | 大小校验结果 |
| `started_at` / `finished_at` / `duration_ms` | 时间信息 |
| `error_type` / `error_message` | 失败详情 |

运行期状态包括 `remote_directory_not_found`、`newest_directory_not_found`、`remote_file_not_found`、`connection_failed`、`download_timeout`、`download_failed`、`size_verification_failed` 和 `local_replace_failed`。

## 14. 显式文件/目录提取

### 14.1 接口

```python
result = ctx.extract_file_from(
    source=package("A1"),
    target_file="driver.bin",
)
```

```python
result = ctx.extract_file_from(
    source=extra_file("manual_package.tar.gz"),
    target_dir="firmware",
)
```

定义：

```python
extract_file_from(
    source: PackageSelector | ExtraFileSelector | MatchSelector,
    *,
    target_file: str | None = None,
    target_dir: str | None = None,
) -> ExtractResult
```

`target_file` 与 `target_dir` 必须且只能提供一个；否则抛 `ValueError`。

### 14.2 共同规则

- 源包必须已经存在于本次 `packages`。
- 不自动调用 `download_package()`。
- 支持 `.run`、`.tar.gz` 和 `.tgz`。
- 原包保留，临时解包目录最终删除。
- 目标路径禁止绝对路径和 `..`。
- 提取内容不得逃离 `packages`。

### 14.3 文件提取

1. 优先按包内完整相对路径查找。
2. 完整路径不存在时递归按文件名查找。
3. 多个同名文件返回 `MULTIPLE_TARGET_FILES_FOUND`。
4. 输出到 `packages` 根目录，保持文件名。
5. 同名文件存在时覆盖并记录前后 MD5。

### 14.4 目录提取

1. 优先按包内完整相对目录查找。
2. 完整路径不存在时递归按目录名查找。
3. 多个同名目录返回 `MULTIPLE_TARGET_DIRS_FOUND`。
4. 完整复制到 `packages/<目录名>/`，保留内部层级。
5. 目标目录存在时先完整删除旧目录，再复制新目录，避免内容混合。
6. 按相对路径排序，以每个文件的路径、大小和 MD5 生成确定性 `tree_md5`，记录覆盖前后文件数量和摘要。

## 15. SCP/SFTP 单文件上传

### 15.1 接口

```python
result = host.scp_upload(
    local_file=package("A1"),
    remote_dir="/root/autoEnv",
    overwrite=True,
)
```

```python
result = host.sftp_upload(
    local_file=extra_file("install.sh"),
    remote_dir="/root/autoEnv",
    overwrite=False,
)
```

### 15.2 规则

- 每次只接受一个文件选择器。
- 上传接口只解析当前 `packages`，绝不下载文件。
- 本地文件不存在时立即返回失败，不等待用户补文件、不重试。
- 远端目录不存在时递归创建。
- `overwrite=True` 为默认值；远端同名文件存在时覆盖。
- 覆盖前读取远端 MD5，上传后再次读取并与本地 MD5 校验。
- 即使覆盖前 MD5 与本地相同，也仍执行明确请求的覆盖。
- `overwrite=False` 且远端文件存在时不上传，返回 `remote_file_exists`。
- 上传后 MD5 不一致返回 `md5_verification_failed`。
- SCP 不依赖 SFTP：目录创建、覆盖检查和前后 MD5 通过普通 SSH 命令完成，文件传输只使用 SCP。SCP 的远端参数使用 `remote_dir`，由远端按本地 basename 落盘，与 main 分支行为一致，也支持未启用 SFTP subsystem 的精简 SSH Server。

### 15.3 `UploadResult`

主要字段：

```text
run_id, operation_id, protocol, target_name,
selector_type, selector, package_dir, resolved_local_file,
remote_dir, remote_file, success, status,
overwrite, remote_existed,
local_md5, remote_md5_before, remote_md5_after,
md5_changed, md5_verified,
started_at, finished_at, duration_ms,
error_type, error_message
```

### 15.4 上传文件映射与 shell 文件生成

成功完成 MD5 校验的 SCP/SFTP 上传会记录：选择器字符串、实际远端文件名和目标 SSH Host。失败上传不进入映射。映射只在当前 `RunContext` 有效，并按目标 Host 隔离。

SSH/Telnet 命令和 `ctx.generate_sh_file()` 支持 `S{file_name}`：

- `file_name` 必须等于已声明 `package()`、`extra_file()` 或 `match()` 的字符串参数，不是 Python 变量名。
- 替换值只取远端路径的文件名部分，不把远端目录写入占位符。
- SSH 命令只解析上传到当前 Host 的文件。
- Telnet 通过 `uploaded_files_from` 指定 SSH 来源；未指定时来源必须唯一。
- 一个生成脚本中的所有占位符必须在生成前成功上传到同一个 Host。
- 未上传、上传失败、跨目标无共同来源、目标歧义和畸形占位符均在发送命令或写文件前抛出 `ValueError`。

```python
ctx.generate_sh_file(
    "install.sh",
    """#!/bin/sh
cd /root/autoEnv
tar -xf "S{A1}"
""",
)
```

`file_name` 必须是无目录的 `.sh` 文件名，输出固定到本次运行的 `packages`。`script` 必须是一整段字符串；函数只替换占位符，保留 shebang、换行类型、末尾换行和命令布局，不自动增加 shell 选项。

## 16. SSH/Telnet 命令执行

### 16.1 接口

```python
result = host.execute(
    "bash /root/autoEnv/install.sh",
    timeout=600,
)
```

```python
result = console.execute(
    "source /root/start_slave.sh",
    timeout=300,
)
```

命令运行期间持续读取并实时显示远端输出；清理后的完整正文同时保存在 `CommandResult.output` 中。结束摘要不重复打印正文，超时或断连也保留已经收到的部分输出。

### 16.1.1 按输出触发原始字节

SSH Host 与 Telnet 对象提供相同的阻塞式契约：

```python
result = target.execute_on_output(
    "reboot",
    keyword="Press Ctrl+B",
    send_data=b"\x02",
    timeout=90,
)
```

接口在一个操作内完成初始命令发送、持续输出读取、跨接收分片的大小写敏感关键词匹配和一次性原始字节发送。`send_data` 必须是非空 `bytes`，且不隐式追加换行。成功只表示匹配和发送完成；超时使用 `KEYWORD_NOT_FOUND`，响应发送失败使用 `RESPONSE_SEND_FAILED`，所有失败均保留部分输出且不自动重放命令。

SSH 复用现有 Transport，但每次操作仍使用独立 Channel。Telnet 在响应发送后丢弃当前 Socket 及旧 Shell 提示符状态，下一次操作懒重连，避免设备进入 Bootloader 后仍按原 POSIX Shell 解析。Ctrl+A 到 Ctrl+Z 对应 `0x01` 到 `0x1A`；常用字节写法和 Enter/Backspace 差异以环境注册指南为准。

### 16.2 `CommandResult`

```python
@dataclass(frozen=True)
class CommandResult:
    run_id: str
    operation_id: str
    protocol: CommandProtocol
    target_name: str
    command: str
    status: CommandStatus
    phase: CommandPhase
    exit_code: int | None
    stdout: str
    stderr: str
    raw_output: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    error_type: str | None
    error_message: str | None
    expected_disconnect: bool
    disconnected: bool
```

便利属性：

- `success`：仅成功状态为 `True`。
- `timed_out`：状态为 `TIMEOUT` 时为 `True`。
- `output`：合并后的标准输出和错误输出。

### 16.3 状态枚举

| 状态 | 含义 | 命令是否可能已执行 |
|---|---|---|
| `SUCCESS` | 已确认成功；通常退出码为 0 | 是 |
| `COMMAND_FAILED` | 命令完成但退出码非 0 | 是 |
| `CONNECTION_FAILED` | 无法建立连接 | 否 |
| `AUTH_FAILED` | 连接到服务但认证失败 | 否 |
| `TIMEOUT` | 等待当前阶段超时 | 取决于 `phase` |
| `DISCONNECTED` | 命令发送后、结果返回前断连 | 是，结果不确定 |
| `PROTOCOL_ERROR` | 协议通道、提示符或数据解析异常 | 取决于 `phase` |
| `RESULT_UNKNOWN` | 收到输出但无法确认退出结果 | 是 |

### 16.4 阶段枚举

`CONNECT`、`AUTHENTICATE`、`DETECT_PROMPT`、`SEND_COMMAND`、`WAIT_OUTPUT`、`PARSE_RESULT`、`COMPLETE`。

阶段用于区分例如：

- `CONNECTION_FAILED + CONNECT`：命令没有发送。
- `TIMEOUT + WAIT_OUTPUT`：命令已经发送，远端可能仍在运行。
- `DISCONNECTED + WAIT_OUTPUT`：命令已经发送，但最终结果不确定。

框架不会自动重试超时或断连命令，避免重复安装、重启或升级。

## 17. Telnet Shell 自动探测

`TelnetDefaults.shell_mode` 支持：

- `auto`：默认；连接后用无副作用标记探测 POSIX 能力。
- `posix`：强制使用 `$?` 和结束标记取得退出码。
- `prompt_only`：只等待提示符恢复，不假设存在 POSIX 退出码。

自动探测逻辑：

1. 发送回车并识别提示符，包括 `#root` 这类自定义提示符。
2. 发送唯一的探测标记命令。
3. 能识别标记时采用 `posix`。
4. 标记不可用但提示符可识别时降级为 `prompt_only`。
5. 提示符也无法识别时返回 `PROTOCOL_ERROR/PROMPT_NOT_FOUND`。

`prompt_only` 命令通常返回 `exit_code=None` 和 `RESULT_UNKNOWN`。注册脚本可以根据输出内容决定是否继续，框架不根据 `error`、`failed` 等文本擅自判断业务结果。

## 18. 预期断连

SSH 和 Telnet 均支持：

```python
result = target.execute("reboot", expect_disconnect=True)
```

规则：

- 命令返回退出码 0：成功。
- 命令已成功发送后发生断连：成功，`exit_code=None`，`disconnected=True`。
- 命令返回非零退出码：失败。
- 建立连接前失败：失败。
- 命令尚未成功发送就断连：失败。

## 19. 运行期结果与异常边界

返回结果对象的情况：

- DNS、连接拒绝、网络不可达、连接超时。
- SSH 认证失败。
- 命令非零退出。
- 命令等待超时或中途断连。
- Telnet 提示符/退出码解析失败。
- HDFS 查询、下载、校验和本地替换失败。
- 本地包未找到、选择器匹配歧义。
- 远端目录创建、上传和 MD5 校验失败。
- 解包目标不存在或目标重名。

直接抛异常的情况：

- `config.json` 缺失、JSON 非法、顶层不是数组、配置名重复。
- 引用不存在的配置名，`image_name` 为空或正则非法。
- 重复注册脚本名、Host 名或 Telnet 名。
- 端口不在 `1..65535`，timeout 小于等于 0。
- 命令为空、参数类型错误。
- 选择器或提取目标使用绝对路径、`..` 或逃逸目录。
- `target_file` 和 `target_dir` 同时提供或都未提供。
- 内部程序逻辑错误。

## 20. 实时输出与日志

SSH 必须在命令运行期间并行读取并实时显示 stdout/stderr，避免大输出填满 Paramiko Channel 缓冲区造成死锁。Telnet 按接收顺序持续读取并实时显示原始字节，同时保留完整 `raw_output`。

`run.log` 自动记录：

- 脚本开始、结束和总耗时。
- 交互后最终参数，密码脱敏。
- 本次包目录和包路径选择。
- 下载、提取、上传、连接和命令操作。
- SSH stdout/stderr 与 Telnet 原始输出。
- MD5、目录 tree MD5、文件大小和耗时。
- 运行期错误和未捕获异常堆栈。
- 最终脚本状态。

终端与 `run.log` 使用不同的展示格式，但内容来自同一结果：

- `run.log` 中的操作结果保持单行 JSON，便于检索和程序解析。
- 终端将脚本开始/结束和每个操作结果显示为带空行的摘要块，突出成功或失败状态。
- 下载、提取和上传摘要分行显示源、目标、校验值及耗时。
- SSH/Telnet 长短命令都实时显示远端输出并累计到 `result.output`；结束摘要分行显示 command、status、phase、exit code、耗时和错误，不重复整段正文。

脚本层不公开 `ctx.logger`，避免日志内容和结构失控。

## 21. 历史包容量控制

默认限制所有历史 `packages` 内容总计 1 GiB，可通过配置调整。

- 只统计 `logs/*/packages`。
- 超限时从最旧运行开始清理包内容。
- 永久保留 `run.log`、`params.json` 和 `result.json`。
- 当前正在运行的 `packages` 永不清理。
- 下载失败的 `.part` 立即删除。
- 清理动作写入当前运行日志。

## 22. CLI

两种入口调用同一实现：

```bash
python main.py
python main.py run start_udk
python main.py rerun start_udk

autoenv
autoenv run start_udk
autoenv rerun start_udk
```

没有指定脚本时显示名称和描述菜单。主流程成功且注册了 func 时，再循环显示该运行的 func 菜单；`rerun` 只跳过参数确认，不跳过这个显式操作菜单。未知脚本名、缺失 last-run 和导入脚本失败需要返回非零退出码和明确错误。

## 23. 原始重构第一版明确不支持

- 工作流 DAG、Step、依赖、自动分支和并发。
- 后台任务调度器或跨本地重启接管远端任务。
- SSH 私钥、跳板机、代理和 SSH Agent。
- Telnet `login:`/`Password:` 登录流程。
- FTP 上传（已由第 28 节后续扩展实现）。
- 上传多文件列表。
- 上传接口的隐式下载。
- 下载接口的自动解包或自动提取。
- 旧 `ENV/`、`EnvironmentSpec`、`EnvironmentProcessContext`、`execute_environment()` 和旧组合注册表兼容层。

## 24. 单元测试范围

`tests/` 必须覆盖：

1. 三种选择器的构造、解析、安全限制、无匹配、多匹配和稳定首项。
2. `config.json` 加载、字段兼容、重复名和非法正则。
3. `link`、`base_link/newest`、交互覆盖优先级和远端最新文件选择。
4. `.part`、大小校验、原子覆盖、MD5 和下载失败保护。
5. 文件与目录提取、精确路径、递归名称、歧义、覆盖和 tree MD5。
6. SSH/Telnet 注册、交互默认值、last-run、rerun 参数无交互和启动后 func 菜单。
7. SSH 连接复用、失效重连、不重放命令和关闭资源。
8. SCP/SFTP 单文件、目录创建、覆盖、`overwrite=False` 和 MD5 校验。
9. SSH 所有 `CommandStatus`、阶段、实时输出、部分输出和预期断连。
10. Telnet 自动探测、POSIX、prompt-only、提示符失败和预期断连。
11. 注册脚本自动发现、返回规则、异常记录和组合脚本独立运行。
12. 运行目录、操作编号、日志脱敏、参数/结果 JSON 和 last-run 更新。
13. 1 GiB 历史包清理策略和当前运行保护。
14. CLI 菜单、`run`、`rerun`、错误码和两种入口等价性。
15. 上传文件占位符的成功/失败上传、多 Host 隔离、Telnet 来源、共同目标和完整 shell 文本保持。
16. 统一环境脚本契约：集中声明、选择器复用、生成前上传、生成后上传、命令目标一致和注册发现。
17. `register_func` 的作用域、重复名、同一上下文、循环选择、退出、失败/异常隔离、结果摘要和主流程失败时不展示菜单。

网络相关测试使用 mock/fake，不依赖真实 HDFS、SSH 或 Telnet 服务器。少量可选集成测试必须单独标记，默认单元测试可离线运行。

各测试文件的简单实现、重点 UT 的逐条目标和失败排查入口维护在 [`tests/README.md`](../tests/README.md)。新增或改变测试契约时必须同步该说明。

## 25. 迁移顺序

1. 建立新包结构、结果模型、选择器和内部记录器。
2. 实现 RunContext、参数存档、脚本注册和 CLI。
3. 迁移并封装现有 WebHDFS 逻辑，移除自动提取。
4. 迁移 `.run`/tar 解包为显式 `extract_file_from()`。
5. 实现 SSH Host、实时命令、SFTP 和 SCP 单文件上传。
6. 实现 Telnet 自动 Shell 探测和统一命令结果。
7. 为 SSH/Telnet 增加按输出关键词发送原始字节的阻塞接口。
8. 添加 `scripts/example.py` 和注册指南。
9. 添加完整单元测试并修复边界问题。
10. 更新 README、依赖和 Git 忽略规则。
11. 删除旧 Python API 和失效文档，保留 `config.json`。

## 26. 验收标准

- 新人只阅读环境注册指南即可新增并运行脚本。
- 所有基础接口都有明确类型、统一结果和自动日志。
- `run`、`rerun` 和组合脚本符合本文语义。
- 下载、提取、上传严格分离。
- SSH/Telnet 长短命令输出实时可见，完整清理正文保存在 `result.output`，所有失败情况可由结果对象区分。
- SSH/Telnet 可在同一次阻塞操作中匹配跨分片输出并发送一次原始响应字节，超时、断连和发送失败均保留部分输出。
- 命令与完整 shell 文本中的 `S{file_name}` 只解析本次运行中对正确目标成功上传的文件，并替换为实际文件名。
- 新增环境脚本可通过统一离线契约 UT 检查，不连接 HDFS、SSH 或 Telnet。
- 主流程成功后可循环执行同一上下文中的注册 func，并在退出前保持连接可用。
- 每次脚本调用都有独立可追溯的目录、参数和结果。
- 单元测试在无外部服务器条件下通过。
- 原始重构变更提交到 `UNIFY_ENV`；后续扩展在 `UNIFY_ENV_WITH_BLOCK` 演进并按发布要求合入默认分支。

## 27. 安全边界

第一版以受控的实验室内网为运行前提，并保持旧项目的连接兼容行为：

- WebHDFS 客户端默认不校验 TLS 证书。只应连接可信内网中的既有 HDFS 服务；接入不可信网络前必须改为校验证书。
- SSH 首次连接会接受并记录进程内未知的主机密钥，不校验本机 `known_hosts`。因此目标地址和网络必须可信，不能把该行为用于开放网络。
- Telnet 的命令和输出均为明文传输，只能用于隔离、可信的管理网络。
- SSH 密码按已确认需求明文写入 `params.json` 和 `state/last_runs/`；日志与终端会脱敏，但仍需使用 Windows 文件权限保护仓库的 `logs/` 与 `state/`。
- 提取 `.run` 时会执行该自解压程序。框架设置执行超时并检查产物路径，但无法证明程序本身无恶意行为，因此只能处理可信构建系统生成的包。
- `expect_disconnect=True` 只能用于确认会主动断连的命令；它不能证明重启或关机后的目标最终恢复正常。

这些限制是兼容当前环境所作的明确取舍，不应被理解为适用于互联网或多租户环境的安全默认值。
## 28. `UNIFY_ENV_WITH_BLOCK` 后续扩展

本分支在原顺序执行模型上增加了结构化入口和本地 Web 控制台，但没有引入 DAG 或隐式并发。新增边界如下：

- `RemoteDownloadResult` 是可记录、可序列化且可直接作为上传源解析的操作结果。
- SSH Host 同时拥有 SCP/SFTP 下载；远端正则必须在指定目录唯一匹配。
- 普通 FTP 是独立连接对象，只提供显式上传，不共享 SSH Transport。
- `LaunchRequest` 把环境档案与请求覆盖合并后注入 `RunContext`；非交互模式中缺参立即失败。
- Web Tools 使用独立注册表和 JSON 字段 Schema，不能执行环境拉起职责。
- Agent CLI 文件上传只负责安全落盘和路径转换，Python/ZIP 的适配由仓库 skill 静态审查。

新增模块、目录、数据流、安全边界、已知限制和接手入口见 [`WEB_ARCHITECTURE_AND_HANDOFF.md`](WEB_ARCHITECTURE_AND_HANDOFF.md)。新增行为的可操作说明见 [`ENVIRONMENT_REGISTRATION_GUIDE.md`](ENVIRONMENT_REGISTRATION_GUIDE.md#22-scp-sftp-下载与结果复用) 和 [`../webPage/QUICK_START.md`](../webPage/QUICK_START.md)。
