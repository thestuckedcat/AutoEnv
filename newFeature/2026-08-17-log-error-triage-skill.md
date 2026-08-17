# 日志错误定位与组件定界 Skill

## 意图

为 Agent 增加一个仓库内可复用的 `log-error-triage` Skill：用户提供日志文件或目录后，自动发现 `[ERROR]`，结合上下文、时间与关联 ID 形成错误簇，再从本地源码追踪错误发出点和调用路径，最终给出组件问题定位与责任边界。同时建立一个由人维护的历史问题知识库，允许在确认后沉淀可复用结论。

## 用户可见行为

- 接受一个或多个本地日志文件/目录，默认精确查找 `[ERROR]`；
- 普通文本与单文件 GZ 日志支持 UTF-8-SIG、UTF-8、GB18030、Latin-1 解码回退；
- 为每个错误保留绝对路径、行号及前后文，按稳定签名聚类；
- 优先用错误原文、错误码、logger、栈帧在本地代码中寻找发出点；
- 区分触发方、失败方和报告/受害方，输出 `internal`、`upstream`、`downstream`、`environment`、`cross-component` 或证据不足的定界；
- 在初步假设形成后才查询历史知识，避免历史结论造成锚定；
- 默认只读，不修改代码，也不会自动把推测写成历史事实；只有用户明确要求记录或确认结论后才允许写知识库。

## 架构与数据流

```text
日志文件/目录
  -> extract_error_context.py（确定性提取 [ERROR] 与上下文）
  -> 时间、关联 ID、稳定签名聚类
  -> component.yaml 提供组件/代码目录检索提示
  -> rg 搜索本地源码并追踪调用路径
  -> 对照历史 issue（仅作为线索）
  -> 证据链、代码定位、组件定界和待确认项
  -> 人工明确确认后，才写入 logKnowledge
```

## 公共入口

- Skill：`.agents/skills/log-error-triage/SKILL.md`
- 错误提取：

  ```powershell
  python -X utf8 .agents/skills/log-error-triage/scripts/extract_error_context.py <log-or-folder> --context 4
  ```

- 知识条目脚手架：

  ```powershell
  python -X utf8 .agents/skills/log-error-triage/scripts/new_knowledge_issue.py `
    --component <component> --slug <slug> --title "<title>" --status confirmed
  ```

- 人工知识库：`logKnowledge/components/<component>/component.yaml` 和 `issues/*.md`。

## 重要语义与决策

- `[ERROR]` 默认大小写敏感；只有明确使用 `--ignore-case` 才匹配其他大小写。
- 时间接近本身不是跨组件关联证据；应同时使用 trace/request/session/resource ID 或代码调用关系。
- 目录和 logger 名只能提示组件，至少还需代码发出点、栈帧、组件描述或调用关系之一确认。
- 当前源码不一定等于产生日志的构建版本，版本不明时必须降低置信度。
- 历史 issue 分为 `confirmed`、`provisional`、`rejected`，任何状态都不能代替当前证据。
- 新建知识条目的脚本拒绝路径型组件名和覆盖已有文件；知识文件禁止保存密钥、客户数据、完整原始日志和动态 ID。

## 主要文件

- `.agents/skills/log-error-triage/SKILL.md`
- `.agents/skills/log-error-triage/agents/openai.yaml`
- `.agents/skills/log-error-triage/scripts/extract_error_context.py`
- `.agents/skills/log-error-triage/scripts/new_knowledge_issue.py`
- `logKnowledge/README.md`
- `logKnowledge/components/_template/`
- `tests/test_log_error_triage_skill.py`
- `AGENTS.md`、`README.md`、`tests/README.md`

## 验证

- Skill Creator `quick_validate.py`：通过；
- 两个辅助脚本通过临时普通/GZ 日志和临时知识库执行：`5 passed`；
- Python `compileall`：通过；
- 环境脚本契约：`2 file(s)` 通过；
- Web Tool 验证器：`log_collection.py`、`system_info.py` 均通过；
- 本次合并提交的最终完整离线 pytest：`269 passed, 1 skipped`。跳过项是当前 Windows 主机不允许创建符号链接；另有 Paramiko Blowfish 弃用和测试故意创建 ZIP 重名项的预期警告。

## 尚未进行的现场验证

- 未使用真实业务日志验证组件别名、时区、构建版本和跨组件关联质量；
- 未对真实大型日志做性能基准；单文件默认限制为解码后 256 MiB；
- 知识库当前只有模板，需要维护者逐组件填写代码根目录、稳定日志标记和已确认历史问题。
