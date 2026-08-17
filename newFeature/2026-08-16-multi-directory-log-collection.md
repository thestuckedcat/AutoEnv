# 多目录日志收集与 BusyBox 兼容枚举

## 意图

日志可能分散在同一远端设备的多个目录，且设备仅提供 BusyBox。原有 Tool 只能提交一个 `remote_dir`，远端枚举还使用 BusyBox `find` 不支持的 GNU `-printf`，无法满足实际部署。

## 用户可见行为

- Tools 页“远端日志目录”改为多行输入，每行一个路径。
- 所有路径组成同一个日志批次；任一路径失败时整个批次失败，不生成可查询的部分结果。
- 不同目录中的同名文件可以同时收集，不会互相覆盖。
- 页面查询仍只展示 `manifest.json` 状态为 `ready` 的批次。

## 架构与数据流

```text
remote_dirs textarea
    -> webPage/tools/log_collection.py 按行解析
    -> LogCollection.download_many()
    -> raw/source-001, source-002, ...
    -> expanded/source-001, source-002, ...
    -> group/match/finalize
    -> targets + index.sqlite3 + manifest.json
```

远端单目录枚举不再使用 `find -printf`。SSH shell 在确认目录存在后，用 BusyBox ash 的非递归 glob 遍历普通文件，以 `wc -c` 读取大小、`stat -c %Y` 读取 mtime，并由 `printf` 输出 NUL 分隔字段。NUL 分隔避免空格、Tab 或换行文件名破坏元数据解析。

## 公共接口与语义

```python
result = collection.download_many(
    host,
    remote_dirs=["/var/log/product", "/var/log/product-backup"],
    glob="cpdt_*",
    protocol="scp",
)
```

- `remote_dirs` 必须是至少一个非空字符串组成的序列，重复目录会被拒绝。
- `source-NNN` 按页面输入顺序编号；分组仍按远端 mtime、相对路径稳定排序。
- manifest 的 `download.remote_dirs` 记录完整目录列表，`download.directories` 记录逐目录状态；单目录兼容字段 `download.remote_dir` 保留。
- 多目录下载失败时保留 manifest/运行记录诊断，但清空 `raw/` 已下载内容。
- `download()` 单目录 API 保持可用。

## 日志处理注释

`webPage/tools/log_collection.py` 现在明确标注产品规则层的四个阶段，以及 AUTH line 和 DB block 的时间继承语义。`autoenv/logs.py` 补充了批次状态、目录隔离、归档展开、稳定排序、block 状态机、编码选择、SQLite/manifest 原子发布等维护注释；`autoenv/log_query.py` 说明了查询过滤与跨午夜规则；`autoenv/ssh_host.py` 说明 BusyBox 枚举和原子下载边界。

## 主要文件

- `webPage/tools/log_collection.py`
- `autoenv/logs.py`
- `autoenv/log_query.py`
- `autoenv/ssh_host.py`
- `tests/test_log_collection.py`
- `tests/test_ssh_host.py`

## 验证

- `python -m compileall -q autoenv webPage tests`
- `python -m pytest tests/test_log_collection.py tests/test_ssh_host.py -q`
- Tool validator、共享契约及完整离线 UT 见本次变更最终验证记录。

未在真实 BusyBox/SSH/SCP 设备执行在线验收；需要在明确授权的 `1260网口` 实验环境确认目标 BusyBox 构建包含 `wc`、`stat` 和 `printf` applet。
