# Web 快速开始

在项目根目录执行：

```powershell
python -X utf8 startWeb.py
```

打开 <http://127.0.0.1:8765/>。启动入口和地址固定；端口被占用时明确报错。

本页说明源码仓库 Web 的使用步骤。安装、依赖快照及其他入口见 [文档地图](DOCUMENTATION_MAP.md)。

1. 在“环境注册”中填写一级组网、二级连接名称、类型、地址与账号，保存连接。SSH 的文件传输方式在这里选择 `scp` 或 `sftp`；FTP 可选择主动或被动模式。保存配置不连接设备，脚本申请对象时才连接并自检。
2. 将普通 Python 流程放入 `scripts/`，流程先调用 `init_script()`，随后直接申请 `get_remote_info(type, allowed_envs)`。不使用 `@register_script`。刷新脚本列表。
3. 在“运行脚本”选择脚本和一级组网，再分别绑定每个对象的二级连接。候选受脚本白名单和连接类型限制。填写需要的 HDFS 路径与脚本参数。
4. 点击“运行脚本”。页面展示实际 CLI 命令和每条操作的来源、目标、结果；完成后给出完整日志路径。可复制同一条命令在终端运行。

新脚本的接口与示例见 [快速开始](QUICK_START.md) 和 [重构计划](REFACTOR_PLAN_FOUR_API_CATEGORIES.md)。

产品日志使用主页“产品日志”链接或 <http://127.0.0.1:8765/product-logs/>。拖入压缩包、选择上传文件夹或填入服务端绝对路径，网页按 `product` 规则解析。diag、var 可分别填部分路径消歧，例如 `diag/slot62`、`var`；默认精确匹配这两个目录名，多命中报错。diag 日志通配符留空即可收集历史/current，内核始终收集全部 dmesg 轮转文件。命令行另提供 `auto/product/generic` 收集选择；auto 的回退及 generic 任意目录规则见 [产品日志说明](../product_log/README.md)。

diag 中 `diaglog_*.log` 按文件名字典序读取，最新 `diag.log` 最后；var 递归展开多层压缩包，`cpdt_dmesg_current.log.<数字>` 数字升序读取，无后缀 current 最后。任务完成后可选“驱动日志 / 固件日志 / 统一时间”，驱动再选“全部 / 用户态 / 内核态”。所有实际出现的用户态 Slot/Cpu/ModuleName 及固件 Slot/Cpu/Dev/类型均可筛选；只有 `udk` 及其空格后缀名称归一成 `udk`，`udk2` 保持独立。固件类型显示 bios/IMP/IMU/HSM，其余显示数字。

页面“模块对应表”预置 `udk→udk→udk`、`UBE→UBE→mami`，其他用户态名称自动补入，内核关键词待填写。保存后即时查询，无需重新解压；配置随批次保留。未找到指定用户态名称显示“不存在”，仍可读取对应内核记录。内核关键词按整条原文、不区分大小写的字面子串匹配，业务模块下拉框使用表中映射，不需要重复输入内核关键词。

统一时间视图按日期升序显示三类记录；Slot/Cpu 仅约束用户态与固件，内核设备显示未知并保留来源路径；Dev/类型只约束固件。内核无日期行全部标为“时间未知”，绝不继承前一行，排在有效日期之后；有日期范围时不把未知行作为命中。驱动可切简洁/原本模式，固件保留读取日期并可切时间/拼接视图。页面显示工作区绝对路径，支持导出和来源定位；旧版本批次会提示重新导入。详见 [产品日志说明](../product_log/README.md)。

产品日志页面“任务数据”显示占用、阈值和最近自动清理结果；点击“检查占用”重新检查并执行到阈值后的清理。Web 上传与历史批次达到 3 GiB 后自动优先清理最旧的可回收内容，活动任务、正在查看和最新结果受到保护。它不会清理你填写的外部输入目录、独立 CLI 输出或普通脚本日志；3 GiB 是触发阈值，不是硬配额。清理范围、时机及批次保留规则以 [产品日志说明](../product_log/README.md) 为准。需要长期保留的原始日志应放在 Web 管理根之外。

## 单文件离线日志页

