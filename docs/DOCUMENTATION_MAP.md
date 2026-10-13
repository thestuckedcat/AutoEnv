# 文档入口与能力边界

全部文档及配套资料的逐项清单见根目录 [README 文档总指引](../README_GUIDE.md)。本页负责按任务选择入口及明确文档归属。

AutoEnv 当前用于个人、本机、单任务操作。常用入口是普通 Python 脚本、同一套 CLI 和本地网页；产品日志是独立本地模块。维护文档时先按下面的归属找到说明，不把旧高级组件的执行方式混入普通脚本。

## 按要做的事阅读

| 要做的事 | 入口 | 主要说明 |
| --- | --- | --- |
| 安装并运行第一个脚本 | `scripts/*.py` → `python -m autoenv.cli` | [快速开始](QUICK_START.md)；[最小示例](../scripts/example.py)、[可复制模板](../scripts/template.py) |
| 登记或修改 SSH、Telnet、FTP 连接 | `environments/topologies/<组网>/connections.json` | [环境登记](ENVIRONMENT_REGISTRATION_GUIDE.md) |
| 拉起 UDK V100 环境或替换固件 | `scripts/UDK_V100.py`、`scripts/UDK_V100_Firmware.py` | [UDK V100 工作流](UDK_V100_WORKFLOWS.md)，含共享组网、包配置、串口判据与模拟测试 |
| 查普通脚本可用接口 | `autoenv` 公共导出及环境对象 | [接口说明](SCRIPT_API_ANALYSIS.md)；[完整用法示例](../scripts/all_interfaces.py) |
| 在网页登记环境、运行脚本 | `python -X utf8 startWeb.py` → <http://127.0.0.1:8765/> | [网页用法](web_usage.md) |
| 整理并查看用户态、内核态和固件日志 | `python -m product_log` 或同一服务的 `/product-logs/` | [产品日志说明](../product_log/README.md)，包括归档全量准备、diag/varlog 收集、分段拼接、模块表、分类/统一时间、查询和自动清理 |
| 生成可直接打开的单文件离线日志网页 | `python -X utf8 tools/build_product_log_page.py` → `dist/product-log-offline.html` | [独立离线页说明](../product_log/standalone/README.md)；生成仅用 Python 标准库，使用者只需要现代浏览器 |
| 修改 Web/CLI 桥接 | `webPage/server.py` 与 CLI/session | [Web 接手说明](WEB_ARCHITECTURE_AND_HANDOFF.md) |
| 运行离线测试、构造日志或排查失败 | `tests/` | [测试说明](../tests/README.md)；[本地协议模拟](LOCAL_SIMULATION.md) |

Web 只支持源码仓库根目录中的 `python -X utf8 startWeb.py`，固定地址为 `http://127.0.0.1:8765/`。SDK 与 CLI 可通过项目安装使用；wheel 包含 `autoenv`、`scripts`、`product_log` 及产品日志静态资源，但不提供独立 Web 启动器。安装 wheel 不等于完整源码 Web 的安装验收。

单文件离线日志页是源码构建产物，由浏览器直接运行全部内嵌代码；构建命令不启动服务器，也不改变 wheel 安装或 SDK/CLI/服务端入口。ZIP LZMA（算法 14）及 RAR4/RAR5 普通和固实压缩的解码器、WASM 和许可均随 HTML 内嵌，运行不加载网络资源。输入经文件选择授权，来源为相对路径，结果只存页面内存；模块表用 JSON 导入/导出，磁盘解压、绝对路径导入、历史批次和 3 GiB 回收仍属于服务端版本。

## 哪份文档负责什么

实现、公共导出和对应测试是当前行为的最终依据；文档冲突应修正文档。以下是各类说明的维护入口，不是多份相互覆盖的规范。

