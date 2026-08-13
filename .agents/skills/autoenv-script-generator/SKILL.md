---
name: autoenv-script-generator
description: Interactively clarify, generate, modify, and verify AutoEnv UNIFY_ENV environment registration scripts under scripts/, including pasted shell conversion, file selectors, SSH or Telnet targets, downloads, uploads, commands, post-start register_func flows, checks, retries, and serial combinations. Use when a user asks to add, register, create, adapt, or review an AutoEnv environment-startup script. Do not use for unrelated Python work or broad framework refactors.
---

# AutoEnv 脚本交互生成

通过逐步对齐把环境启动过程转换成可运行的 `scripts/*.py`。先理解需求并获得确认，再编辑文件；不要根据模糊描述猜测地址、包来源、命令顺序或成功条件。

## 1. 建立仓库事实基线

1. 确认当前目录属于 AutoEnv 仓库，并存在 `autoenv/`、`scripts/` 和 `config.json`。
2. 先读取以下文件：
   - `docs/ENVIRONMENT_REGISTRATION_GUIDE.md`
   - `scripts/example.py`
   - `autoenv/__init__.py`
3. 按需读取实现、测试和已有业务脚本。若文档、示例与实现冲突，以当前分支实现和测试为准，并把差异告诉用户。
4. 检查现有注册名、Python 函数名、连接对象名和 `config.json` 包配置，避免冲突。
5. 只使用当前代码实际导出的公共 API；不要重用旧版 `ENV`、`EnvironmentSpec` 或其他已废弃接口。

## 2. 选择生成目标

先判断用户要做哪一种工作：

- 新建独立环境脚本。
- 修改已有环境脚本。
- 新建组合脚本，串行调用已有注册脚本。
- 先梳理需求，暂不写文件。

若用户没有说清，先询问这一项。已有答案直接复用，不重复提问。

## 3. 逐项交互对齐

优先使用当前 agent 的原生问答控件；没有时使用普通文本。一次只问一个会改变实现的问题，提供少量可选项和自由输入入口。简单问题可合并，但不要一次抛出长问卷。

持续维护一份“脚本草案”，至少覆盖以下信息。只询问与当前脚本有关的项目。

### 3.1 身份与边界

- 脚本注册名、文件名和一句描述。
- 新建还是修改；独立脚本还是组合脚本。
- 环境启动的总体目标和最终成功标志。
- 哪些步骤由 AutoEnv 执行，哪些仍由人手工执行。

优先建议合法的 `snake_case` 注册名和函数名。注册名必须唯一；文件放在 `scripts/`。

### 3.2 按真实顺序梳理操作

让用户按实际先后描述启动流程，然后逐步补齐，不要先套固定模板。对每一步确认：

- 操作类型：下载、提取、上传、SSH 命令、Telnet 命令、按输出关键词响应或调用子脚本。
- 操作对象：哪个包、哪个 SSH Host、哪个 Telnet 连接或哪个子脚本。
- 前置条件和失败后是否立即停止。
- 成功依据：框架结果、退出码、输出文本、预期断连或其他业务状态。

最终脚本顺序必须与确认后的流程顺序一致，不引入 DAG、Step、并发或隐式依赖。

### 3.3 连接对象

对每个 SSH Host 询问并确认：

- 逻辑名称。
- 默认 `host`、`port`、`username`、`password` 和 `connect_timeout`。
- 需要执行的命令和上传操作。

对每个 Telnet 连接询问并确认：

- 逻辑名称。
- 默认 `host`、`port`、`timeout` 和 `shell_mode`。
- Shell 是 POSIX、仅提示符模式，还是不确定。通常不确定时使用 `auto`；RTOS、U-Boot 或自定义 CLI 不要仅凭提示符外观推断系统类型。
- 是否存在 `login:` 或密码登录流程。当前框架不处理 Telnet 登录提示；若需要，明确报告能力边界，不伪造支持。
- 命令中若使用上传文件占位符，确认对应的 SSH Host 名，并设置 `uploaded_files_from="SSH注册名"`；不把 SSH 连接参数复制给 Telnet。

