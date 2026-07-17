# AutoEnv 环境注册指南

本文面向第一次接触 AutoEnv 的使用者。照着“最小示例”即可注册一个环境；后续章节列出所有常用接口、可选参数和错误处理方式。

如果只想先完成安装、最小脚本和快速测试，请先看 [`QUICK_START.md`](QUICK_START.md)。

> 本文描述 `UNIFY_ENV` 新接口。旧版 `ENV/EnvironmentSpec` 接口不再使用。

## 1. 最快上手

### 1.1 创建脚本文件

在 `scripts/` 下创建 `start_demo.py`：

```python
from autoenv import SSHDefaults, package, register_script


@register_script(name="start_demo", description="下载并安装演示环境")
def start_demo(ctx):
    demo_package = package("A1")
    host = ctx.register_ssh_host(
        "demo_server",
        defaults=SSHDefaults(
            host="192.168.1.100",
            port=22,
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

    return host.execute(
        "bash /root/autoEnv/install.sh",
        timeout=600,
    )
```

### 1.2 确认包配置

`package("A1")` 对应 `config.json` 中 `name == "A1"`：

```json
[
  {
    "name": "A1",
    "link": "",
    "base_link": "/compilepackage/CI_Version/torino/br_hisi_trunk_ai",
    "image_name": "^HN922-driver-.*\\.run$",
    "target_file": []
  }
]
```

### 1.3 运行

```bash
python main.py run start_demo
```

或安装项目后：

```bash
autoenv run start_demo
```

程序启动后会打印本次 `packages` 的绝对路径。下载包、手工包、提取结果和上传源文件都从这个目录管理。

## 2. 一个环境脚本的基本结构

```python
from autoenv import register_script


@register_script(
    name="start_my_env",
    description="启动我的测试环境",
)
def start_my_env(ctx):
    # 1. 集中声明文件选择器和连接对象
    # 2. 显式下载或检查本地包
    # 3. 按需提取
    # 4. 上传
    # 5. 按需生成完整 shell 脚本
    # 6. 执行命令并判断结果
    # 7. 可选：在主流程末尾注册启动后 func
    # 8. 返回最后一个结果，或正常返回 None
    ...
```

`name` 必须唯一，并用于：

- CLI 脚本名称。
- 运行目录名称。
- `state/last_runs/<name>.json`。
- 菜单显示和日志定位。

`description` 可省略，但建议填写一句能区分环境用途的说明。

### 2.1 集中声明、顺序复用

每个独立环境函数先集中声明文件选择器和连接对象。后续下载、提取、上传和命令步骤只复用这些变量：

```python
def start_ubengine(ctx):
    ubengine_run = package("UBEngine")
    ubengine_testcase = match(r"^UBEngine-testcase-.*\.tgz$")
    install_script = extra_file("install.sh")
    host_1260 = ctx.register_ssh_host("host_1260", defaults=SSHDefaults(...))

    result = ctx.download_package(ubengine_run)
    if not result.success:
        return result

    result = host_1260.sftp_upload(ubengine_run, "/root/autoEnv")
    if not result.success:
        return result

    # 后续继续使用 ubengine_testcase、install_script 和 host_1260。
```

变量名是 Python 代码中的业务名称；`S{...}` 中使用的仍是选择器构造函数里的字符串，例如 `S{UBEngine}`。

### 2.2 注册启动后的固定流程

使用 `register_func()` 可以把环境拉起后常用的检查或维护动作放进循环菜单：

```python
from autoenv import register_func, register_script


@register_script(name="start_demo", description="启动演示环境")
def start_demo(ctx):
    host = ctx.register_ssh_host("demo", defaults=SSHDefaults(...))
    result = host.execute("/root/start.sh", timeout=600)
    if not result.success:
        return result

    @register_func(name="check_status", description="检查 READY 状态")
    def check_status(_func_ctx):
        status = host.execute("cat /tmp/env_status", timeout=30)
        if status.success and "READY" not in status.output:
            return status.with_failure("environment status is not READY")
        return status

    @register_func(name="list_files", description="查看环境目录")
    def list_files(_func_ctx):
        return host.execute("ls -la /root/autoEnv", timeout=30)

    return result
```

