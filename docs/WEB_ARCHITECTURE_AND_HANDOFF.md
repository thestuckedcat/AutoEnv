# AutoEnv Web 架构与接手说明

本文面向下一位本地 Agent 或开发者。实现以当前代码和 UT 为准。

需要逐步了解页面按钮、`adapt_interface.py` 和环境参数绑定时，见 [`web_usage.md`](web_usage.md)。

> Web 只有 `startWeb.py` 一个启动入口和 `webPage/` 一套实现。旧原型和兼容启动器不保留；需要回退时使用 Git commit。Web 只在 Windows 本机开发场景验证，真实 SSH/SFTP/SCP/FTP/HDFS、设备串口和 Agent CLI 仍需在目标网络按明确授权验收。

## 1. 入口与数据流

- `startWeb.py` → `webPage/server.py`：唯一 Web 启动链，固定监听 `127.0.0.1:8765`。不接受 host/port 参数，不自动选择其他端口，绑定冲突直接失败；`webPage/server.py` 不能作为模块直接启动。
- `adapt_interface.py` → `autoenv/interface.py` → `registry.run_script()`：结构化非交互启动。
- `adapt_tool_interface.py` → `autoenv/web_tools.py`：workflow Tool 的结构化子进程入口。
- `environments/<name>.json`：环境档案；默认被 `.gitignore` 忽略，允许明文密码。
- `webPage/index.html/app.js/styles.css`：无构建链的本地前端。
- `autoenv/web_tools.py`：Tools 注册和发现契约。
- `webPage/tools/*.py`：独立工具模块。

Web 启动环境时先写临时 request JSON，再启动独立 Python 子进程调用 `adapt_interface.py`。workflow Tool 使用独立的 `adapt_tool_interface.py` 进程和事件流，因此与环境启动可以分别停止。设备操作不会阻塞 HTTP 线程，输出通过本地事件轮询送到控制台。

## 2. 结构化参数

`LaunchRequest` 包含 `script`、`mode`、`environments`、`parameters`；兼容入口仍接受单个 `environment`。`environments` 以脚本资源逻辑名为键，每项选择一个环境档案。启动时只取该档案中标签和协议均匹配的唯一连接，映射到脚本注册名。参数分区：

- `ssh_hosts`：映射 `register_ssh_host()` 的逻辑名；
- `telnet_connections`：映射现有串口/Telnet；
- `ftp_hosts`：映射独立普通 FTP；
- `packages`：空对象沿用 config link/base_link；`path_override` 指定 HDFS 路径；
- `arguments`：脚本通过 `ctx.argument()` 读取。

非交互模式缺少必填参数时立即失败，不回退到 `input()`。

`register_func` 仍是运行成功后的 CLI 循环菜单，Web 启动页没有对应的选择/输入控件。不要把依赖该菜单完成的流程当作 Web 可交互流程；`scripts/template.py` 将它拆成独立的 CLI 示例。

资源标签的唯一数据源是 `autoenv/resource_labels.json`，`autoenv/resources.py` 负责加载并校验目录，`/api/resource-labels` 把同一份数据返回 Web。环境保存接口拒绝未知标签、串/网协议错配和同环境重复标签。Web 初始化时先加载标签目录再创建环境资源行；目录加载完成后也会刷新已有下拉框，避免异步初始化留下空选项。注册器在导入脚本时静态解析 `register_ssh_host()`、`register_telnet()`、`register_ftp_host()` 和 `package()` 调用，不执行环境函数；连接协议由调用函数确定，标签取 `resource_label`，包名取 `package(name)`。两类调用都可携带页面 `alias` 和提示 `description`，省略时分别默认使用 `name` 和空字符串。`/api/scripts` 返回自动形成的 `resources` 和 `package_inputs`，Web 选择脚本后立即渲染这些输入：每个连接点独立选择包含匹配标签的环境/IP，每个 HDFS 包独立填写链接。包输入在启动请求中写入 `parameters.packages`；留空继续使用 `config.json` 的 `link/base_link`，填写时作为 `path_override`。

环境页保存的 `baud_rate` 是网络串口的档案元数据；当前 Telnet 客户端连接 TCP 端口，不能直接修改串口服务器的物理波特率。若设备要求下发频率，应在对应环境脚本中用已确认的设备命令完成。

