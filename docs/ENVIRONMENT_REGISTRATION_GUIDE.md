# AutoEnv 环境注册指南

本文面向第一次接触 AutoEnv 的使用者。照着“最小示例”即可注册一个环境；后续章节列出所有常用接口、可选参数和错误处理方式。

> 本文描述 `UNIFY_ENV` 新接口。旧版 `ENV/EnvironmentSpec` 接口不再使用。

## 1. 最快上手

### 1.1 创建脚本文件

在 `scripts/` 下创建 `start_demo.py`：

```python
from autoenv import SSHDefaults, package, register_script


@register_script(name="start_demo", description="下载并安装演示环境")
def start_demo(ctx):
    host = ctx.register_ssh_host(
        "demo_server",
        defaults=SSHDefaults(
            host="192.168.1.100",
            port=22,
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
    # 1. 注册连接对象
    # 2. 显式下载或检查本地包
    # 3. 按需提取
    # 4. 上传
    # 5. 执行命令并判断结果
    # 6. 返回最后一个结果，或正常返回 None
    ...
```

`name` 必须唯一，并用于：

- CLI 脚本名称。
- 运行目录名称。
- `state/last_runs/<name>.json`。
- 菜单显示和日志定位。

`description` 可省略，但建议填写一句能区分环境用途的说明。

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
- 在 `rerun` 模式下单独无交互复用参数。

不要把父脚本的 `ctx` 传给已注册子脚本。

## 17. `run` 与 `rerun`

普通运行：

```bash
autoenv run start_udk
```

- 上次参数作为默认值。
- 每项仍展示并可修改。
- 直接回车使用显示值。

快速重跑：

```bash
autoenv rerun start_udk
```

- 完全使用该脚本自己的上次参数。
- 不出现交互提示。
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
    register_script,
)


@register_script(
    name="example_environment",
    description="AutoEnv 完整接口示例",
)
def example_environment(ctx):
    host = ctx.register_ssh_host(
        "example_host",
        defaults=SSHDefaults(
            host="192.168.1.100",
            port=22,
            username="root",
            password="root",
            connect_timeout=30.0,
        ),
    )

    console = ctx.register_telnet(
        "example_console",
        defaults=TelnetDefaults(
            host="192.168.1.200",
            port=23,
            timeout=30.0,
            shell_mode="auto",
        ),
    )

    # 显式下载完整包；不会自动解包。
    result = ctx.download_package(package("A1"))
    if not result.success:
        return result

    # 从 A1 包中提取单个文件。
    result = ctx.extract_file_from(
        source=package("A1"),
        target_file="driver.bin",
    )
    if not result.success:
        return result

    # 从用户手工放入 packages 的压缩包中提取目录。
    result = ctx.extract_file_from(
        source=extra_file("manual_bundle.tar.gz"),
        target_dir="firmware",
    )
    if not result.success:
        return result

    # package() 按 config 的 image_name 找本地包。
    result = host.scp_upload(
        local_file=package("A1"),
        remote_dir="/root/autoEnv",
    )
    if not result.success:
        return result

    # extra_file() 使用明确文件名。
    result = host.sftp_upload(
        local_file=extra_file("driver.bin"),
        remote_dir="/root/autoEnv",
        overwrite=True,
    )
    if not result.success:
        return result

    # match() 按确定性顺序选择第一个匹配文件。
    result = host.sftp_upload(
        local_file=match(r"^firmware-.*\.bin$"),
        remote_dir="/root/autoEnv/firmware",
        overwrite=False,
    )
    if not result.success:
        return result

    result = host.execute(
        "bash /root/autoEnv/install.sh",
        timeout=600,
    )
    if not result.success:
        return result

    status = host.execute("cat /tmp/env_status", timeout=30)
    if not status.success:
        return status
    if "READY" not in status.output:
        return status.with_failure("环境状态不是 READY")

    telnet_result = console.execute(
        "source /root/start_slave.sh",
        timeout=300,
    )
    if telnet_result.status == CommandStatus.RESULT_UNKNOWN:
        if "READY" not in telnet_result.output:
            return telnet_result
    elif not telnet_result.success:
        return telnet_result

    # 示例：命令会主动导致连接断开。
    return console.execute(
        "reboot",
        timeout=60,
        expect_disconnect=True,
    )
```

仓库中的 `scripts/example.py` 会提供可直接复制的对应模板。

## 19. 新环境提交前检查清单

- [ ] 脚本放在 `scripts/`，文件名和脚本名清晰。
- [ ] `@register_script` 名称唯一并填写描述。
- [ ] `package()` 名称在 `config.json` 中存在。
- [ ] `image_name` 正则能唯一匹配预期本地包。
- [ ] 下载、提取、上传分别显式调用。
- [ ] 手工文件使用 `extra_file()` 或 `match()`。
- [ ] SSH Host 和 Telnet 分别注册。
- [ ] 每个运行期结果都检查 `success` 或明确检查 `status`。
- [ ] `TIMEOUT`/`DISCONNECTED` 操作不会被危险地自动重发。
- [ ] 主动断连命令使用 `expect_disconnect=True`。
- [ ] 组合脚本直接调用已注册脚本，不传递 `ctx`。
- [ ] 不在脚本中直接写日志、调用 Paramiko 或访问绝对本地路径。
- [ ] 使用 `autoenv run <name>` 完成一次交互运行。
- [ ] 使用 `autoenv rerun <name>` 验证上次参数复用。

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