主流程成功后会反复显示：

```text
1. check_status             检查 READY 状态
2. list_files               查看环境目录
0. exit
```

规则：

- `register_func()` 必须写在环境函数内部、所有主流程操作之后，不能放在模块末尾的顶层作用域。
- 每个 func 必须接收一个 `ctx` 参数；它与主流程收到的是同一个 `RunContext`。
- func 可调用主流程能够调用的接口；优先通过闭包复用已经声明的 Host、Telnet、选择器，不要重复注册同名对象。
- func 名称在当前运行中必须唯一，描述建议填写。
- 主流程失败时不进入菜单。func 返回失败或抛异常时记录结果并回到菜单，不覆盖已成功主流程的结果。
- 选择 `0` 后退出菜单，随后关闭本次运行的 SSH/Telnet 连接。

## 3. 文件选择器：必须显式说明文件来源

### 3.1 `package("A1")`

```python
local_file=package("A1")
```

含义：读取 `config.json` 中 `name=A1` 的 `image_name`，然后在本次 `packages` 中匹配文件。

注意：用在上传或提取中时，它只查本地，不会自动下载。

### 3.2 `extra_file("install.sh")`

```python
local_file=extra_file("install.sh")
```

含义：直接查找本次 `packages/install.sh`，不读取 `config.json`。

适合用户手工放入包目录的脚本、补丁或固件。

### 3.3 `match(r"正则")`

```python
local_file=match(r"^firmware-.*\.bin$")
```

含义：在本次 `packages` 根目录中按正则匹配；多个匹配按文件名排序后取第一个。

### 3.4 禁止的写法

```python
# 不接受裸字符串
host.scp_upload(local_file="A1", remote_dir="/root/autoEnv")

# 不允许绝对路径
extra_file(r"D:\packages\driver.run")

# 不允许逃离 packages
extra_file("../driver.run")
```

## 4. 注册 SSH Host

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

### 4.1 `SSHDefaults` 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `host` | `str` | `""` | IP 或主机名；交互后不能为空 |
| `port` | `int` | `22` | SSH 端口，范围 `1..65535` |
| `username` | `str` | `"root"` | 用户名 |
| `password` | `str` | `""` | 密码；参数存档允许明文，日志脱敏 |
| `connect_timeout` | `float` | `30.0` | 建立连接超时，必须大于 0 |

注册时只确认参数，不立即连接。第一次 SSH、SCP 或 SFTP 操作才建立连接。

### 4.2 一个脚本注册多个 Host

```python
server_a = ctx.register_ssh_host("server_a", defaults=SSHDefaults(host="10.0.0.10"))
server_b = ctx.register_ssh_host("server_b", defaults=SSHDefaults(host="10.0.0.11"))
```

名称在本次脚本内必须唯一。不同脚本可以使用相同名称，它们仍是独立对象并分别询问。

## 5. 注册 Telnet

```python
console = ctx.register_telnet(
    "board_console",
    defaults=TelnetDefaults(
        host="192.168.1.200",
        port=23,
        timeout=30.0,
        shell_mode="auto",
    ),
)
```

`uploaded_files_from` 是可选的已注册 SSH Host 名称。只有 Telnet 命令需要使用 `S{file_name}` 时才需要配置；SSH Host 必须先注册：

```python
host_1260 = ctx.register_ssh_host("host_1260", defaults=SSHDefaults(...))
console = ctx.register_telnet(
    "board_console",
    defaults=TelnetDefaults(...),
    uploaded_files_from="host_1260",
)
```

该参数只选择上传记录，不共享 SSH 连接。未配置时，占位符来源必须能唯一推断。

