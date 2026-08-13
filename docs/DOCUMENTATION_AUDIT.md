# 文档一致性审计

> 审计基线：`UNIFY_ENV_WITH_BLOCK` 工作树，2026-08-13
> 结论：公共实现、入口、测试和项目 skill 已建立对应文档；真实外部系统仍未验收。

## 1. 文档职责

| 文档 | 唯一职责 | 主要读者 |
|---|---|---|
| `README.md` | 当前能力、安装、主入口和文档导航 | 首次访问者 |
| `docs/QUICK_START.md` | CLI/脚本最短操作和本地验证路径 | 环境脚本开发者 |
| `docs/ENVIRONMENT_REGISTRATION_GUIDE.md` | 环境脚本公共 API、参数、结果和检查清单 | 脚本作者 |
| `docs/AutoEnv-Refactor-Detailed-Design.md` | 顺序执行核心设计与 BLOCK 扩展边界 | 框架维护者 |
| `webPage/QUICK_START.md` | Web 四页签、非交互入口、Tool/Agent 导入操作 | Web 用户 |
| `docs/WEB_ARCHITECTURE_AND_HANDOFF.md` | Web/结构化入口的数据流、安全边界和待开发项 | 接手开发者/Agent |
| `tests/README.md` | fake/mock 范围、UT 到实现映射和排错 | 测试维护者 |
| `.agents/skills/*/SKILL.md` | AutoEnv 脚本、Web Tool 和导入流程约束 | Agent |
| `sdd/README.md` 与 `sdd/*/SKILL.md` | 通用底软 SDD 流程、模板和门禁 | 跨项目开发者/Agent |

`frontend/` 已明确标记为历史原型；当前 Web 只在 `webPage/` 演进。

## 2. 实现到文档/测试映射

| 当前实现 | 公共入口 | 说明文档 | 离线证据 |
|---|---|---|---|
| WebHDFS 包下载/newest | `RunContext.download_package()` | 注册指南、详细设计 | `test_package_extractor.py`、运行时 UT |
| SCP/SFTP 上传与下载 | `SSHHost.scp_*` / `sftp_*` | 注册指南 §9/§10/§22、Web 接手说明 §3 | `test_ssh_host.py` |
| 独立 FTP 上传 | `RunContext.register_ftp_host()` / `FTPHost.upload()` | 注册指南 §23、Web 接手说明 §3 | `test_interfaces_web_ftp.py` |
| RUN/TAR/ZIP 安全提取 | `RunContext.extract_file_from()` | 详细设计、Web 接手说明 §4 | `test_package_extractor.py` |
| JSON/直接参数非交互启动 | `adapt_interface.py` / `LaunchRequest` | Web 快速入门、Web 接手说明 §2 | `test_interfaces_web_ftp.py` |
| 环境档案和脚本拉起 | `startWeb.py` / `/api/environments` / `/api/runs` | Web 快速入门、Web 接手说明 §1/§2 | API 冒烟 + 接口 UT |
| 动态 Tools | `register_web_tool()` | Web 快速入门、Web 接手说明 §5 | `test_interfaces_web_ftp.py` + tool validator |
| Agent CLI 路径转换 | `/api/agent/*` | Web 快速入门、Web 接手说明 §6/§7 | 上传边界 UT + 本机冒烟 |
| 日志下载/解压/解析实例 | `download_and_parse_logs` | Web 接手说明 §4 | 脚本静态契约；解析规则明确待样例 |

## 3. 本轮修正

1. 将原始 `UNIFY_ENV` 设计与当前 `UNIFY_ENV_WITH_BLOCK` 扩展分层说明，修复“FTP 不支持”与当前实现的表面矛盾。
2. 把追加的一级标题整理为原文档的正式章节，补齐相对链接。
3. README 能力清单补入 SCP/SFTP 下载、ZIP、FTP、结构化入口和 Web/SDD 导航。
4. 测试总览补入 fake 下载/FTP、接口/Web/导入 UT，明确离线证据边界。
5. 将 `frontend/` 标为历史原型，并在接手文档集中列出 Agent CLI、日志解析和错误码 Tool 的已知限制。
6. 示例 LaunchRequest 标明是结构占位，运行前必须替换真实环境或参数。

## 4. 一致性检查规则

- 公共名称或语义变化后，用全仓搜索检查实现、导出、示例、README、指南、详细设计、测试说明和项目 skill。
- Markdown 相对链接必须存在；新增顶层能力必须进入 README 文档导航。
- 离线 fake/mock 结果只写“契约通过”，不得写“真实设备验证通过”。
- 待业务输入的功能保留明确 TODO、输入契约和接手位置，不猜测规则。
- 历史设计必须标注时间/分支语境；当前文档不能同时宣称同一能力“支持”和“不支持”。

## 5. 尚未由本审计证明

- 未连接真实 HDFS、SSH、SFTP、SCP、FTP 或 Telnet 环境。
- 未用真实 `codeagent`/`nga` 验证全部 TUI 控制序列；当前已使用 Windows ConPTY，并在离线 UT 中验证分块输出、回车覆盖与 ANSI 控制序列透传。
- 未实现业务日志块 pattern 和错误码解释规则，因为缺少用户样例/规则。
- 明文密码是已确认项目取舍，不代表适合公网或多用户部署。