不要擅自生成真实密码。用户未提供时使用空值或明确占位默认值，并说明运行时会交互确认。

### 3.4 文件与包来源

对每个本地输入明确选择器：

- `package("NAME")`：按 `config.json` 的 `image_name` 匹配当前运行的 `packages`。
- `extra_file("filename")`：使用用户手工放入当前 `packages` 的明确文件。
- `match(r"pattern")`：按正则匹配当前 `packages` 根目录。

若使用 `package()`：

1. 检查名称是否已存在于 `config.json`。
2. 确认本次是否要显式调用 `ctx.download_package()`。
3. 若配置不存在，分别询问 `name`、`link`/`base_link` 和 `image_name`，展示拟新增配置，并在用户确认后才修改 `config.json`。
4. 不利用旧 `target_file` 字段隐式提取。

若要提取，确认源选择器、`target_file` 或 `target_dir`，二者必须且只能选择一个。若要上传，确认协议、选择器、远端目录和 `overwrite`。

不要把裸字符串、绝对本地路径或包含 `..` 的路径传给上传和提取接口。不要让上传或提取隐式触发下载。

### 3.5 命令、判定与重试

对每条命令确认：

- 命令文本、目标对象和超时。
- 是否会主动重启、关机或导致连接断开；若会，确认使用 `expect_disconnect=True`。
- 是否只需 `result.success`，还是还需检查 `result.output` 中的业务标志。
- Telnet `RESULT_UNKNOWN` 时，什么输出可以证明业务成功。

若需要在命令输出出现特定内容时立即输入，再确认：

- 初始命令、大小写敏感的输出关键词、总超时。
- 响应是控制字符还是普通命令；必须落实为确切 `bytes`，例如 Ctrl+B 是 `b"\x02"`，不是文本 `b"ctrl+b"`。
- 是否需要回车以及实际换行形式；`send_data` 不会自动追加 `\r`、`\n` 或 `\r\n`。
- 命中并发送是否足以视为成功。当前 `execute_on_output()` 不验证设备已经进入目标模式，也不自动重发。

初始命令、等待和响应必须放在同一次 `execute_on_output()` 调用中，不能先用阻塞的 `execute()` 发 reboot、再单独等待。普通 SSH reboot 后无法继续读取固件启动输出；这类流程通常使用串口映射的 Telnet 连接。

SSH 每次 `execute()` 使用独立通道。需要保留目录或环境变量时，把相关动作合并到同一条命令，例如 `cd ... && ...`。Telnet 会话可跨命令保留状态，但重连后不能依赖旧状态。

只有用户明确需要时才加入重试，并确认次数、间隔和可重试状态。对已经发送后发生 `TIMEOUT` 或 `DISCONNECTED` 的安装、升级、重启命令，默认不要自动重发。

### 3.6 把已有 shell 脚本转换为环境脚本

用户粘贴已有脚本时，把整段文本作为一个输入处理，不逐条改写成 Python 命令。按以下顺序引导：

1. 原样保留 shebang、注释、换行、引号、变量、重定向和命令布局；不要自动增加 `set -e`。
2. 找出脚本中所有会随构建变化、需要由 AutoEnv 准备的文件名。普通系统路径、固定配置名和脚本自身变量不自动变成占位符。
3. 为每个待替换文件展示映射：原脚本文本、`S{选择器字符串}`、Python 变量名、`package`/`extra_file`/`match` 类型、下载或手工来源、上传 SSH Host 和远端目录。
4. 确认输出 `.sh` 文件名，并在声明区增加对应的 `extra_file("name.sh")`。
5. 执行顺序固定为：集中声明 → 下载/提取 → 上传脚本依赖文件 → `ctx.generate_sh_file()` → 上传生成的 `.sh` → 执行。
6. `S{...}` 只替换文件名；原脚本中的目录仍保留。生成前，脚本内所有占位符必须已经成功上传到同一个 SSH Host。
7. 若最终通过 Telnet 执行包含占位符的命令，确认并设置 `uploaded_files_from`。