### 5.1 `TelnetDefaults` 参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `host` | `str` | `""` | Telnet IP 或主机名 |
| `port` | `int` | `23` | Telnet 端口，范围 `1..65535` |
| `timeout` | `float` | `30.0` | 连接/默认命令超时，必须大于 0 |
| `shell_mode` | `str` | `"auto"` | `auto`、`posix` 或 `prompt_only` |

### 5.2 Shell 模式怎么选

- 一般保持 `auto`。
- 明确支持 `$?` 和 `printf` 时可以用 `posix`。
- U-Boot、RTOS 或自定义 CLI 只依赖提示符时用 `prompt_only`。
- `#root` 只是提示符外观，不能单独判断系统类型；`auto` 会实际探测能力。

Telnet 默认认为连接后已经进入命令行，不处理 `login:` 和 `Password:`。

## 6. 下载 HDFS 包

```python
result = ctx.download_package(package("A1"))
if not result.success:
    return result
```

下载接口只接受 `package()`。

### 6.1 路径选择

普通 `run` 会询问包目录，直接回车使用默认值。优先级为：

```text
本次输入 > 上次运行值 > config.link > config.base_link/newest 候选解析
```

`rerun` 不询问，复用上次路径选择。如果是 `base_link` 模式，仍会按 WebHDFS 修改时间重新选择最新子目录及其中名称含 `newest` 的候选目录，再选择最新匹配包；不会绑定上次下载的具体文件。

### 6.2 下载不会解包

```python
ctx.download_package(package("A1"))
```

只会把完整包放入本次 `packages`。即使 `config.json` 存在 `target_file`，新流程也不会自动提取。

### 6.3 常用结果字段

```python
result.success
result.status
result.remote_file
result.local_file
result.local_md5_after
result.size_verified
result.error_type
result.error_message
```

下载使用 `.part` 临时文件和大小校验；失败不会破坏已经存在的同名完整文件。

## 7. 显式提取文件

```python
result = ctx.extract_file_from(
    source=package("A1"),
    target_file="bin/driver.bin",
)
if not result.success:
    return result
```

查找顺序：

1. 包内完整相对路径 `bin/driver.bin`。
2. 如果不存在，再递归查找文件名 `driver.bin`。
3. 多个同名文件时返回失败，不猜测目标。

输出为本次 `packages/driver.bin`。

## 8. 显式提取目录

```python
result = ctx.extract_file_from(
    source=package("A1"),
    target_dir="lib/firmware",
)
if not result.success:
    return result
```

整个目录会复制为 `packages/firmware/` 并保留内部层级。目标目录已经存在时会先删除旧目录再完整复制，防止新旧文件混合。

一次调用只能使用 `target_file` 或 `target_dir` 其中一个：

```python
# 错误：两个都没有
ctx.extract_file_from(source=package("A1"))

# 错误：两个同时存在
ctx.extract_file_from(
    source=package("A1"),
    target_file="driver.bin",
    target_dir="firmware",
)
```

支持的源包类型：`.run`、`.tar.gz`、`.tgz`。

## 9. SCP 上传

```python
result = host.scp_upload(
    local_file=package("A1"),
    remote_dir="/root/autoEnv",
    overwrite=True,
)
if not result.success:
    return result
```

## 10. SFTP 上传

```python
result = host.sftp_upload(
    local_file=extra_file("driver.bin"),
    remote_dir="/root/autoEnv",
    overwrite=True,
)
if not result.success:
    return result
```

SCP 和 SFTP 规则一致：

- 每次只上传一个文件。
- 远端目录不存在时自动递归创建。
- 默认覆盖远端同名文件。
- 自动记录本地、覆盖前远端和上传后远端 MD5。
- 上传后校验远端 MD5 与本地一致。

### 10.1 禁止覆盖

```python
result = host.sftp_upload(
    local_file=extra_file("settings.json"),
    remote_dir="/root/autoEnv",
    overwrite=False,
)
```

远端文件已存在时不上传，`result.success` 为 `False`，`status` 为 `remote_file_exists`。如果远端不存在则正常上传。

### 10.2 文件不存在

