# 日志问题定位知识库

本目录保存由人维护、可复用的组件边界与历史问题定位结论，供
`.agents/skills/log-error-triage/` 在分析新日志时检索。历史记录只是检索线索；每次定位仍需由当前日志和当前代码独立验证。

## 目录约定

```text
logKnowledge/
  components/
    _template/                 # 模板，不参与检索
      component.yaml
      issues/ISSUE_TEMPLATE.md
    <component>/               # 小写组件名
      component.yaml           # 组件别名、代码范围、日志标记和依赖
      issues/
        YYYY-MM-DD-<slug>.md    # 一次可复用的问题结论
```

以下划线开头的组件目录是模板或辅助内容，分析时必须忽略。组件名和问题 slug 仅使用小写字母、数字、`-`、`_`。

## 新增组件或问题

可以复制 `_template`，也可以使用只创建、不覆盖的辅助脚本：

```powershell
python -X utf8 .agents/skills/log-error-triage/scripts/new_knowledge_issue.py `
  --component auth-service `
  --slug invalid-token `
  --title "Token 校验失败" `
  --status confirmed
```

创建后必须人工补完所有 `TODO`，并检查 `component.yaml`：

- `aliases`：日志或代码中稳定出现的组件别名；
- `code_roots`：相对仓库根目录的代码路径，不使用机器相关的绝对路径；
- `log_markers`：logger、进程名、固定前缀等稳定标记；
- `owners`：团队或维护范围，不记录临时个人推测；
- `dependencies`：直接上下游组件，用于辅助定界。

问题状态含义：

- `confirmed`：证据链和定界已经由人确认；
- `provisional`：明确要求保留、但尚未闭环的调查线索；
- `rejected`：历史上相似但已被证伪的假设，用于避免重复误判。

只有用户或评审者明确确认后，才能把分析结论写入本目录。不得因 Skill 自动命中历史记录就修改状态或新增结论。

## 记录质量与安全

每条问题记录至少包含稳定错误签名、代码版本或适用范围、日志与代码证据、触发方/失败方/报告方、责任边界、排除项、修复或规避以及回归检查。

不要保存密码、令牌、客户数据、完整原始日志、动态请求 ID、无必要的主机地址或无法证实的责任归属。引用日志时只保留完成证据链所需的最小片段，并在提交前脱敏。
