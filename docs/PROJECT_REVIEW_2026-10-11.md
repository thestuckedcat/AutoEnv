# AutoEnv 项目整体独立评审

日期：2026-10-11。评审者：编码结束阶段新开的独立 subagent。基线提交：`e8e29cf`；评审对象包含本次尚未提交的文件 SDK 与 `product_log/` 实现。

## 总体判断

项目已经具备清晰的常用执行路径：普通 Python 脚本 → 静态声明发现 → CLI 宿主 → 已有协议客户端；本次产品日志也遵循了明确的本地边界，独立目录、公共文件 SDK、共享 CLI/Web workflow、来源保留、确定性跨文件组装和 SQLite 查询均有实际实现。现阶段应优先补齐少数具体的运行边界和并发问题，继续增量维护，不需要推倒重写框架。

本地功能的验证较充分，但“本机监听”仍缺少浏览器来源隔离，历史配置保存和脚本启动也存在事务边界缺口。全项目不能据此宣称已完成安全验收或真实设备验收；这些问题与本次日志功能的本地使用完成度应分别评价。

本报告是工程建议，不是正式 SDD Gate 判决，也不代替人类风险批准。仅新增本报告，未修改实现、配置、技能或历史诊断知识。

## 证据范围

证据标签：**FACT** 表示直接源码或已执行的本地复现；**INFERENCE** 表示由源码推导的影响；**UNKNOWN** 表示本次没有实测。

- 已阅读 `AGENTS.md`、脚本生成 skill、公共导出、SDK/CLI/session/catalog、协议客户端关键路径、旧/新日志实现、Web 路由与任务管理、相关测试、打包配置、操作/设计文档及 SDD 合同。
- 未访问任何真实 SSH、Telnet、FTP、HDFS 或串口目标，未读取或输出用户凭据；没有重复执行全量协议测试。
- 母任务提供的最终验证证据：全量离线回归 **554 passed、2 skipped、1 warning，51.30s**。两项跳过为 Windows 符号链接创建权限，warning 为已有重复 ZIP 成员 fixture；审查修复已纳入最终全量。
- 母任务提供的最终合成性能证据：8 个文件、12,271,816 字节，48,000 条驱动及 48,000 条固件正文，4 worker 解析/索引 6.281s；960 个关键词命中，首/次页 0.0841/0.0205s；索引 62,832,640 字节。未测峰值内存、GB 级包或真实日志样本。
- 本评审独立执行了三项非网络复现：微秒排序、来源标识分隔符碰撞、并发配置保存。临时产物均位于被忽略的 `logs/`，复现后清理。
- 修订后独立执行 `python -X utf8 -m pytest -q tests/test_product_logs.py -k 'microsecond or index_time_format or provenance or ambiguous_relative_rename' --basetemp logs/pytest-audit-regression`，结果 **4 passed、24 deselected，1.30s**。

## 现有优势

| 方面 | 判断及依据 |
| --- | --- |
| 常用接口 | `init_script/get_remote_info/FileHandle` 使脚本主体接近普通 Python；`run` 与 `execute` 明确区分直线失败和显式恢复。见 `autoenv/remote_environment.py`、`scripts/example.py`。 |
| 静态发现 | `autoenv/script_files.py:24` 使用 AST 读取声明，不执行脚本来生成表单；角色、白名单、参数和历史校验与 CLI 共享，减少 UI/脚本执行的语义偏差。 |
| 协议复用 | 新对象适配已有 SSH/Telnet/FTP 客户端；没有为了新接口另写传输栈。Telnet 未知退出状态保留为未知，retry 不重放升级/重启命令。 |
| 可观测性 | 当次源码快照、操作编号、完整 `run.log`、简洁 `output.log`、退出码和最终失败结果有明确对应；Web 读取真实 CLI 结果。 |
| 测试可信度 | 本地协议服务使用真实 TCP 与生产传输客户端，验证完整文件字节；主测试进程及关键 CLI 子进程均有 loopback 守卫。见 `tests/conftest.py`、`tests/test_cli_protocol_flow.py`。 |
| 本地文件安全 | `autoenv/local_archives.py` 校验路径、大小/数量、链接、特殊文件、重复/大小写冲突和压缩校验，临时目录完整成功后发布；覆盖正常 PAX 及异常扩展头。 |
| 产品日志独立性 | `product_log/` 与旧远端 LogCollection 分离符合不同语义：跨文件 seg 与 Cpu/Dev/Type 专用模型，不强塞进旧 Slot/Socket API；CLI/Web 共用 workflow。 |
| 顺序和来源 | worker 只解析，合并器恢复文件/行顺序后判定 dump 归属；时间视图与完整拼接视图分开，保留重复正文、缺段诊断、原文及来源行号。 |
| 查询与 UI | 参数化 SQLite、ready 守卫、ID 查找、分页、流式导出、请求取消和有界虚拟列表均已实现；新任务管理器与旧单进程运行器隔离。 |
| 交付可追踪性 | feature 记录、详细设计、接口目录、操作说明和构造日志测试一起维护，且明确本地模拟不等于真实设备验收。 |