上传只查当前 `packages`。文件不存在会立即失败，并打印包目录；不会自动下载，也不会停下来等待用户补文件。

### 10.3 从整段文本生成 shell 文件

`ctx.generate_sh_file()` 接收完整 shell 文本，适合直接复制已有脚本。函数不会拆分命令，也不会自动添加 shebang 或 `set -e`；只把已成功上传文件的 `S{file_name}` 替换成实际文件名：

```python
result = host_1260.sftp_upload(ubengine_run, "/root/autoEnv")
if not result.success:
    return result

ctx.generate_sh_file(
    "install.sh",
    """#!/bin/sh
set -eu
cd /root/autoEnv
tar -xf "S{UBEngine}"
./install
""",
)
```

生成文件名必须是无目录的 `.sh` 文件名。文件位于本次运行的 `packages/install.sh`，可以通过开头声明的 `install_script = extra_file("install.sh")` 上传。输入必须是一整段字符串，换行和布局保持不变。

`S{...}` 使用选择器构造函数中的字符串，不使用 Python 变量名；例如变量 `ubengine_run = package("UBEngine")` 对应 `S{UBEngine}`。占位符只替换为实际文件名，脚本里的目录仍由用户保留。多个占位符必须在生成前成功上传到同一个 SSH Host。

## 11. 执行 SSH 命令

```python
result = host.execute(
    "bash /root/autoEnv/install.sh",
    timeout=600,
)

if not result.success:
    return result
```

每次 SSH `execute()` 都是独立命令通道：

```python
# 错误理解：第二条不会继承 cd
host.execute("cd /root/autoEnv")
host.execute("./install.sh")

# 推荐
host.execute("cd /root/autoEnv && ./install.sh")
```

远端 stdout/stderr 会实时显示并写入日志。

## 12. 执行 Telnet 命令

```python
result = console.execute(
    "source /root/start_slave.sh",
    timeout=300,
)
```

同一脚本运行中 Telnet 会话保持连接，所以工作目录和 Shell 状态可以跨命令保留：

```python
console.execute("cd /root/autoEnv")
result = console.execute("./start_slave.sh")
```

意外断连后下一条命令会重新连接；新会话不保证保留之前状态。

### 12.1 按输出关键词发送数据

SSH Host 和 Telnet 对象都提供阻塞式 `execute_on_output()`：它先发送初始命令，然后持续读取并实时记录输出。累计输出中第一次出现大小写敏感的 `keyword` 后，接口立即向同一通道原样发送 `send_data` 并返回。

```python
result = console.execute_on_output(
    "reboot",
    keyword="Press Ctrl+B",
    send_data=b"\x02",
    timeout=90,
)

if not result.success:
    return result
```

关键词可以被拆在多个网络包中，例如先收到 `Press Ctrl`、再收到 `+B`，仍然能够匹配。`send_data` 必须是非空 `bytes`，不会自动增加 `\r`、`\n` 或 `\r\n`：

```python
# 发送一个控制字符
send_data=b"\x02"          # Ctrl+B

# 发送一条需要回车确认的命令
send_data=b"boot recovery\r\n"
```

返回规则：

| 情况 | `status` | `phase` | `error_type` |
|---|---|---|---|
| 命中并成功发送 | `SUCCESS` | `COMPLETE` | `None` |
| 超时仍未命中 | `TIMEOUT` | `WAIT_OUTPUT` | `KEYWORD_NOT_FOUND` |
| 命中前断连 | `DISCONNECTED` | `WAIT_OUTPUT` | `CONNECTION_LOST` |
| 命中但响应发送失败 | `PROTOCOL_ERROR` | `SEND_COMMAND` | `RESPONSE_SEND_FAILED` |
| SSH 命令正常退出但从未命中 | `COMMAND_FAILED` | `COMPLETE` | `KEYWORD_NOT_FOUND` |