在源码根目录执行 `python -X utf8 tools/build_product_log_page.py`，得到 `dist/product-log-offline.html`。将这个文件复制到使用者电脑，在现代桌面浏览器打开，选择压缩包或文件夹，填写 diag/var 部分路径后解析。HTML 包含全部资源，使用阶段无需 Python、Node、网络或服务器；构建脚本只使用 Python 标准库。

页面保留分类/统一时间、设备筛选、模块对应表、搜索、来源定位和筛选结果导出。模块表可下载为 JSON，在下次解析后导入并应用。日志与表格只保存在当前页面内存，关闭或刷新后需重新选择输入；显示的是相对来源路径，复制/重命名只改变内存副本。该页没有服务端绝对路径输入、磁盘解压目录、历史批次或 3 GiB 清理。完整用法及实际验收边界见 [独立离线页说明](../product_log/standalone/README.md)。

## 输出与日志

| 位置 | 内容 |
| --- | --- |
| 页面“操作结果” / `output.log` | 操作编号、脚本文件与行号、动作、成功/失败/未知状态、已取得的退出码 |
| `run.log` | 流程来源、完整命令、执行结果、远端原始输出、脚本 `print()`、错误与 traceback |
| 普通 CLI stdout | 运行完成后的绝对 `run.log` 路径，仅一行 |

页面同步读取 `output.log`，不会把完整远端输出混入简洁结果。`print()` 进入 `run.log`。点击“打开日志”或“打开运行目录”定位本次文件。

退出码为 `0`（流程成功）、`1`（运行失败）、`2`（参数或准备失败）。准备失败同样提供已创建的日志；若日志目录本身不能创建，显示 CLI 的简短错误。

## 配置和脚本参数

环境保存在 `environments/topologies/<组网>/connections.json`，文件内容是连接数组。一个连接名称对应一个对象；同组网同名保存会更新原配置。可停用连接而保留配置。账号密码保存在本机 JSON。

每个脚本的环境对象必须在 `main()` 开始部分无条件直接赋值，例如：

```python
from autoenv import init_script, get_remote_info

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("单机验证", "主机-ssh")])
    print(dut.run("echo READY"))
```

页面静态读取这些申请，不导入或运行脚本。若某个白名单环境未注册、类型不符或没有共同组网，该脚本显示具体错误，其他脚本仍可选择。新增连接后刷新脚本与候选。

可选的 `SCRIPT_ARGUMENTS` 是 Python 字面量数组，每项包含 `name`、`type`、`required`、`default`、`description`。类型支持 `str/int/float/bool/list/dict`；列表和字典填写 JSON。脚本通过 `paths.arguments[name]` 读取。

HDFS 输入由脚本中的 `hdfs_download(path, "config包名")` 发现。留空沿用 `config.json`，填写时只覆盖远端 HDFS 路径。复杂参数通过请求 JSON 传给同一 CLI，其余参数转换为 `--topology/--bind/--package/--arg`。

Telnet 连接为 TCP 串口网关，不是本地 COM。`posix` 可读取 shell 返回码；`prompt_only` 需要设备自检命令和响应关键词，普通命令能否成功应使用该设备输出判断。填写账号密码后，会按登录提示符认证；自定义提示符位于展开项，默认 `login:` 和 `Password:`。无认证网关留空账号密码。Telnet 不提供文件传输。

## 补充组件与测试

Web 主页面只保留环境注册、脚本运行、操作结果和日志定位。Tools、Agent 终端、历史日志查询不再显示为主页面模块；已有独立高级 SDK/API 契约继续保留。

停止按钮终止本次本地 CLI 子进程，保留已经写入的日志。再次运行创建新的执行目录，不复用前一次事件。

```powershell
python -X utf8 -m pytest -q tests/test_web_cli_bridge.py tests/test_interfaces_web_ftp.py
node --check webPage/app.js
python -X utf8 -m compileall -q webPage
```

测试使用本机 HTTP、临时项目和真实 CLI 子进程，验证中文参数、两级环境保存、无导入发现、逐操作输出与完整日志分离，以及成功/准备失败/运行失败。运行完整测试的要求见 [测试说明](../tests/README.md)。真实目标设备与 HDFS 仍需按明确授权单独验收。