## 优先建议

### ISSUE-REVIEW-001 · P1：Web 缺少浏览器来源边界

**FACT，静态证据。** `webPage/server.py:446` 直接按长度解析 JSON，未检查请求 Content-Type；`Handler.do_POST()` 在 `:548` 没有 Origin、Host 或会话令牌校验。产品日志 JSON 接口同样直接解析。历史 `/api/agent/start` 在 `webPage/server.py:629` 接收任意 command，再经 `:291` 启动 `cmd.exe` 并写入该命令。页面不展示 Agent 入口，并不意味着该 HTTP 能力被关闭。

**INFERENCE。** 外部网页可构造 `text/plain` 的简单 POST 请求，绕过 JSON 类型通常触发的跨域预检而触发副作用。CORS 不允许读取响应，不等于禁止发送请求；仅绑定 loopback 也不能证明请求来自 AutoEnv 页面。实际浏览器的本地网络防护和 DNS rebinding 场景尚未做攻击验证，不能声称已成功利用。

建议先在唯一 HTTP 边界验证预期 Host，并拒绝不属于当前固定 Web origin 的浏览器请求；JSON 接口只接受明确 JSON 类型。随后为副作用接口加入由本次服务实例产生的令牌。对 CLI/本机程序等无 Origin 客户端单独定义令牌契约，不能用“缺少 Origin 即信任”形成旁路。未展示的 Agent/Settings/旧上传能力宜默认关闭或显式启用，再决定保留哪些能力。固定启动地址无需改变。

验收重点是恶意 Origin、错误 Host、`text/plain` JSON、缺令牌请求均被拒绝，正常页面/上传/CLI 桥接仍通过；先使用无副作用测试路由验证边界。

### ISSUE-REVIEW-002 · P2：配置保存不是完整事务

**FACT，已复现。** `autoenv/remote_catalog.py:83` 的 `save_connection()` 在 `:89` 读现有目录、合并后在 `:96` 原子替换。原子替换保证单个文件不会写一半，不能防止两次 read/merge/write 丢失更新。Web 使用 ThreadingHTTPServer，具备产生并发请求的条件。

本地受控线程交错：两个调用都读取旧快照，随后 A 写入、B 写入；两个调用均无错误返回，最终仅有 B：

```text
saved_without_error: ['A', 'B']
persisted_names: ['B']
both_saved: False
```

首次直接同时替换还出现过 Windows `WinError 5`；具体错误时序受 OS 影响，丢失更新问题本身由 read/merge/write 结构及上述交错证明。

建议按 topology 对整个读取、合并、发布加锁；若允许多个进程修改，则再加跨进程锁或 revision 冲突检查。先做受控并发回归，确认两个不同连接均保留，再增加更新同名连接的冲突策略。不要只重试 `os.replace` 来掩盖问题。

### ISSUE-REVIEW-003 · P2：旧脚本启动/停止缺少生命周期事务

**FACT，静态证据。** `webPage/server.py:108` 的 `start_script()` 先 stop，再短暂加锁重置 generation，随后锁外 Popen 并赋值 `self.process`。旧 `ProcessSession.start()` 在 `:48` 也采用这个结构；`stop()` 在 `:90` 读取并清空 process，没有覆盖启动全过程的互斥。