`SUCCESS` 只证明关键词已经出现且响应字节已经交给连接通道，不证明设备一定进入了目标模式。接口不会自动重发初始命令或响应。完整的匹配前输出保留在 `stdout` 和 `raw_output` 中。

发送响应后，SSH 本次独立命令 Channel 会关闭，但 SSH Transport 仍可复用。Telnet 响应通常会让设备从 Linux Shell 切换到 Bootloader，因此接口会断开当前 Telnet Socket，清除旧提示符和 Shell 模式；同一个 Telnet 对象下次调用时会自动重新连接。

普通 SSH 在执行 `reboot` 后通常会断开，无法继续观察 BIOS、BootROM 或 Bootloader 输出。卡启动时点进入模式应使用串口，或者使用串口服务器映射出来的 Telnet 连接。SSH 上的该接口更适合安装器确认、交互式脚本输入等场景。

#### 常用控制字符对照

控制字符必须发送实际字节，不能发送它们的文字名称。Ctrl+A 到 Ctrl+Z 的 ASCII 值依次为 `0x01` 到 `0x1A`，可用 `bytes([ord(letter.upper()) & 0x1F])` 计算。

| 按键/字符 | 十六进制 | Python `bytes` | 常见含义 |
|---|---:|---|---|
| Ctrl+A | `0x01` | `b"\x01"` | SOH，部分终端的行首 |
| Ctrl+B | `0x02` | `b"\x02"` | STX，常用于进入 Bootloader 菜单 |
| Ctrl+C | `0x03` | `b"\x03"` | ETX，通常中断前台程序 |
| Ctrl+D | `0x04` | `b"\x04"` | EOT，终端中常表示输入结束 |
| Ctrl+H / Backspace | `0x08` | `b"\x08"` 或 `b"\b"` | 退格；部分终端使用 DEL |
| Ctrl+I / Tab | `0x09` | `b"\x09"` 或 `b"\t"` | 水平制表符 |
| Ctrl+J / LF | `0x0A` | `b"\x0a"` 或 `b"\n"` | Unix 换行 |
| Ctrl+L / FF | `0x0C` | `b"\x0c"` 或 `b"\f"` | 终端中常用于清屏 |
| Ctrl+M / CR | `0x0D` | `b"\x0d"` 或 `b"\r"` | 回车 |
| Ctrl+Q / XON | `0x11` | `b"\x11"` | 恢复软件流控 |
| Ctrl+S / XOFF | `0x13` | `b"\x13"` | 暂停软件流控 |
| Ctrl+Z | `0x1A` | `b"\x1a"` | POSIX 终端中常挂起前台程序 |
| Esc | `0x1B` | `b"\x1b"` | 转义键/ANSI 序列起始符 |
| Space | `0x20` | `b" "` | 空格，有些启动菜单要求按任意键 |
| DEL | `0x7F` | `b"\x7f"` | 删除；部分终端把它作为 Backspace |
| Enter（CR） | `0x0D` | `b"\r"` | 串口/终端常用回车 |
| Enter（LF） | `0x0A` | `b"\n"` | Linux 管道或部分程序使用 |
| Enter（CRLF） | `0x0D 0x0A` | `b"\r\n"` | Telnet 和部分串口命令行常用 |

实际设备需要哪一种 Enter/Backspace 取决于串口程序、终端服务器和目标固件配置，应以人工终端抓包或设备说明为准。

## 13. 判断命令结果

最常用判断：

```python
result = host.execute("bash install.sh")

if not result.success:
    return result

if "READY" not in result.output:
    # 业务输出不符合预期时，由脚本决定下一步
    return result.with_failure("未检测到 READY")
```

### 13.1 `CommandStatus`