映射和完整脚本文本都属于编辑前确认稿；文件来源、上传目标或成功条件不明确时继续询问，不猜测。

### 3.7 启动后固定 func

询问环境成功后是否需要可重复选择的检查或维护流程。对每个 func 确认：

- 唯一名称和一句描述。
- 真实操作顺序、复用的 Host/Telnet/选择器、超时和成功条件。
- 失败结果是否已有明确的 `with_failure()` 业务原因。

生成时在所有主流程操作之后定义嵌套 `@register_func`。func 必须接收一个 `ctx` 参数，使用与主流程相同的 `RunContext`；优先闭包复用开头声明的连接和选择器，不重复注册同名对象。主流程失败时不会出现菜单；func 失败会记录并返回菜单，选择 `0` 才退出并关闭连接。

不要把 `register_func` 放在模块顶层，也不要把固定 func 写成独立 `@register_script`。若组合脚本调用带 func 的子脚本，明确告诉用户：必须先退出该子脚本的 func 菜单，组合流程才继续。

### 3.8 组合脚本

确认子脚本列表、严格顺序以及失败是否立即停止。检查每个子脚本已经注册且可导入。

组合脚本直接调用装饰后的函数：

```python
result = start_first()
if not result.success:
    return result
return start_second()
```

不要向子脚本传父脚本的 `ctx`，不要共享 Host、Telnet、运行目录或 `packages`，不要创建组合专用注册表。

## 4. 编辑前确认

信息足够后，向用户展示紧凑的最终草案：

1. 将新增或修改的文件。
2. 注册名和描述。
3. SSH/Telnet 对象及默认值；密码必须脱敏。
4. 按顺序编号的操作，每步标明输入、目标、超时、覆盖策略和成功条件。
5. `config.json` 的拟议变更。
6. 已有 shell 脚本的文件映射、输出 `.sh` 名称及“原文只替换占位符”的约束。
7. 启动后 func 的名称、描述、复用对象、步骤和成功条件。
8. 尚未解决的风险或框架能力边界。

请求一次明确确认。用户修改草案时继续对齐；确认前不要写业务脚本或包配置。

## 5. 生成脚本

用户确认后执行以下规则：

- 在 `scripts/<name>.py` 新建脚本，或对用户指定文件做最小修改。
- 仅从 `autoenv` 顶层导入当前需要的公共符号，保持导入最小化。
- 使用 `@register_script(name=..., description=...)`，并保证注册名唯一。
- 在环境函数开头集中声明所有 `package()`、`extra_file()`、`match()` 文件选择器以及 SSH/Telnet 连接对象；变量名应体现环境或文件用途。
- 后续下载、提取、上传和命令步骤只复用开头声明的变量，不要在各步骤中重复构造相同选择器。
- 按输出响应使用 `target.execute_on_output(command, keyword=..., send_data=..., timeout=...)`；`send_data` 必须是明确的 bytes 字面量，控制字符和回车形式必须与用户确认结果一致。
- 声明完成后再按确认顺序执行操作。
- `ctx.generate_sh_file(file_name, script)` 的 `script` 是一整段 shell 文本。保留用户粘贴内容的 shebang、换行和命令布局，仅替换其中已成功上传文件对应的 `S{file_name}`；不要拆成命令列表，也不要自动添加 `set -e`。
- `file_name` 必须是无目录的 `.sh` 文件名，并在声明区有对应 `extra_file()`；生成文件后再上传。
- `S{file_name}` 使用选择器构造函数的字符串参数。生成前所有占位符必须上传到同一 SSH Host；SSH 命令必须上传到当前 Host，Telnet 通过 `uploaded_files_from` 绑定来源。
- 若需要启动后 func，从 `autoenv` 导入 `register_func`，在主流程成功路径的末尾定义嵌套 func；每个 func 接收同一个 `ctx`，复用集中声明的对象，并返回合法 AutoEnv 结果或 `None`。
- 每个运行期操作后立即处理结果；通常使用：