**INFERENCE。** 两个同时 start 请求可各自通过 stop，再产生两个子进程，最后只登记其中一个；旧 generation 的事件被丢弃，用户可能看不到仍在运行的脚本。此并发时序本次未以真实子进程复现。停止也只终止直接 CLI 进程，没有证明其继承子进程已结束，更不能保证已经发送的远端命令撤销。

建议像新的 `product_log.web.TaskManager` 一样，让检查 busy、预留 generation、创建/登记进程和 stop 由同一生命周期状态机控制。优先明确拒绝重复启动，不隐式终止前一个设备流程。随后在 Windows 上核实子进程树生命周期，并在操作文档说明本地停止与远端命令停止的实际区别。

### ISSUE-REVIEW-004 · P2：产品日志缺少总量预算与保留策略

**FACT，静态证据。** `product_log/web.py:26` 的 20 GiB 上限针对单次上传，`:122` 可创建多个上传；只有 `:210` 的显式上传取消删除目录。完成/失败/已领取的上传及 batches 持续保留，历史加载 `:101` 会读入全部 manifest。没有全局磁盘配额、过期回收或批次删除入口。

索引同时保留 text/raw_text/search_text，本轮 12.27 MB 合成输入生成 62.20 MB 索引，说明展开字节上限不能代表最终磁盘预算。关键词 ID 缓存最多 12 个查询、每个 500,000 命中，虽有逻辑上限，SQLite 删除行也不自动等于缩小文件体积。

**INFERENCE。** 反复拖包、浏览器中途关闭和多批次历史积累会持续占用磁盘；GB 级日志、异常文本和索引构建的最终峰值尚未实测。

建议首先显示任务磁盘用量、剩余空间和批次保留位置，提供显式删除已结束批次及清理未领取上传的能力；删除前验证绝对路径始终位于管理根，禁止删除活动任务/引用中的上传。随后设置全局预算、过期上传回收和用户可选择的历史保留策略。完整 ready 批次不要在用户不知情时静默删除。

### ISSUE-REVIEW-005 · P2：大文件性能仍有具体未覆盖路径

**FACT，静态证据。** 新 parser 在 `product_log/parser.py:116` 按二进制行迭代，单行大小没有限制；`get_source()` 在 `product_log/index.py:398` 用 `islice(enumerate(stream), offset, ...)`，每次定位末尾行仍从文件起点扫描。外部目录的文件数/总字节没有与压缩展开相同的预算；扫描和 FileHandle 初始摘要阶段也没有处处传递取消回调。

旧日志接口开销更明显：`autoenv/log_query.py:166` 的关键词/时间查询先 fetchall 整个 target，再在 Python 过滤；`:362` 导出也 fetchall 并构造完整字符串。旧 `autoenv/logs.py:1371` 缓存整份解析行。这些路径仍通过高级后端 API 暴露，新页面的分页并未修复旧能力。

**INFERENCE。** 超长单行会使一次读取/响应占用大内存，末尾来源点击随文件变大而变慢，旧高级日志在大 target 上可能阻塞或耗尽内存；本次没有测得 OOM 或 GB 级响应时间。

建议先补一组具有工程意义的规模验收：大单文件、长行、末尾来源定位、频繁更换关键词及取消耗时，并记录峰值内存/磁盘。依据结果选择行长度预算或异常行分块、稀疏行号→字节偏移索引、输入总量预算。旧高级接口若继续支持大日志，应改 SQL 过滤和流式导出；无需把两个领域解析器强行合并。

### ISSUE-REVIEW-006 · P2：远端主机身份校验策略不足

**FACT，静态证据。** `autoenv/ssh_host.py:1193` 无条件采用 Paramiko `AutoAddPolicy()`；该路径没有加载 known_hosts 或对登记指纹做校验。连接成功和密码鉴权成功无法单独证明连接的是登记设备。

同类历史边界在 HDFS 客户端也存在：`autoenv/package_manager.py:55` 的默认 `verify_ssl=False`，`:74` 全局关闭 InsecureRequestWarning；`:145` 的默认 PackageManager 直接构造该客户端。HTTPS 路径没有默认验证证书，且普通 `hdfs_download` 没有提供证书/校验策略入口。HTTP loopback 模拟不能验证 HTTPS 身份或真实集群认证。

**INFERENCE。** 在地址复用、目标 IP 登记错误或网络中间人场景，可能把认证和命令发给错误主机。没有进行真实网络攻击或真实设备指纹验证。