| 状态 | 说明 | 建议处理 |
|---|---|---|
| `SUCCESS` | 已确认成功 | 继续 |
| `COMMAND_FAILED` | 非零退出码 | 查看 `exit_code`/`stderr` |
| `CONNECTION_FAILED` | 未连接成功 | 可由脚本决定重试 |
| `AUTH_FAILED` | 认证失败 | 检查账号密码 |
| `TIMEOUT` | 当前阶段超时 | 不要盲目重发，命令可能仍运行 |
| `DISCONNECTED` | 命令后断连 | 结果不确定，按业务确认 |
| `PROTOCOL_ERROR` | 通道或协议解析失败 | 查看 `phase` 和原始输出 |
| `RESULT_UNKNOWN` | 无法取得可靠退出结果 | 常见于 prompt-only Telnet，检查输出 |

### 13.2 常用字段

```python
result.success
result.status
result.phase
result.exit_code
result.stdout
result.stderr
result.raw_output
result.output
result.timed_out
result.error_type
result.error_message
result.duration_ms
```

SSH/Telnet 不会因为输出中出现 `error` 或 `failed` 字样就自动判失败。退出码或协议结果由框架判断，业务文本由环境脚本判断。

## 14. 预期重启或断连

执行 `reboot`、`poweroff` 或重启 SSH 服务时：

```python
result = console.execute(
    "reboot",
    expect_disconnect=True,
    timeout=60,
)
```

命令已发送后发生断连会视为成功。连接前失败、发送前断连和明确非零退出码仍然失败。

## 15. 脚本自行重试

框架不自动重试。需要时使用普通 Python：

```python
import time


result = None
for _ in range(3):
    result = host.execute("uname -a", timeout=30)
    if result.success:
        break
    time.sleep(5)

if result is None or not result.success:
    return result
```

对于已经发送后超时或断连的安装、升级、重启命令，不建议直接重发，以免重复执行。

## 16. 串行组合多个已注册脚本

```python
from autoenv import register_script

from scripts.start_mami import start_mami
from scripts.start_udk import start_udk


@register_script(
    name="start_full_env",
    description="依次启动 UDK 和 MAMI",
)
def start_full_env(ctx):
    result = start_udk()
    if not result.success:
        return result

    return start_mami()
```

这是普通串行函数调用，但每个子脚本仍会：

- 创建自己的运行目录和 `packages`。
- 使用自己的 last-run。
- 注册自己的 SSH/Telnet 对象。
- 在 `run` 模式下单独询问。
- 在 `rerun` 模式下单独无参数交互地复用参数。

不要把父脚本的 `ctx` 传给已注册子脚本。

## 17. `run` 与 `rerun`

普通运行：

```bash
autoenv run start_udk
```

- 上次参数作为默认值。
- 每项仍展示并可修改。
- 直接回车使用显示值。

快速重跑（不重新确认参数；若注册了 func，启动成功后仍显示 func 菜单）：

```bash
autoenv rerun start_udk
```

- 完全使用该脚本自己的上次参数。
- 不出现参数确认提示；启动后 func 菜单仍会显示。
- 没有历史参数时直接报错。

每次 `rerun` 仍会创建全新的运行目录和 `packages`，不会复用上次下载文件。

## 18. 完整注册示例

下面示例展示所有核心接口。真实项目可以删除不需要的部分。

