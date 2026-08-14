# AutoEnv Web 快速入门

## 启动

在仓库根目录运行：

```powershell
python -X utf8 startWeb.py
```

服务默认只监听 `127.0.0.1:8765`。依次使用四个页签：

1. **环境库**：注册 SSH 网口、Telnet 串口和可选 FTP 目标。每个 IP 必须从固定目录选择唯一资源标签（1260/1712/udie1 的网口或串口）。JSON 保存在 `environments/`，密码按当前项目约定明文保存且目录默认不提交。
2. **环境启动**：先选脚本，页面按脚本声明的 `alias` 与 `description` 展开所有交互资源。SSH/Telnet/FTP 交互点分别选择一个包含匹配标签的环境/IP，因此一次启动可组合多个环境；每个 HDFS 包也有独立的说明和链接输入，留空使用 `config.json` 的 link/base_link 逻辑，填写则覆盖为指定 HDFS 路径。
   当前页面不提供启动后 `register_func` 菜单的输入控件；包含该菜单的脚本应从 CLI 运行，或确保输入流关闭后自动退出菜单。
3. **Tools**：工具由 `webPage/tools/*.py` 动态发现。不要修改核心页面来增加工具。
4. **Agent CLI**：通过 Windows ConPTY 启动 `codeagent` 或 `nga`，持续渲染页面刷新输出；拖入图片、`.py` 或 `.zip` 后把落盘绝对路径插入消息。

## 非交互启动

下面的仓库示例使用占位环境和地址，供查看 JSON 结构；运行前必须改成实际已注册环境或有效连接参数，不能把示例成功加载理解为设备已验证。

```powershell
python -X utf8 adapt_interface.py --request docs/examples/launch-request.json
```

也可直接传参数：

```powershell
python -X utf8 adapt_interface.py --script download_and_parse_logs --environment lab-a --parameters '{"arguments":{"remote_dir":"/var/log","remote_pattern":"^logs-.*\\.zip$"}}'
```

## 添加 Tool

调用仓库 skill `.agents/skills/autoenv-web-tool/SKILL.md`，或复制 `webPage/tools/system_info.py`。工具必须：

- 使用 `register_web_tool()`；
- 接收一个 `dict`；
- 返回可 JSON 序列化值；
- 不直接修改 `index.html`、`app.js` 或 `styles.css`；
- 有对应离线 UT。

## Agent 文件导入

图片只转换为本地路径。Python 文件或 ZIP 不会自动执行；应让 Agent 使用 `.agents/skills/import-python-web-tool/SKILL.md` 检查、提取并注册为 Tool。项目 ZIP 必须拒绝路径穿越和符号链接，且不得覆盖已有工具。