| 内容 | 权威说明/维护入口 | 其他材料的定位 |
| --- | --- | --- |
| 安装和最短上手流程 | [QUICK_START.md](QUICK_START.md) | 根 README 只提供入口；[webPage/QUICK_START.md](../webPage/QUICK_START.md) 导向网页用法 |
| 普通脚本及文件 SDK 的 API 名称、参数和返回语义 | [SCRIPT_API_ANALYSIS.md](SCRIPT_API_ANALYSIS.md)，对应 `autoenv/__init__.py`、`script_session.py`、`remote_environment.py`、`file_handles.py` | [机读目录](script-api-catalog.json) 是同步摘要；示例展示调用场景，不另定义默认值 |
| 产品日志导入、解析、查询与保留规则 | [product_log/README.md](../product_log/README.md)，对应 `product_log/` | 当前全量归档准备、默认 varlog、旧 var 兼容及本机 RAR 后端边界以此 README 为使用入口；[日志设计书第 12 节](LOCAL_PRODUCT_LOG_DESIGN.md#12-diag-用户态var-内核态与模块映射已确认实现) 保留此前 diag/var 设计，不覆盖后续规则；第 1–11 节保留初版通用设计 |
| 单文件离线日志页构建、使用和边界 | [product_log/standalone/README.md](../product_log/standalone/README.md)，对应 `tools/build_product_log_page.py` 与 `product_log/standalone/` | [日志设计书第 13 节](LOCAL_PRODUCT_LOG_DESIGN.md#13-单文件离线日志网页) 保留浏览器数据流和 Python 对照设计；新增格式与解码资源以当前使用说明和清单为准 |
| 连接配置、类型、白名单和选择规则 | [ENVIRONMENT_REGISTRATION_GUIDE.md](ENVIRONMENT_REGISTRATION_GUIDE.md) | 快速开始和网页用法仅保留操作步骤 |
| Web 操作与桥接实现 | [web_usage.md](web_usage.md)、[WEB_ARCHITECTURE_AND_HANDOFF.md](WEB_ARCHITECTURE_AND_HANDOFF.md) | 分别面向使用者和接手开发者 |
| 依赖声明与已测环境快照 | [pyproject.toml](../pyproject.toml)、[constraints-local-tested.txt](../constraints-local-tested.txt) | 下述环境说明解释验证边界；历史测试次数不代表全部版本兼容性 |
| 测试方式和真实协议证据 | [tests/README.md](../tests/README.md)、[LOCAL_SIMULATION.md](LOCAL_SIMULATION.md) | [newFeature/](../newFeature/) 记录各次变更的实际验证 |
| 设计决定与历史评审 | [重构约定](REFACTOR_PLAN_FOUR_API_CATEGORIES.md)、[实现设计](AutoEnv-Refactor-Detailed-Design.md)、[项目评审](PROJECT_REVIEW_2026-10-11.md) | 具有时间语境，不覆盖后续实现或用户取舍 |

公共接口变化时，同时核对导出、对应测试、接口说明和机读目录；新增场景优先写在负责该功能的说明中，其他入口加链接。保留现有轻量 Markdown，不增加文档构建系统。

## 现有高级能力

| 能力 | 当前定位 | 修改边界 |
| --- | --- | --- |
| 普通脚本、`init_script/get_remote_info`、CLI run/rerun | 推荐的环境操作路径 | 静态发现、绑定与 session 是同一执行链；网页调用真实 CLI |
| `FileHandle` 与独立 `product_log` | 推荐的本地文件和产品日志路径 | 只复用本地 SDK，不需要设备登记，也不借用旧远端日志模型 |
| 旧 RunContext、decorators、selectors、低层协议类与结果模型 | 保留给已有高级组件；公共导出未移除 | 修改前检查相关旧 SDK 测试，不将旧注册方式作为新普通脚本的默认入口 |
| 旧日志采集/提取/查询、Tools、Agent/ConPTY 后端 | 保留已有 SDK/API，常用主页不展示这些模块 | 独立后端契约仍存在；主页不展示不代表 API 已停用，不与新产品日志任务模型混用 |
| `newFeature/`、旧设计/评审与 SDD 材料 | 变更记录、设计背景和可选流程资料 | 不是额外执行入口；skill/validator 可用不代表整个项目经过完整 SDD 基线批准 |

本轮没有停用或迁移高级 API，也没有增加兼容 namespace。后续是否收拢应由实际使用需求决定。

## 本地环境与数据

项目声明 Python 3.11+。当前已测本地环境为 Windows、Python 3.11.4，直接依赖版本见 [constraints-local-tested.txt](../constraints-local-tested.txt)。需要沿用该快照时，在仓库根目录执行：

```powershell
python -m pip install -c constraints-local-tested.txt -e ".[test]"
```

该文件只约束本次已测的直接依赖，不是包含全部传递依赖的锁文件，也不表示已完成洁净安装、所有 Python/依赖组合或多平台 CI 验收。`pyproject.toml` 继续保留支持范围声明。

真实日志可以直接在本机使用，本项目不把脱敏作为本地解析的前置条件。真实设备日志、账号配置和运行产物保留在本机，不提交 Git；建议把样本放在已忽略的 `logs/`。仓库中的测试使用构造日志，样本生成和预期数量见 [产品日志测试说明](../product_log/README.md#测试)。

产品日志 `auto/product/generic` 都先递归准备输入中的全部支持压缩包，再定位目录和选择日志。默认内核目录为 `varlog`，仅缺失时回退到旧 `var`，显式选择旧 `var` 的构造样本仍有效；旧 archive_patterns 参数保留校验但不限制准备阶段。文件 SDK 的搜索/排序合同不变，`detect_archive()` 增加 `rar`；解压边界见 [产品日志说明](../product_log/README.md#输出与读取规则)。当前索引版本为 2，旧产品日志批次需要重新导入；构造样本与核心/收集测试见 [测试说明](../tests/README.md)，RAR 固定样本来源见 [归档测试夹具](../tests/fixtures/product_log_archives/README.md)。

归档格式与常见串口兼容的实现及本地验证范围见 [2026-10-13 功能记录](../newFeature/2026-10-13-archive-and-serial-compatibility.md)。串口的 command/keyword/prompt 自检范围与 `exit_status_available` 由 [接口说明](SCRIPT_API_ANALYSIS.md) 和 [UDK 工作流](UDK_V100_WORKFLOWS.md) 维护；提示符就绪不等于业务命令已获得退出码 0。验证未访问真实串口、BMC、1260 或 HDFS。

产品日志 Web 的上传与历史批次达到默认 3 GiB（`3 * 1024**3` 字节）时触发自动清理，具体顺序和保护规则以 [产品日志说明](../product_log/README.md) 为准。它只管理 Web 专属目录，CLI 指定输出、外部输入目录和普通环境脚本日志由使用者管理。阈值是清理触发条件，不是磁盘硬配额。