```python
from autoenv import (
    CommandStatus,
    SSHDefaults,
    TelnetDefaults,
    extra_file,
    match,
    package,
    register_func,
    register_script,
)


@register_script(
    name="example_environment",
    description="AutoEnv 完整接口示例",
)
def example_environment(ctx):
    a1_package = package("A1")
    manual_bundle = extra_file("manual_bundle.tar.gz")
    driver_file = extra_file("driver.bin")
    firmware_image = match(r"^firmware-.*\.bin$")
    install_script = extra_file("install.sh")
    host_1260 = ctx.register_ssh_host(
        "example_host",
        defaults=SSHDefaults(
            host="192.168.1.100",
            port=22,
            username="root",
            password="root",
            connect_timeout=30.0,
        ),
    )

    console_1260 = ctx.register_telnet(
        "example_console",
        defaults=TelnetDefaults(
            host="192.168.1.200",
            port=23,
            timeout=30.0,
            shell_mode="auto",
        ),
    )

    # 显式下载完整包；不会自动解包。
    result = ctx.download_package(a1_package)
    if not result.success:
        return result

    # 从 A1 包中提取单个文件。
    result = ctx.extract_file_from(
        source=a1_package,
        target_file="driver.bin",
    )
    if not result.success:
        return result

    # 从用户手工放入 packages 的压缩包中提取目录。
    result = ctx.extract_file_from(
        source=manual_bundle,
        target_dir="firmware",
    )
    if not result.success:
        return result

    # package() 按 config 的 image_name 找本地包。
    result = host_1260.scp_upload(
        local_file=a1_package,
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    # extra_file() 使用明确文件名。
    result = host_1260.sftp_upload(
        local_file=driver_file,
        remote_dir="/root/autoEnv",
        overwrite=True,
    )
    if not result.success:
        return result

    # match() 按确定性顺序选择第一个匹配文件。
    result = host_1260.sftp_upload(
        local_file=firmware_image,
        remote_dir="/root/autoEnv/firmware",
        overwrite=False,
    )
    if not result.success:
        return result

    ctx.generate_sh_file(
        "install.sh",
        """#!/bin/sh
set -e
cd /root/autoEnv
chmod +x "S{A1}"
./"S{A1}"
""",
    )

    result = host_1260.sftp_upload(
        local_file=install_script,
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    result = host_1260.execute(
        "bash /root/autoEnv/S{install.sh}",
        timeout=600,
    )
    if not result.success:
        return result

    status = host_1260.execute("cat /tmp/env_status", timeout=30)
    if not status.success:
        return status
    if "READY" not in status.output:
        return status.with_failure("环境状态不是 READY")

    telnet_result = console_1260.execute(
        "source /root/start_slave.sh",
        timeout=300,
    )
    if telnet_result.status == CommandStatus.RESULT_UNKNOWN:
        if "READY" not in telnet_result.output:
            return telnet_result
    elif not telnet_result.success:
        return telnet_result

    # 示例：监控串口启动输出，并在时点出现时发送 Ctrl+B。
    reboot_result = console_1260.execute_on_output(
        "reboot",
        keyword="Press Ctrl+B",
        send_data=b"\x02",
        timeout=60,
    )
    if not reboot_result.success:
        return reboot_result

    @register_func(name="check_status", description="检查环境 READY 状态")
    def check_status(_func_ctx):
        result = host_1260.execute("cat /tmp/env_status", timeout=30)
        if result.success and "READY" not in result.output:
            return result.with_failure("环境状态不是 READY")
        return result

    return reboot_result
```

仓库中的 `scripts/example.py` 会提供可直接复制的对应模板。

## 19. 新环境提交前检查清单

- [ ] 脚本放在 `scripts/`，文件名和脚本名清晰。
- [ ] `@register_script` 名称唯一并填写描述。
- [ ] `package()` 名称在 `config.json` 中存在。
- [ ] `image_name` 正则能唯一匹配预期本地包。
- [ ] 下载、提取、上传分别显式调用。
- [ ] 手工文件使用 `extra_file()` 或 `match()`。
- [ ] 所有选择器和连接对象集中声明在环境函数开头，流程只复用变量。
- [ ] SSH Host 和 Telnet 分别注册。
- [ ] `S{file_name}` 使用选择器字符串，且对应文件已成功上传到命令目标。
- [ ] Telnet 使用占位符时已配置 `uploaded_files_from`，或上传来源唯一。
- [ ] `generate_sh_file()` 接收完整 shell 字符串，所有占位符先上传到同一 Host。
- [ ] 生成的 `.sh` 已声明为 `extra_file()`，并在生成后上传、执行。
- [ ] 每个运行期结果都检查 `success` 或明确检查 `status`。
- [ ] `TIMEOUT`/`DISCONNECTED` 操作不会被危险地自动重发。
- [ ] 主动断连命令使用 `expect_disconnect=True`。
- [ ] 按输出响应使用同一次 `execute_on_output()`，关键词、原始字节、回车形式和超时均已确认。
- [ ] 组合脚本直接调用已注册脚本，不传递 `ctx`。
- [ ] 可选 `register_func()` 位于主流程末尾，名称唯一，接收一个 `ctx` 参数。
- [ ] 子 func 复用主流程上下文和已注册对象，失败条件与返回结果明确。
- [ ] 不在脚本中直接写日志、调用 Paramiko 或访问绝对本地路径。
- [ ] `python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py scripts/<name>.py` 通过。
- [ ] `python -X utf8 -m pytest tests/test_generated_script_contract.py` 通过。
- [ ] 使用 `autoenv run <name>` 完成一次交互运行。
- [ ] 使用 `autoenv rerun <name>` 验证上次参数复用。