## 3. 文件传输

- `SSHHost.scp_download()`：SCP 传输，目录枚举和大小检查使用 SSH 命令。
- `SSHHost.sftp_download()`：SFTP 枚举、传输和大小检查。
- `SSHHost.scp_download_many()`：在指定远端目录中按 basename glob 枚举全部普通文件，读取远端 mtime，按 mtime/文件名稳定排序后逐个 SCP 到本次运行目录；任一传输失败会清除本批已完成文件和 `.part`。远端枚举兼容 BusyBox ash，不使用 GNU `find -printf`，而用目录 glob、`test`、`wc -c`、`stat -c %Y` 和 NUL 分隔的 `printf`。
- 两者的 `remote_file` 和 `pattern` 必须二选一。pattern 只搜索指定目录、不递归；零匹配和多匹配都失败，没有 newest 语义。
- 下载写入本次 `packages/`，先使用 `.part`，大小校验成功后原子替换。
- 成功的 `RemoteDownloadResult` 可直接传给 SCP/SFTP/FTP 上传。
- `FTPHost.upload()` 是独立普通 FTP，默认被动模式，以文件大小校验；FTP 不提供 SSH/SFTP 的通道安全性或标准 MD5 能力。

## 4. 解压与日志 SDK

公共 `Extractor` 支持 `.run`、`.tar.gz`、`.tgz`、`.zip`，并拒绝路径穿越和 ZIP 符号链接。

`RunContext.create_log_collection()` 创建当前运行唯一的日志批次目录。单目录处理链为 `download()` → `extract_all()` → `group()` → 一个或多个 `match_line()`/`match_block()` → `finalize()`；多目录把首步换成 `download_many(remote_dirs=[...])`：

- `download_many()` 将每个远端目录下载到 `raw/source-NNN/`，允许不同目录存在同名文件；任一路径失败会清理此前已下载文件并使整个批次失败。目录列表和逐目录状态写入 manifest。
- `extract_all()` 递归处理 ZIP、GZ、TAR.GZ 和 TGZ；拒绝路径穿越、绝对路径、链接、特殊文件、同名/父子路径冲突，并限制总文件数与展开大小。失败的展开目录会清理。
- `group()` 递归匹配 basename glob，按继承的远端 mtime、相对路径稳定排序。每个文件独立尝试 UTF-8、UTF-8-SIG、GB18030、Latin-1，并在 manifest 记录实际编码。
- `TimestampPattern` 使用 `year/month/day/hour/minute/second` 命名组；`hour` 和 `minute` 必须存在，日期与秒可缺失。
- `match_line()` 扫描全部原始行更新时间，匹配行继承本文件上一时间；文件之间不继承。
- `match_block()` 排除 begin/end 行，支持文件头和 end 后的隐式块、重复 begin、连续 end 与显式块 EOF 输出；同一块统一时间。未取得时间时统一输出 `[-]`。
- `finalize()` 生成 `targets/*.log`、逐行 SQLite 索引和不含密码的 `manifest.json`。只有 manifest 状态为 `ready` 的批次可查询。

旧的 `scripts/download_and_parse_logs.py` 已删除；日志采集是 Tool workflow，不是环境启动脚本。

## 5. Web Tools 扩展

使用 `.agents/skills/autoenv-web-tool/SKILL.md`。Tools 与 scripts 使用独立注册表；`list_scripts()` 永远不会发现 Tool。工具模块导入时不得执行工作。

`webPage/tools/_template.py` 是 local Tool 的可复制模板；下划线文件被发现器跳过。复制成非下划线文件并替换全部占位符后，该文件成为自动发现的正式 Tool。workflow Tool 使用 Skill 内的 `scaffold_tool.py --kind workflow` 生成。

- `kind="local"` 保持原契约：接收 values 字典，在 HTTP 进程运行并返回 JSON。
- `kind="workflow"` 接收 `RunContext`，静态发现资源声明，通过环境标签绑定后在独立子进程运行，支持启动、事件轮询、停止和统一 `ScriptResult`。
- `renderer="log_collection"` 使用日志批次、目标列表和分页查询 API。页面支持多日志窗口、每窗独立的全文关键词 Find/命中高亮、中心时间窗、跨午夜查询和五分钟关联高亮。

