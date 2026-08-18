# 日志元数据、strict block 与虚拟连续浏览

## 目标与用户行为

日志行可从命名正则提取 `slot_id`、`socket_id` 并像 timestamp 一样逐字段继承。Web 以连续滚动替代可见分页，普通行只显示极简时间与 `S/K` 属性；source 留在悬停详情。用户可筛选 slot/socket、隐藏模板匹配片段、调整字号、收起 Tools 左栏，并双向拉伸日志窗口。

## strict block 语义

`match_block(..., boundary_mode="strict")` 暂存文件头：首边界为 END 时保留前缀，为 BEGIN 时丢弃。活动块内后续 BEGIN 不重新开块，但该控制行仍能刷新属性；首个 END 关闭，EOF 视为正常 END。END 后正文丢弃，直到新的 BEGIN。`consume_regex` 行先参与属性提取，再从输出移除。旧调用默认 `legacy`，避免静默改变既有脚本。

## 架构与数据流

`LogGroup` 缓存每个文件的一次解码/解析结果，使 line、line-block 和 strict block 复用 timestamp/metadata 上下文。SQLite 新增 slot/socket、字段来源和匹配 span，并建立 target/metadata/sequence 索引；查询检测旧 schema。普通浏览直接由 SQL `LIMIT/OFFSET` 分块，不再 `fetchall()` 全 target。Web 每次请求 240 行并只创建当前虚拟窗口 DOM。

## 公共接口

```python
groups = collection.source_groups(
    timestamp=timestamp,
    metadata_patterns=(
        MetadataPattern(r"slotid=(?P<slot_id>[^\s]+)"),
        MetadataPattern(r"socketid=(?P<socket_id>[^\s]+)"),
    ),
)
groups["product"].match_block(
    r"BEGIN EXTRACT LOG",
    r"END EXTRACT LOG",
    "extract.log",
    boundary_mode="strict",
    consume_regex=r"LOG_TYPE\[\d+\]\s+seg\[\d+\]",
)
```

查询 API 新增 `offset`、`slot_id`、`socket_id`；返回 `slot_id_source`、`socket_id_source` 和 `matched_spans`。

关联高亮现由服务端跨 target 按时间查询，窗口由 Web 以秒为单位指定；它不会附带 slot/socket 条件。slot/socket 仍是当前窗格的精确筛选。Web 还提供不落盘的 8 MiB 本地样本规则预览，以及“原始文本”“带元数据文本”两种完整导出，隐藏模板只改变显示、不改变导出内容。

每批 manifest 记录 `rule_schema_version`、完整规则描述、`rules_hash` 和 `index_schema_version`。产品规则集中在 `autoenv/log_collection_rules.py`，采集和预览共同调用，避免两套规则漂移；所有 matcher 复用同一文件的解码/解析缓存。

## 主要文件

- `autoenv/logs.py`、`autoenv/log_query.py`、`autoenv/__init__.py`
- `webPage/server.py`、`webPage/app.js`、`webPage/logs.css`
- `autoenv/log_collection_rules.py`、`webPage/tools/log_collection.py`
- `tests/test_log_collection.py`、`tests/test_interfaces_web_ftp.py`

## 验证与限制

离线测试覆盖 strict 边界、跨 block/不跨文件继承、unknown、consume、属性筛选、秒级服务端关联、预览、双格式导出、规则/schema 版本、单次文件解析、旧 schema/旧行为和 Web 契约。验证结果：Python compileall、JavaScript `node --check` 和完整测试均通过；全量离线 UT 为 `296 passed, 1 skipped`，跳过项是 Windows 主机不允许创建符号链接。本机浏览器确认秒级关联、样本预览、字号和左栏控件已渲染、没有分页按钮、控制台无错误；预览 API 实测正确返回继承的 timestamp/slot/socket。仓库无真实日志批次，未执行百万行浏览器压测；未连接 SSH/BusyBox 目标。
