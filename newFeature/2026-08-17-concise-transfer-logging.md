# 精简批量传输日志与 Web 进度条

## 目标

日志 Tool 批量下载数百个远端文件时，页面只展示可判断状态的关键信息，并用进度条体现确定总量的进度；逐文件和内部诊断证据继续完整保存，便于定位“远端只匹配 40 个”与“匹配 300 个但只完成 40 个”的区别。

本功能延续 `2026-08-16-scripted-log-source-groups.md` 的脚本化路径/glob、BusyBox 枚举和来源隔离契约，不改变远端读取范围、解压、group 或 target 生成规则。

## 用户可见行为

- 批量 SCP 开始后，LIVE TASK OUTPUT 只保留开始、匹配总数、最终状态和 matched/completed/retained 计数。
- Web 服务把 `SCP BATCH PROGRESS operation_id=... completed=... total=...` 转换为结构化 `progress` 事件；前端使用原生 `<progress>` 控件原地显示 `完成数 / 总数 / 百分比`，不追加几百行进度文本。
- 每个新的批量操作从 `0 / total` 开始；多个脚本化来源顺序下载时，同一进度条使用新的 operation id 和总数继续展示当前来源。
- 用户显式执行的 SSH/Telnet 命令继续实时输出；框架内部的远端枚举、大小查询和批量文件名不会刷到页面。
- workflow 未捕获异常在页面只显示一条紧凑错误，完整 traceback 保存到该次 `run.log`。
- 时间正则命中但 year/month/day 不能组成合法日历日期时，日志处理不再因 `year 0 is out of range` 等异常终止；对应记录按原文件/行顺序保留并显示 `[?]`，完全没有时间的记录仍显示 `[-]`。
- 日志批量 SCP 不再读取或比较远端文件大小。BusyBox 枚举只读取 mtime 以维持稳定顺序，SCP 正常返回后将 `.part` 原子改名；单文件 SCP/SFTP 下载仍保留原有大小校验。

## 文件日志与失败诊断

`run.log` 仍立即 flush 完整证据：

- `SCP BATCH FILE {json}`：每个文件的 start、complete 或 failed 事件，包含 operation id、序号、总数、远端/本地路径和错误；日志批量下载不再采集远端大小。
- `SCP BATCH PROGRESS ...`：每次完成后的累计进度，进程被外部终止时仍能找到最后完成数。
- `SSH EXECUTE {json}`：内部 BusyBox 枚举命令的完整结果，但不进入控制台；日志批量枚举不再发起 `wc` 查询。
- `LOG ENCODING ...`：每个被处理日志文件的实际解码方式，但不进入控制台。
- `UNHANDLED EXCEPTION ...`：未捕获异常的完整堆栈，但不进入控制台。

单个来源失败后仍执行全有或全无清理：已完成文件和 `.part` 不保留。`RemoteBatchDownloadResult` 新增 `matched_count` 和 `completed_count`，即使 `files` 因清理变为空也保留失败前计数；同样的计数写入日志 collection manifest 的目录条目。

## 主要文件

- `autoenv/recorder.py`
- `autoenv/results.py`
- `autoenv/ssh_host.py`
- `autoenv/logs.py`
- `autoenv/web_tools.py`
- `webPage/server.py`
- `webPage/app.js`
- `webPage/logs.css`

## 验证范围

- 25 文件批量下载只产生关键可见消息，逐文件 start/complete 全部静默落盘。
- BusyBox 日志文件枚举以静默控制台模式执行，并且只采集 basename 与 mtime。
- 部分失败清理本地文件，同时结果保留 matched/completed 计数和 failed 文件事件。
- recorder 的静默结果仍写入 `run.log`，批量控制台摘要不包含文件名。
- Web 只把合法且不越界的 completed/total 文本转换为 progress 事件，前端存在并更新原生进度条。
- workflow traceback 不出现在页面紧凑错误中，但完整写入 `run.log`。
- 非法日历日期的 line 记录显示 `[?]`，SQLite `timestamp_source=invalid`，不参与时间窗查询且不改变相对顺序或后续合法时间继承。
- BusyBox 批量枚举命令不含 `wc -c`，成功 SCP 不执行隐式文件长度比对，失败清理语义保持不变。

## 执行结果与现场验证缺口

- 聚焦回归：`116 passed, 1 deselected`；deselect 的项目是下述主线既有失败。
- 统一环境脚本契约：`9 passed`；Tool 验证、注册发现、四个仓库 Skill 校验、Python 编译和 JavaScript 语法检查均通过。
- 全量离线 UT：`285 passed, 1 failed`。唯一失败为 `test_selectors_reject_unsafe_paths_and_package_ambiguity` 在 POSIX 上未拒绝 Windows 盘符路径；已在干净的 `origin/main` 单独复现，不是本功能引入。
- 排除该既有基线项后：`285 passed, 1 deselected`。

未执行真实远端 SSH/SCP 冒烟测试；离线测试使用 fake Paramiko/SCP 和临时文件覆盖枚举、进度、失败清理、日志及 Web 事件契约。