```python
result = operation(...)
if not result.success:
    return result
```

- 业务输出不满足条件时使用 `result.with_failure("明确原因")`。
- 只返回 `None` 或 AutoEnv 结果对象；不要返回 `True`/`False`。
- 保留明确请求的覆盖策略和超时，不自行改变危险命令语义。
- 不直接使用 Paramiko、socket、WebHDFS 请求、文件日志或任意本地绝对路径。
- 仅为不明显的业务判断添加短注释，不复制整份注册指南。
- 若新增包配置，保持 `config.json` 为合法顶层数组并遵循已有字段风格。

## 6. 静态检查与测试

完成编辑后：

1. 重新读取生成文件，核对操作顺序与用户确认稿完全一致。
2. 检查所有 `package()` 名称都存在，所有注册名都唯一，所有导入都可解析。
3. 先运行统一环境脚本契约验证器，再运行 Python 编译检查。契约检查负责集中声明、选择器复用、完整 shell 字符串、上传/生成/执行顺序、目标一致性，以及 `register_func` 的嵌套位置、参数和唯一名称。
4. 运行项目现有单元测试；若完整测试不可用，运行与注册、运行时和新脚本发现相关的最小测试。
5. 通过只导入/发现脚本的方式验证注册，不执行真实环境操作。
6. 不自动运行 `autoenv run <name>` 或 `rerun <name>`，因为它们可能下载文件、连接设备、上传内容并执行远端命令。只有用户明确要求实际运行并确认目标环境后才执行。

UT 失败时先读取 `tests/README.md` 中对应测试的实现、目标和排查入口，使用 pytest 输出的完整 node id 单独重跑；不要为了消除失败直接放宽契约断言。

建议命令：

```bash
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py scripts/<name>.py
python -X utf8 -m compileall autoenv scripts
python -X utf8 -m pytest tests/test_generated_script_contract.py
python -X utf8 -m pytest
python -X utf8 -c "from autoenv.registry import list_scripts; print([item.name for item in list_scripts()])"
```

按仓库实际入口调整命令，不为了通过测试篡改业务语义。

## 7. 交付结果

简要报告：

- 创建或修改了哪些脚本和包配置。
- 生成流程的关键顺序和成功条件。
- 执行了哪些静态检查和单元测试，结果如何。
- 哪些内容尚未在真实 SSH、Telnet 或 HDFS 环境中验证。
- 用户下一步可运行的 `autoenv run <name>` 命令；不要声称未执行的远端流程已经成功。
# Current branch additions

- For SCP/SFTP remote downloads, declare the SSH host once and call `host.scp_download()` or `host.sftp_download()`. Pass exactly one of `remote_file` and `pattern`. A pattern searches only the named directory and must match exactly one file; never apply HDFS newest semantics.
- Reuse a successful `RemoteDownloadResult` directly as the source of `scp_upload()`, `sftp_upload()`, or FTP `upload()` so the actual downloaded basename and operation log remain connected.
- Register plain FTP independently with `ctx.register_ftp_host()` and `FTPDefaults`; FTP does not reuse SSH credentials unless the script deliberately gives the same defaults.
- Declare Web-facing HDFS inputs with `packages=(...)` and script inputs with `parameters=(...)` on `register_script()`. Read script inputs with `ctx.argument()`.
- Keep recursive nested-ZIP expansion and business log-block parsing script-specific until a second confirmed use case justifies a public API.
- Validate generated scripts offline. Do not run SCP/SFTP downloads, FTP uploads, or registered scripts against real targets without explicit user authorization.
