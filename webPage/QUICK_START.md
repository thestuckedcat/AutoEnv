# AutoEnv Web 快速入门

## 启动

在仓库根目录运行：

```powershell
python -X utf8 startWeb.py
```

Web 框架、按钮触发 Python 子进程和环境到脚本参数的完整说明见
[`../docs/web_usage.md`](../docs/web_usage.md)。

服务固定监听 `127.0.0.1:8765`。这是唯一启动命令和端口，不支持参数覆盖或自动回退；若端口已被占用，请停止占用进程后重试。依次使用四个页签：

1. **环境库**：注册 SSH 网口、Telnet 串口和可选 FTP 目标。每个 IP 必须从 `autoenv/resource_labels.json` 维护的目录中选择唯一且协议匹配的资源标签。环境 JSON 保存在 `environments/`，密码按当前项目约定明文保存且目录默认不提交。首次保存后，“环境启动”和已打开 Tool 的资源下拉框会立即出现匹配环境，无需重启 Web。
2. **环境启动**：先选脚本，页面按脚本声明的 `alias` 与 `description` 展开所有交互资源。SSH/Telnet/FTP 交互点分别选择一个包含匹配标签的环境/IP，因此一次启动可组合多个环境；每个 HDFS 包也有独立的说明和链接输入，留空使用 `config.json` 的 link/base_link 逻辑，填写则覆盖为指定 HDFS 路径。
   当前页面不提供启动后 `register_func` 菜单的输入控件；包含该菜单的脚本应从 CLI 运行，或确保输入流关闭后自动退出菜单。
3. **Tools**：工具由 `webPage/tools/*.py` 动态发现。普通 local Tool 在 HTTP 进程返回 JSON；workflow Tool 在独立子进程中接收 `RunContext`、绑定环境资源并支持事件与停止。Tool 使用独立注册表，不会出现在“环境启动”的脚本下拉框。
4. **Agent CLI**：通过 Windows ConPTY 先在指定目录启动本地 `cmd.exe`，再把设置的 `codeagent`、`nga` 或其他命令作为键盘字符自动输入并回车；命令留空时只打开 cmd。点击终端画布后可继续直接输入，不使用独立发送框；把图片、`.py` 或 `.zip` 粘贴/拖到终端会保存文件，并把带引号的绝对路径插到当前 CLI 输入（不自动回车）。

## 非交互启动

下面的仓库示例使用占位环境和地址，供查看 JSON 结构；运行前必须改成实际已注册环境或有效连接参数，不能把示例成功加载理解为设备已验证。

```powershell
python -X utf8 adapt_interface.py --request docs/examples/launch-request.json
```

workflow Tool 的结构化入口独立于环境脚本：

```powershell
python -X utf8 adapt_tool_interface.py --request docs/examples/tool-request.json
```

示例会连接所选环境中标签为 `1260网口` 的 SSH 资源；只有目标环境已明确授权时才实际运行。

## 添加 Tool

调用仓库 skill `.agents/skills/autoenv-web-tool/SKILL.md`。新增 local Tool 时复制 `webPage/tools/_template.py` 为 `webPage/tools/<name>.py`，然后替换注册名、标题、字段和函数体；前导下划线让模板本身不会出现在 Tools 页。workflow Tool 使用脚手架生成：

```powershell
python -X utf8 .agents/skills/autoenv-web-tool/scripts/scaffold_tool.py `
  sample-workflow --title "示例 workflow" --kind workflow
```

也可分别参考 `webPage/tools/system_info.py` 和 `webPage/tools/log_collection.py`。工具必须：

- 使用 `register_web_tool()`；
- 明确使用默认 `kind="local"` 或 `kind="workflow"`；
- local Tool 接收一个 `dict` 并返回可 JSON 序列化值；
- workflow Tool 接收一个 `RunContext`，只通过 AutoEnv SDK 使用声明并绑定的资源，返回 `None` 或 AutoEnv 结果；
- 不直接修改 `index.html`、`app.js` 或 `styles.css`；
- 有对应离线 UT。

完成后执行：

```powershell
python -X utf8 .agents/skills/autoenv-web-tool/scripts/validate_tool.py webPage/tools/<name>.py
python -X utf8 -m pytest -q
```

## 日志收集与关联分析

Tools 页的“日志收集与关联分析”只要求选择包含 `1260网口` 的 SSH 环境。远端路径与每条路径的下载通配符固化在 `webPage/tools/log_collection.py` 的 `LOG_SOURCES`；当前默认来源是 `/var/log/product → cpdt_*`。每次点击“获取日志”会创建唯一 `run_id` 批次，递归解压匹配到的压缩包，并把每个来源的普通文件和展开内容直接组成独立 Group，再由脚本中的 line/block 规则生成 `auth.log` 和 `database.log`。批量 SCP 的已完成数、总数和百分比通过页面进度条原地更新；LIVE TASK OUTPUT 只保留开始、匹配数和最终摘要，不展开几百个文件名。逐文件 start/complete/failed、内部远端枚举、编码判断和异常堆栈保存在该次运行的 `run.log`。

只有成功 `finalize()` 的批次会出现在查询列表。可打开多个目标日志窗口，按文件顺序分页浏览，或填写中心时间和可选日期进行共享时间窗查询；窗口分钟数按中心前后各一半解释。每个窗口的 `Find` 输入框会在整个 target file 中执行不区分大小写的关键词筛选，并高亮匹配正文；它可与时间窗组合使用。点击有时间的记录后，其他窗口中相差不超过五分钟的已显示记录会高亮；完全没有时间的 `[-]` 和日期文本非法的 `[?]` 记录只按原始顺序浏览，不参与时间查询和联动。

## Agent 文件导入

图片只转换为本地路径。图片既可以从资源管理器拖入终端，也可以复制位图后在已聚焦的终端内粘贴；普通文本粘贴会直接写入 ConPTY。Python 文件或 ZIP 不会自动执行；应让 Agent 使用 `.agents/skills/import-python-web-tool/SKILL.md` 检查、提取并注册为 Tool。项目 ZIP 必须拒绝路径穿越和符号链接，且不得覆盖已有工具。
