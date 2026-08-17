# Web Tool 注册模板

## 意图

提供一个可直接复制的 local Web Tool 注册样板，并把注册、自动发现、workflow 脚手架和验证流程统一写入项目文档，减少新增 Tool 时修改核心页面或误入环境脚本注册表的风险。

## 用户可见行为

- 新增 `webPage/tools/_template.py`；
- 模板覆盖注册元数据、text/select/checkbox 字段、输入校验和 JSON 输出；
- 模板本身不会显示在 Tools 页；复制为非下划线 `.py` 文件并替换占位符后自动注册；
- workflow Tool 继续使用 `scaffold_tool.py --kind workflow`，避免用 local 模板猜测资源绑定；
- 正式 Tool 无需修改 `index.html`、`app.js` 或 CSS。

## 自动发现语义

```text
webPage/tools/_template.py
  -> 下划线文件：发现器跳过

复制并重命名为 webPage/tools/my_tool.py
  -> 导入模块
  -> 执行唯一 @register_web_tool
  -> 出现在 Tools 页
  -> 永远不进入 scripts 下拉框
```

每个非下划线 Tool 文件必须注册且只注册一个 Tool。模板允许被静态验证器检查，但运行期发现器不会导入它。

## 使用方式

local Tool：

```powershell
Copy-Item webPage/tools/_template.py webPage/tools/my_tool.py
python -X utf8 .agents/skills/autoenv-web-tool/scripts/validate_tool.py webPage/tools/my_tool.py
```

复制后必须替换文件中的注册名、标题、说明、字段和实现。

workflow Tool：

```powershell
python -X utf8 .agents/skills/autoenv-web-tool/scripts/scaffold_tool.py `
  my-workflow --title "My workflow" --kind workflow
```

## 主要文件

- `webPage/tools/_template.py`
- `.agents/skills/autoenv-web-tool/SKILL.md`
- `.agents/skills/autoenv-web-tool/references/contract.md`
- `README.md`
- `webPage/QUICK_START.md`
- `docs/web_usage.md`
- `docs/WEB_ARCHITECTURE_AND_HANDOFF.md`
- `docs/DOCUMENTATION_AUDIT.md`
- `tests/test_log_collection.py`

## 验证

- 模板通过 `validate_tool.py`；
- 自动发现结果不包含模板占位注册名；
- 聚焦与完整离线 pytest；
- Python 编译、Tool/脚本契约、Skill 校验和 `git diff --check`。

验证结果：

- 模板、`system_info.py` 和 `log_collection.py` 均通过 Tool 验证器；
- 模板发现隔离、workflow 脚手架及唯一 Web 启动聚焦测试：`3 passed`；
- AutoEnv Web Tool Skill 校验：通过；
- Python `compileall` 和环境脚本契约：通过；
- 本次合并提交的最终完整离线 pytest：`269 passed, 1 skipped`。跳过项是当前 Windows 主机不允许创建符号链接；另有 Paramiko Blowfish 弃用和测试故意创建 ZIP 重名项的预期警告；
- `git diff --check`：通过。