错误码工具尚未实现业务规则。现有 `tool-contract-preview` 只证明动态子页签和结构化输出可用。

## 6. Agent CLI 与文件导入

Agent 页签通过 `pywinpty` 在 Windows ConPTY 中始终先启动本地 `cmd.exe /d`，并把设置的“启动目录”作为 ConPTY `cwd`。设置里的 Agent 命令不是待直接创建的可执行文件，而是在 cmd 启动后自动写入的字符和回车；因此内置命令、带参数命令和不存在的命令都由 cmd 在同一终端中回显，留空则只打开 cmd。终端按原始块持续读取，保留回车覆盖、清屏、窗口标题序列过滤和常用 ANSI 光标移动语义；启动尺寸按终端画布计算，页面尺寸变化时同步调整 ConPTY。终端画布自身持有输入焦点，不存在独立消息框；键盘输入按顺序写回 ConPTY，支持方向键、翻页键、Home/End、Insert/Delete、F1-F12、Tab、Escape、Backspace 和 Ctrl 字母组合。

在终端上粘贴或拖入图片、`.py`、`.zip` 时，文件以随机前缀保存到设置目录，带引号的绝对路径直接写到当前 CLI 光标处，但不自动发送回车。普通剪贴板文本也直接写入 ConPTY。文件不会自动导入或执行；Python/ZIP 应由 `.agents/skills/import-python-web-tool/SKILL.md` 静态审查并适配。

页面仍使用仓库内轻量屏幕渲染器而非 xterm.js，因此数据通路和键盘交互等价于真实伪终端，但复杂颜色、鼠标模式、IME 和少见控制序列尚不能视为完整 Windows Terminal 复刻。

## 7. 安全边界

- Web 固定只允许本机访问且没有登录鉴权；不要增加 `0.0.0.0`、动态 host/port 或备用启动入口。
- 环境密码和 last-run 参数允许明文，相关目录不得提交。
- local Tools 禁止任意 shell、网络和设备修改。workflow Tools 只能通过 `RunContext`/AutoEnv SDK 使用页面绑定的声明资源；禁止直接 Paramiko/socket、任意 shell、动态代码和在结果/manifest 中保存密码。
- Agent 上传限制单请求 64 MiB、单文件解码后 48 MiB；ZIP skill 限制展开 128 MiB/2,000 文件。
- “打开文件夹”是明确按钮触发，不接受任意路径。

## 8. 验证命令

```powershell
python -X utf8 -m compileall autoenv scripts webPage
python -X utf8 .agents/skills/autoenv-web-tool/scripts/validate_tool.py webPage/tools/system_info.py
python -X utf8 .agents/skills/autoenv-web-tool/scripts/validate_tool.py webPage/tools/log_collection.py
python -X utf8 .agents/skills/autoenv-web-tool/scripts/validate_tool.py webPage/tools/_template.py
python -X utf8 C:/Users/admin/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/autoenv-web-tool
python -X utf8 C:/Users/admin/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/import-python-web-tool
python -X utf8 -m pytest
```

不要在离线 UT 中实际运行注册脚本，因为它们可能连接 SSH、Telnet、FTP 或 HDFS。

## 9. 已知限制与后续优先级

1. Agent CLI 已使用 ConPTY 并同步页面尺寸；页面尚未提供完整 xterm.js 颜色、鼠标和 IME 能力。
2. 日志 Tool 当前固定使用已确认的 `cpdt_*`/`cpdt*.log`、AUTH line 和 DB block 规则；新增组件规则时直接修改 Tool Python 并增加样例精确断言。
3. 错误码工具仅有动态 Tool 契约示例，尚无业务规则。
4. 环境密码按已确认需求允许明文保存；若未来允许远程访问 Web，必须先增加鉴权、CSRF/来源限制、传输保护和密钥存储方案。
5. 离线 UT 和本机 HTTP 冒烟只能证明控制面契约；不能替代真实设备、文件服务器或 Agent CLI 验收。日志 SCP 仍需在一个明确授权且含 `1260网口` 的实验环境单独冒烟。
