# AutoEnv Web 架构与接手说明

本文面向下一位本地 Agent 或开发者。实现以当前代码和 UT 为准。

需要逐步了解页面按钮、`adapt_interface.py` 和环境参数绑定时，见 [`web_usage.md`](web_usage.md)。

> 当前入口是 `webPage/`；`frontend/` 仅保留为历史交互原型。Web 只在 Windows 本机开发场景验证，真实 SSH/SFTP/SCP/FTP/HDFS、设备串口和 Agent CLI 仍需在目标网络按明确授权验收。

## 1. 入口与数据流

- `startWeb.py` → `webPage/server.py`：本地 HTTP 服务，仅默认监听 `127.0.0.1:8765`。
- `adapt_interface.py` → `autoenv/interface.py` → `registry.run_script()`：结构化非交互启动。
- `environments/<name>.json`：环境档案；默认被 `.gitignore` 忽略，允许明文密码。
- `webPage/index.html/app.js/styles.css`：无构建链的本地前端。
- `autoenv/web_tools.py`：Tools 注册和发现契约。
- `webPage/tools/*.py`：独立工具模块。

Web 启动环境时先写临时 request JSON，再启动独立 Python 子进程调用 `adapt_interface.py`。设备操作不会阻塞 HTTP 线程，终止按钮可以结束任务进程。输出通过本地事件轮询送到控制台。

## 2. 结构化参数

`LaunchRequest` 包含 `script`、`mode`、`environments`、`parameters`；兼容入口仍接受单个 `environment`。`environments` 以脚本资源逻辑名为键，每项选择一个环境档案。启动时只取该档案中标签和协议均匹配的唯一连接，映射到脚本注册名。参数分区：

- `ssh_hosts`：映射 `register_ssh_host()` 的逻辑名；
- `telnet_connections`：映射现有串口/Telnet；
- `ftp_hosts`：映射独立普通 FTP；
- `packages`：空对象沿用 config link/base_link；`path_override` 指定 HDFS 路径；
- `arguments`：脚本通过 `ctx.argument()` 读取。

非交互模式缺少必填参数时立即失败，不回退到 `input()`。

`register_func` 仍是运行成功后的 CLI 循环菜单，Web 启动页没有对应的选择/输入控件。不要把依赖该菜单完成的流程当作 Web 可交互流程；`scripts/template.py` 将它拆成独立的 CLI 示例。

资源标签来自 `autoenv/resources.py` 的固定目录：`1260网口`、`1260串口`、`1712网口`、`1712串口`、`udie1网口`、`udie1串口`。环境保存接口拒绝未知标签、串/网协议错配和同环境重复标签。脚本通过 `register_script(resources=...)` 声明连接交互点，每项都包含内部 `name`、页面 `alias`、提示 `description`、固定 `label` 和 `protocol`；连接注册调用必须带同一 `resource_label`。HDFS 输入通过 `packages=({"name": ..., "alias": ..., "description": ...},)` 声明。`/api/scripts` 返回 `resources` 和 `package_inputs`，Web 选择脚本后立即渲染这些输入：每个连接点独立选择包含匹配标签的环境/IP，每个 HDFS 包独立填写链接。

环境页保存的 `baud_rate` 是网络串口的档案元数据；当前 Telnet 客户端连接 TCP 端口，不能直接修改串口服务器的物理波特率。若设备要求下发频率，应在对应环境脚本中用已确认的设备命令完成。

## 3. 文件传输

- `SSHHost.scp_download()`：SCP 传输，目录枚举和大小检查使用 SSH 命令。
- `SSHHost.sftp_download()`：SFTP 枚举、传输和大小检查。
- 两者的 `remote_file` 和 `pattern` 必须二选一。pattern 只搜索指定目录、不递归；零匹配和多匹配都失败，没有 newest 语义。
- 下载写入本次 `packages/`，先使用 `.part`，大小校验成功后原子替换。
- 成功的 `RemoteDownloadResult` 可直接传给 SCP/SFTP/FTP 上传。
- `FTPHost.upload()` 是独立普通 FTP，默认被动模式，以文件大小校验；FTP 不提供 SSH/SFTP 的通道安全性或标准 MD5 能力。

## 4. 解压与日志脚本

