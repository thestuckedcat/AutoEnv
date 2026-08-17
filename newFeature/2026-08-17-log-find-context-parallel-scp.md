# Web Find 上下文与并行批量 SCP

## 意图

日志 Find 原先只返回命中行，定位异常时仍需退出筛选寻找前因后果；批量 SCP 原先逐文件串行传输，大量压缩日志会把网络等待时间线性累加。本改动让 Find 可直接展示每个命中的上下文，并在不改变 BusyBox 枚举、原子落盘和整批失败语义的前提下并行下载同一批文件。

## 用户可见行为

- 每个日志窗口在 Find 旁新增“上下 N 行”，范围 `0..50`，默认 `3`。
- Find 命中行显示暖色并高亮关键词；仅作为上下文返回的行显示蓝色。
- 查询先按命中分页，再为本页每个命中展开上下 N 行。上下文不会被普通分页边界截断，重叠窗口去重后仍按 target 原始顺序显示。
- 时间窗继续决定哪些行属于命中，但不会二次裁剪命中的上下文。
- 同一 `scp_download_many()` 批次最多并行四个文件；页面继续显示单一 `completed/total` 进度条。

## 架构与数据流

### Find

1. `webPage/app.js` 为每个 pane 保存独立的 keyword 和 context 数值。
2. `/api/log-batches/query` 将 `context` 传给 `query_log_records()`。
3. 查询层读取 target 的完整 sequence，先筛出满足 keyword/时间条件的命中索引并对命中分页。
4. 对本页命中索引展开前后范围，以 set 去重并排序。
5. 响应行通过 `find_role=match|context` 标记用途；前端映射到不同 CSS class。

`total` 和 `has_more` 描述命中数量，`returned_count` 描述本页展开后实际返回的行数。未填写 keyword 时忽略 context，保持普通分页行为。

### 并行 SCP

1. BusyBox ash glob、`test`、`stat` 与 NUL 分隔枚举逻辑不变。
2. 主线程只建立一次 Paramiko transport；最多四个 worker 各自创建和关闭一个 `SCPClient`/channel，绝不在线程间共享 `SCPClient`。
3. 每个 worker 使用唯一 `.part`，SCP 正常返回后原子改名并恢复远端 mtime。
4. 完成计数和进度日志在同一锁内更新，保证 `completed=1..N` 单调递增。
5. worker 完成顺序不影响结果；`RemoteBatchDownloadResult.files` 按枚举的 mtime/名称顺序组装。
6. 任一 worker 失败时等待活动 worker 关闭 channel，随后删除全部成功文件和残留 `.part`，保留 matched/completed 诊断计数。

## 公共接口

```python
query_log_records(
    root_dir,
    batch_id,
    target,
    keyword="ERROR",
    context_lines=3,
)
```

HTTP 查询新增 `context=0..50`。`SSHHost.scp_download_many()` 的 Python 签名不变，并行度是框架固定安全上限，不由 Web 请求控制。

## 主要文件

- `autoenv/log_query.py`
- `autoenv/ssh_host.py`
- `webPage/server.py`
- `webPage/app.js`
- `webPage/logs.css`
- `webPage/index.html`
- `tests/test_log_collection.py`
- `tests/test_ssh_host.py`
- `tests/test_interfaces_web_ftp.py`

## 测试与验证

- `tests/test_ssh_host.py tests/test_log_collection.py tests/test_interfaces_web_ftp.py`：113 passed；覆盖 Find 上下文、分页边界、角色标记、时间窗组合、范围校验、四路并发峰值、稳定结果顺序、无大小校验、进度和部分失败清理，并包含最新 main 的 line-block 回归。
- `tests/test_generated_script_contract.py`：9 passed。
- `compileall`、`node --check webPage/app.js`、三个 Web Tool validator、注册发现以及四个仓库 Skill quick validation 均通过。
- 全量离线测试：289 passed, 1 failed；唯一失败为主线已有的 POSIX 环境 Windows 绝对路径断言 `tests/test_results_selectors.py::test_selectors_reject_unsafe_paths_and_package_ambiguity`。
- 排除上述已知基线项：289 passed, 1 deselected。

## 真实环境验证缺口

未连接真实 SSH/BusyBox 设备。离线测试证明四个 worker 同时进入传输，并验证排序与清理契约；仍需在授权实验设备上确认目标 SSH 服务允许同一 transport 同时打开四个 channel，并观察真实链路吞吐提升与设备负载。
