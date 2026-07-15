---
name: autoenv-script-generator
description: Interactively clarify, generate, modify, and verify AutoEnv UNIFY_ENV environment registration scripts under scripts/, including SSH or Telnet targets, HDFS package downloads, explicit extraction, SCP or SFTP uploads, command execution, result checks, retries, and serial combinations of registered scripts. Use when a user asks to add, register, create, adapt, or review an AutoEnv environment-startup script. Do not use for unrelated Python work or broad AutoEnv framework refactors.
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

- 操作类型：下载、提取、上传、SSH 命令、Telnet 命令或调用子脚本。
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

SSH 每次 `execute()` 使用独立通道。需要保留目录或环境变量时，把相关动作合并到同一条命令，例如 `cd ... && ...`。Telnet 会话可跨命令保留状态，但重连后不能依赖旧状态。

只有用户明确需要时才加入重试，并确认次数、间隔和可重试状态。对已经发送后发生 `TIMEOUT` 或 `DISCONNECTED` 的安装、升级、重启命令，默认不要自动重发。

### 3.6 组合脚本

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
6. 尚未解决的风险或框架能力边界。

请求一次明确确认。用户修改草案时继续对齐；确认前不要写业务脚本或包配置。

## 5. 生成脚本

用户确认后执行以下规则：

- 在 `scripts/<name>.py` 新建脚本，或对用户指定文件做最小修改。
- 仅从 `autoenv` 顶层导入当前需要的公共符号，保持导入最小化。
- 使用 `@register_script(name=..., description=...)`，并保证注册名唯一。
- 先注册连接对象，再按确认顺序执行操作。
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
3. 运行适合当前仓库的静态验证，至少包括 Python 编译检查。
4. 运行项目现有单元测试；若完整测试不可用，运行与注册、运行时和新脚本发现相关的最小测试。
5. 通过只导入/发现脚本的方式验证注册，不执行真实环境操作。
6. 不自动运行 `autoenv run <name>` 或 `rerun <name>`，因为它们可能下载文件、连接设备、上传内容并执行远端命令。只有用户明确要求实际运行并确认目标环境后才执行。

建议命令：

```bash
python -m compileall autoenv scripts
python -m pytest
python -c "from autoenv.registry import list_scripts; print([item.name for item in list_scripts()])"
```

按仓库实际入口调整命令，不为了通过测试篡改业务语义。

## 7. 交付结果

简要报告：

- 创建或修改了哪些脚本和包配置。
- 生成流程的关键顺序和成功条件。
- 执行了哪些静态检查和单元测试，结果如何。
- 哪些内容尚未在真实 SSH、Telnet 或 HDFS 环境中验证。
- 用户下一步可运行的 `autoenv run <name>` 命令；不要声称未执行的远端流程已经成功。