公共 `Extractor` 支持 `.run`、`.tar.gz`、`.tgz`、`.zip`，并拒绝路径穿越和 ZIP 符号链接。

`scripts/download_and_parse_logs.py` 的职责划分：

- 公共已有/新增：SSH 注册、SFTP 下载、远端正则唯一匹配、ZIP 安全能力、运行目录和日志。
- 脚本私有：递归寻找所有嵌套 ZIP、拒绝路径穿越/绝对路径/符号链接、为非 `.log` 文件创建 `.log` 副本、业务日志块解析。
- 待开发：`_parse_log_blocks()`。拿到样例后确认编码、开始 pattern、结束规则、重叠策略、上下文行和输出格式。当前只写 `LOG_PARSER_TODO.txt`，不猜测。

## 5. Web Tools 扩展

使用 `.agents/skills/autoenv-web-tool/SKILL.md`。新增工具只创建 `webPage/tools/<name>.py` 和 UT；核心 HTML/JS/CSS 不需要修改。工具模块导入时不得执行工作。

错误码工具尚未实现业务规则。现有 `tool-contract-preview` 只证明动态子页签和结构化输出可用。

## 6. Agent CLI 与文件导入

Agent 页签通过 `pywinpty` 在 Windows ConPTY 中启动设置里的 `codeagent`、`nga` 或 `cmd.exe`，并把设置的“启动目录”作为 ConPTY `cwd`。终端按原始块持续读取，保留回车覆盖、清屏、窗口标题序列过滤和常用 ANSI 光标移动语义；启动尺寸按终端画布计算，页面尺寸变化时同步调整 ConPTY。终端画布自身持有输入焦点，不存在独立消息框；键盘输入按顺序写回 ConPTY，支持方向键、翻页键、Home/End、Insert/Delete、F1-F12、Tab、Escape、Backspace 和 Ctrl 字母组合。

在终端上粘贴或拖入图片、`.py`、`.zip` 时，文件以随机前缀保存到设置目录，带引号的绝对路径直接写到当前 CLI 光标处，但不自动发送回车。普通剪贴板文本也直接写入 ConPTY。文件不会自动导入或执行；Python/ZIP 应由 `.agents/skills/import-python-web-tool/SKILL.md` 静态审查并适配。

页面仍使用仓库内轻量屏幕渲染器而非 xterm.js，因此数据通路和键盘交互等价于真实伪终端，但复杂颜色、鼠标模式、IME 和少见控制序列尚不能视为完整 Windows Terminal 复刻。

## 7. 安全边界

- Web 默认只允许本机访问且没有登录鉴权；不要改为 `0.0.0.0` 暴露到网络。
- 环境密码和 last-run 参数允许明文，相关目录不得提交。
- Tools 禁止任意 shell、网络和设备修改；设备流程使用 environment script。
- Agent 上传限制单请求 64 MiB、单文件解码后 48 MiB；ZIP skill 限制展开 128 MiB/2,000 文件。
- “打开文件夹”是明确按钮触发，不接受任意路径。

## 8. 验证命令

```powershell
python -X utf8 -m compileall autoenv scripts webPage
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py scripts/download_and_parse_logs.py
python -X utf8 .agents/skills/autoenv-web-tool/scripts/validate_tool.py webPage/tools/system_info.py
python -X utf8 C:/Users/admin/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/autoenv-web-tool
python -X utf8 C:/Users/admin/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/import-python-web-tool
python -X utf8 -m pytest
```

不要在离线 UT 中实际运行注册脚本，因为它们可能连接 SSH、Telnet、FTP 或 HDFS。

## 9. 已知限制与后续优先级

1. Agent CLI 已使用 ConPTY 并同步页面尺寸；页面尚未提供完整 xterm.js 颜色、鼠标和 IME 能力。
2. `download_and_parse_logs` 的日志块规则等待真实样例，当前只生成明确 TODO 文件。
3. 错误码工具仅有动态 Tool 契约示例，尚无业务规则。
4. 环境密码按已确认需求允许明文保存；若未来允许远程访问 Web，必须先增加鉴权、CSRF/来源限制、传输保护和密钥存储方案。
5. 离线 UT 和本机 HTTP 冒烟只能证明控制面契约；不能替代真实设备、文件服务器或 Agent CLI 验收。