建议集中登记增加可选的 known_hosts/固定指纹策略，重连复用同一策略；HDFS 增加明确的证书验证/CA 配置，避免全局抑制无关请求的警告。实验室动态设备需要宽松模式时应明确配置，而非永远隐式信任。不能直接改变默认策略导致所有已登记设备突然不可连接，也不应把当前允许明文环境密码的既定需求当成待擅自推翻的设计。

### ISSUE-REVIEW-007 · P3：公共接口和文档存在持续的双轨维护成本

**FACT。** `autoenv/__init__.py` 同时导出普通脚本 API、旧 decorators/selectors 和旧日志模型；`registry.py/runtime.py/interface.py` 与 `script_files.py/script_session.py/cli.py` 有各自的发现、参数、生命周期和结果逻辑。Web 保留 Tools/Agent/旧日志路由，主页面使用新的简洁流程。当前文档已说明这种边界，保留旧能力不是本身的错误。

建议列一张能力归属表：推荐新入口、仍支持高级入口、仅历史记录、待停用能力；给高级能力明确维护负责人或兼容承诺。后续修改先确认影响哪一条链，避免为了“复用”把两种执行语义互相混用。可以逐步将旧能力集中在兼容 namespace，保留原导出直到有迁移依据。

README/QUICK_START/详细设计/接口 Markdown/接口 JSON/skill/feature 记录重复陈述相同签名，维护负担已经明显。建议接口签名与默认值保留一个权威来源，其他文档只写场景和链接；用现有 AST/inspect 做机械一致性检查即可，不需要引入完整文档构建框架。SDD skill 是可用流程工具，当前 repo 并没有全部采用其完整 manifest/ADR/Gate 布局；不要把“技能存在/validator 通过”表述成项目每项需求均完成正式 SDD 基线批准。

### ISSUE-REVIEW-008 · P3：验证环境、发布契约及真实样本需要分层固定

**FACT。** `pyproject.toml` 依赖主要为最低版本约束，未见仓库级 constraints/lock 或 CI 配置。本轮核心协议证据使用 Paramiko 3.5.1 和 Python 3.11；不表示所有满足 `>=3.0` 的版本已验证。wheel 已包含 `product_log` 及静态资源；Web 启动脚本和 `webPage` 仍是源码仓库工作方式，不能把包含静态资源等同于完整 Web wheel 安装验收。

建议保留宽范围库依赖声明，同时发布一份当前已测开发 constraints；在 Windows/Python 3.11 与一个受支持的新版本上执行现有离线协议、CLI、wheel 资源检查。区分 SDK 安装、独立产品日志 CLI 和源码仓库 Web 的支持方式，避免增加第二个 Web 启动器。

日志测试构造覆盖已经很好。下一步应由用户提供脱敏真实样本，覆盖实际轮转名、不同外层头、设备重启、截断末段、GB18030 变体和多个设备来源；保留预期结果作为版本化 fixture。未来配置可选解析规则时，先证明样本需要，避免泛化成任意正则平台。

## 本次评审发现后的收尾项

### ISSUE-REVIEW-009 · P2：微秒排序截断（已修复，聚焦回归通过）

初始源码的日期正则接受 1..6 位小数，但排序值截断到毫秒。本地真实 workflow/query 复现：`a.log` 时间 `.383900`、`b.log` 时间 `.383100`，默认 time 返回 900→100。随后实现已改为整数微秒，并给 manifest 添加索引版本和时间单位、查询检查不兼容索引；新增微秒排序及过滤回归。当前对应 `product_log/parser.py:42`、`product_log/index.py:240`、`tests/test_product_logs.py:65`。

独立聚焦回归及母任务最终全量均已通过。毫秒索引不会被微秒查询静默沿用，不兼容的已完成批次要求重导入。

### ISSUE-REVIEW-010 · P2：展示路径不能作为机器来源标识（已修复，聚焦回归通过）

本地输入同时含 `payload.zip`（成员 `file.log`）和合法目录 `payload.zip!/file.log`，两个来源都得到展示字符串 `payload.zip!/file.log`。初始 `product_log/workflow.py:230` 用该字符串 hash 生成 source_id，真实 workflow 报 `IntegrityError: UNIQUE constraint failed: sources.source_id`；selected 目录和按展示路径重命名也可能有同类歧义。

