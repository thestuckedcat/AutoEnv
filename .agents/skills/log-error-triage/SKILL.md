---
name: log-error-triage
description: Analyze provided local log files or log folders containing [ERROR] entries, correlate failures across components and timestamps, trace distinctive messages and stack frames into the local source tree, consult component-specific historical incident knowledge, and produce evidence-based problem localization and ownership boundaries. Use when users ask to inspect error logs, locate the responsible code/component, distinguish root cause from reporter or downstream victim, compare with known issues, or record a confirmed diagnosis into the repository knowledge base.
---

# Triage Log Errors Against Local Code

Keep analysis read-only unless the user explicitly asks to modify code or record a conclusion. Treat historical knowledge as a lead, never as proof for a new incident.

## Required inputs and repository facts

1. Resolve every user-provided log file or directory. If none is explicit, inspect only attached files and obvious paths mentioned in the request; do not scan the whole filesystem.
2. Use the current workspace as the default source root. If it contains `AGENTS.md`, follow it before analyzing code.
3. Read `logKnowledge/README.md` before consulting or writing historical conclusions. Read only relevant non-underscore component directories under `logKnowledge/components/`.
4. Record missing logs, truncated logs, unavailable source roots, build/version mismatch, clock/timezone ambiguity, and absent component mappings as evidence limitations.

## Workflow

### 1. Extract `[ERROR]` evidence

Run the bundled scanner for deterministic extraction and surrounding context:

```powershell
python -X utf8 .agents/skills/log-error-triage/scripts/extract_error_context.py <log-or-folder> --context 4
```

Use `--output <file.json>` only for a temporary or user-requested artifact. The scanner supports text logs and single-file `.gz` logs with UTF-8-SIG, UTF-8, GB18030, and Latin-1 fallback.

Preserve, for every error occurrence:

- absolute source log path and 1-based line number;
- exact `[ERROR]` line and nearby context;
- visible timestamp, process/thread/logger, component hint, request/session/trace ID;
- stack frames, error codes, remote endpoint, operation and resource identifiers.

Cluster only when stable evidence matches, such as the same normalized error text, error code, top stack frame and operation. Do not merge messages merely because both use `[ERROR]`.

### 2. Establish chronology and cross-component correlation

Order occurrences by the most complete available timestamp. Preserve file order when timestamps are missing or equally precise.

For each cluster, distinguish:

- first observable trigger;
- first failing operation;
- later propagation or retry noise;
- components that only report, wrap or react to the failure.

Correlate components using time proximity plus a shared trace/request/session/resource ID. Time proximity alone is weak evidence. State timezone and partial-timestamp assumptions explicitly.

### 3. Map evidence to components

Read matching `component.yaml` descriptors first when they exist. Use aliases, log markers and `code_roots` as search hints, not conclusions.

Search the local repository with `rg` in this order:

1. exact distinctive error string excluding volatile timestamps, IDs and values;
2. stable error code or logger name;
3. function/class/file names from stack frames;
4. shorter message fragments combined with the suspected code root.

Prefer fixed-string searches (`rg -n -F`) before regex. Exclude generated output, dependencies, `.git`, downloaded logs and the knowledge base from source searches unless directly relevant. If exact text is constructed dynamically, trace constants, formatting calls and exception wrappers.

Do not assign ownership from a directory name alone. Confirm a component using at least one code emission site, stack frame, component descriptor, runtime logger or call-path relationship.

### 4. Trace the code path

For each likely emission site:

1. Read the full function and its direct callers/callees relevant to the error.
2. Identify validation, state transitions, retry/timeout behavior, exception translation and cleanup.
3. Separate the code that detects/reports the error from the code or dependency that caused it.
4. Check configuration and input provenance when the branch is data-dependent.
5. Use read-only Git history or blame only when it helps establish version or intent; do not assume the latest source matches the log-producing build.

Stop when the evidence cannot support a narrower conclusion. Do not implement a fix unless the user asks.

### 5. Consult historical knowledge without anchoring

After forming an initial evidence-based hypothesis, search relevant component descriptors and issue files with distinctive signatures, error codes, function names and dependency names.

For each historical match, report:

- why the signature is comparable;
- version/environment differences;
- which prior evidence is independently reproduced now;
- which parts remain only a precedent.

Never promote `provisional` knowledge to a confirmed diagnosis. A historical conclusion cannot override contradictory current logs or code.

### 6. Produce the diagnosis and boundary report

Lead with the narrowest supported outcome. Use this structure:

```markdown
## 结论
- 主定位：<component / code location / dependency>
- 定界：<internal | upstream | downstream | environment | cross-component | insufficient evidence>
- 置信度：<high | medium | low>

## 错误簇
| 错误簇 | 首次时间 | 次数 | 报错组件 | 代码发出点 | 判定 |

## 证据链
1. 日志：<absolute-log-path:line> — <fact>
2. 代码：<absolute-source-path:line> — <fact>
3. 关联：<ID/time/call path> — <inference and strength>

## 定界理由
- 触发方：...
- 失败方：...
- 报告/受害方：...
- 排除项：...

## 未确认项与下一步
- <smallest test, extra log, version or runtime evidence needed>

## 历史知识命中
- <issue path and applicability, or “无可靠命中”>
```

Use clickable absolute file links and tight line references. Clearly separate confirmed facts, inferences and unknowns. Avoid claiming root cause when evidence only identifies the reporting component.

## Record confirmed knowledge

Only write knowledge after the user explicitly asks to record it or confirms the diagnosis. Do not silently learn from an unconfirmed analysis.

Create a component/issue scaffold with:

```powershell
python -X utf8 .agents/skills/log-error-triage/scripts/new_knowledge_issue.py `
  --component <component> --slug <short-slug> --title "<title>" --status confirmed
```

Then replace every TODO in the generated issue. Preserve exact reusable signatures without secrets or volatile IDs. Include code version/range, evidence, boundary, root cause, exclusions, fix/workaround and regression checks. Update `component.yaml` aliases, markers, code roots, owners and dependencies when newly confirmed.

Use `provisional` only when the user explicitly wants an unresolved lead recorded. Never store passwords, tokens, customer data, full raw logs or unsupported blame. Refuse to overwrite an existing issue file.

## Validation

When changing this skill or its knowledge schema:

1. Run both bundled scripts against temporary sample data.
2. Run the skill validator:

```powershell
python -X utf8 C:/Users/admin/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/log-error-triage
```

3. Run focused repository tests and update the companion `newFeature/` record for material capability changes.