### 19.1 统一 UT 能检查什么

`tests/test_generated_script_contract.py` 对所有 `scripts/*.py` 调用同一个 AST 契约验证器，不执行环境函数。它离线检查：注册函数可解析、选择器和连接集中声明、流程复用变量、`generate_sh_file()` 使用完整字符串、占位符有声明、依赖文件先上传到同一目标、生成脚本随后上传，以及执行命令的 Host 与上传目标一致。

框架的其他 UT 使用 fake HDFS、SSH、SFTP/SCP 和 Telnet Socket，验证成功上传才注册实际文件名、失败上传不能替换、多 Host 隔离、Telnet 来源选择、文本保持与错误分支。它们不依赖真实服务器。

统一 UT 能验证 skill 生成的 Python 文件是否符合可静态判定的框架契约，但不能证明用户粘贴脚本的业务含义、远端路径、设备状态或交互询问质量正确。前者由该 UT 把关；后者仍需要生成前确认、skill eval/人工审阅和用户明确授权后的真实环境运行。

每个测试文件的 fake/mock 实现、每条 `register_func`、上传占位符及统一契约 UT 的存在目的和失败排查方法见 [`tests/README.md`](../tests/README.md)。失败时优先复制 pytest 输出的完整 node id 单独执行，不要先放宽断言。

## 20. 常见问题

### 为什么上传 `package("A1")` 不自动下载？

下载和上传故意分离。需要下载时先显式调用：

```python
ctx.download_package(package("A1"))
```

也可以由用户手工把符合 `A1.image_name` 的文件放进本次 `packages`。

### 为什么不能传绝对路径？

所有输入文件集中在本次 `packages`，日志才能完整记录该次运行实际使用了哪些文件，也能避免脚本意外访问任意 Windows 路径。

### `RESULT_UNKNOWN` 是否表示命令失败？

它表示框架没有证据确认成功，常见于非 POSIX Telnet。根据 `result.output` 做业务判断，不要直接把它当作退出码失败。

### 两条 SSH 命令为什么没有保留 `cd`？

每条 SSH 命令使用独立通道。把相关动作写在一条命令中：

```python
host.execute("cd /root/autoEnv && ./install.sh")
```

### 手工文件放到哪里？

脚本开始时终端会打印本次包目录。把文件放到该目录后需要重新执行脚本；文件缺失时当前操作会直接失败，不会原地等待。

## 21. 安全注意事项

AutoEnv 第一版面向可信实验室内网。注册和运行环境前请确认：

- HDFS 地址、SSH 地址和 Telnet 地址均属于可信网络；WebHDFS 默认兼容旧项目而不校验 TLS 证书。
- SSH 会自动接受未知主机密钥；不要在可能遭受中间人攻击的网络中使用。
- Telnet 全程明文传输，不要发送用于其他系统的敏感凭据。
- SSH 密码会明文保存在本机 `params.json` 和 `state/last_runs/`。请限制仓库目录访问权限，不要提交 `logs/` 或 `state/`。
- `.run` 提取会实际执行该文件，只能使用可信构建系统生成的 `.run` 包。
- `TIMEOUT` 或 `DISCONNECTED` 表示命令可能已经执行，除非业务上确认安全，否则不要直接重发。