当前修订使用 `provenance_parts` 分别表示裸来源 `["payload.zip!/file.log"]` 和归档来源 `["payload.zip", "file.log"]`，以 JSON 身份生成 source_id、selected 目录及最终排序 tie；`!/` 仅用于展示。相对重命名键遇到这种歧义会明确报错。见 `product_log/workflow.py:47`、`:217`、`:230`、`:244`。回归构造两类来源，确认两份均被复制、来源 ID/目标路径唯一，并检查歧义重命名拒绝。

独立聚焦回归及母任务最终全量均已通过。

## 建议落实顺序

1. **交付当前功能时**：关闭微秒和来源标识两个新问题，记录最终本地结果及真实样本未知项；保持独立模块和唯一 Web 入口。
2. **下一轮先处理运行边界**：Web Host/Origin/Content-Type/令牌与隐藏 Agent 能力；配置 read/merge/write 锁；脚本 start/stop 生命周期互斥。这几项范围小、影响明确，优先于抽象重构。
3. **随实际日志规模补资源治理**：显式历史清理、上传回收、总磁盘预算及内存/取消/末尾来源测量。再针对瓶颈修改旧全量查询或新来源定位。
4. **在扩大使用前**：SSH 主机身份策略、可复现依赖/CI、脱敏真实样本；整理能力归属和文档权威来源。

前三项后续建议是：**先补 Web 来源边界；再修历史配置/脚本的并发事务；再补产品日志磁盘保留与资源预算。** 当前优势应保留：普通 Python、真实协议测试、明确失败语义、完整证据、来源顺序与时间视图分离，以及独立本地日志领域模型。

## 本轮使用约束与用户取舍

2026-10-11 补充。以上保留独立评审当时的事实、推论与建议；本段记录用户随后确认的范围，作为本轮实施计划。**不采纳改造不等于原问题已修复**，也不将个人本地工具扩大为多人服务。

使用前提为本人本地操作、同一时刻只运行一个任务、设备环境由本人管理。程序内部的 HTTP/解析线程实现保持原样；用户所说的单线程是单任务使用约束，不是改写全部内部线程。

| 建议 | 本轮决定 |
| --- | --- |
| 004 磁盘保留 | 采纳：产品日志 Web 管理的上传与批次达到 3 GiB（`3 * 1024**3` 字节）后触发自动清理，按最旧顺序回收已结束批次与可回收上传，保护活动/引用中的内容。具体规则以 [产品日志说明](../product_log/README.md) 为准；外部输入、独立 CLI 输出和普通脚本日志不纳入。阈值不是硬配额。 |
| 007、008 文档与环境说明 | 采纳：建立 [文档地图](DOCUMENTATION_MAP.md)，明确普通脚本/CLI、源码 Web、独立产品日志和已有高级 API 的边界；减少入口文档重复。增加已测直接依赖 [constraints](../constraints-local-tested.txt)，不宣称完整锁定或跨版本/跨平台验收。 |
| 008 真实日志 | 本地使用不要求先脱敏；真实日志保留本机，不提交 Git。本轮不以提供真实日志或新增多平台 CI 为完成条件。构造样本的已验证范围保留，真实格式验收不凭空补写。 |
| 001 Web 来源保护 | 用户明确不采纳，本轮不增加 Host/Origin/令牌或高级路由关闭策略。原评审的静态事实保留。 |
| 002 配置并发事务 | 用户明确不采纳，本轮不增加跨线程/进程保存事务。 |
| 003 脚本启动停止治理 | 用户明确不采纳，本轮不改旧运行器的启动停止行为。 |
| 005 大文件性能 | 用户明确不采纳，本轮不增加性能专项、偏移索引或旧日志查询重构。 |
| 006 远端身份校验 | 用户明确不采纳，本轮保留现有 SSH/Telnet/HDFS 认证和信任策略，不增加设备指纹或证书策略配置。 |
| 009、010 | 已修复，仍按上文回归证据记录，不与本轮主动不采纳项混淆。 |

本轮重点是 **3 GiB 自动清理与文档归属整理**。后续重新考虑未采纳项，应由用户使用方式变化或明确需求推动；原评审的建议排序不作为自动追加任务的授权。
