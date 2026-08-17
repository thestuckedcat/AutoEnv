# Line-block 起始行与无时间戳续行匹配

## 目标

部分日志记录只有第一行同时包含时间戳和业务标记，后续堆栈、缩进字段或换行正文没有时间戳。普通 `match_line()` 只保留标记行，begin/end `match_block()` 又要求产品提供明确边界。新增 line-block 模式，在不引入结束正则的情况下完整保留这类多行记录。

本功能建立在现有 `TimestampPattern`、稳定 source 顺序和 target finalize 机制上，不改变 `match_line()` 或 `match_block()` 的既有行为。

## 公共接口

```python
result = group.match_line_block(
    r"\[ERROR\]",
    target_file="errors.log",
)
```

`start_regex` 和 `target_file` 的正则编译、target 文件名限制、结果对象及失败处理与现有匹配方法一致。操作结果以 `LOG MATCH LINE BLOCK` 记录到 `run.log`。

## 匹配流程

1. 每个源文件开始时清空活动块和继承时间。
2. 扫描每一原始行并先尝试 `TimestampPattern.parse()`。
3. `start_regex` 命中的行写入 target，并成为活动块起始行。
4. 活动块后续所有没有时间戳的行继续写入，使用起始行的关联时间。
5. 下一条带时间戳的行是硬边界：先关闭旧块；若该行同时匹配 start，则直接开始新块，否则该行不进入 target。
6. 文件结束会自然结束活动块；下一文件开头的无时间戳行不会串入上一文件。

空行也是无时间戳行，因此会被活动块保留。起始行本身没有时间戳时，沿用 `match_line()` 语义：继承本文件上一合法时间；没有可继承时间则显示 `[-]`。非法日历日期的起始行及其续行显示 `[?]`，不参与 Web 时间窗查询。

## 顺序与索引

- 记录仍按 target、文件顺序、源行号和规则声明顺序 finalize，因此新增续行不会打乱其他规则生成记录的相对位置。
- 起始行的 `timestamp_source` 仍为 `parsed`、`inherited`、`invalid` 或 `unknown`。
- 正常续行使用 `timestamp_source=line_block_continuation`；非法日期块的续行保持 `invalid`，确保 target 文件和 Web 查询都显示 `[?]`。
- 每个续行保留自己的 `source_file` 和 `source_line`，便于回查原始日志。

## 主要文件

- `autoenv/logs.py`
- `tests/test_log_collection.py`
- `docs/ENVIRONMENT_REGISTRATION_GUIDE.md`
- `docs/WEB_ARCHITECTURE_AND_HANDOFF.md`

## 验证范围

- 匹配起始行、普通续行、缩进续行和 EOF 续行。
- 非匹配时间戳终止活动块，之后的无时间戳行不会误收。
- 下一条带时间戳且匹配 start 的行可开启新块。
- 无时间戳 start 继承本文件上一合法时间。
- 非法日期 start 及其续行显示 `[?]`。
- 文件边界重置活动块，下一文件的 orphan 续行不会跨文件混入。
- SQLite/Web 查询保持时间、源行和 `timestamp_source` 契约。

## 执行结果

- 日志收集完整回归：`27 passed`。
- 统一环境脚本契约：`9 passed`。
- Tool 验证、注册发现隔离、四个仓库 Skill 校验、Python 编译、JavaScript 语法和 diff 检查均通过。
- 全量离线 UT：`287 passed, 1 failed`；唯一失败是主线既有的 `test_selectors_reject_unsafe_paths_and_package_ambiguity` POSIX/Windows 盘符断言差异。
- 排除该主线既有项：`287 passed, 1 deselected`。

未执行真实日志服务器或 BusyBox 设备验收；该功能只处理已下载并解压的本地日志内容，离线测试使用临时日志和 SQLite 覆盖匹配、边界、顺序与查询行为。
