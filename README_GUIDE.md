# AutoEnv 文档总指引

更新时间：2026-10-13。本文集中列出项目维护的说明、设计、评审、功能记录、技能、模板和配套参考文件；点击名称即可进入对应资料。

本文负责完整文档清单；[文档入口与能力边界](docs/DOCUMENTATION_MAP.md) 负责说明各场景入口和文档权威归属。实际行为以当前代码和测试为准，历史记录中的旧接口或旧启动方式不替代当前使用说明。运行日志、临时样本、测试依赖、构建产物及第三方包源码不纳入文档清单，第三方许可单独列出。

## 先按用途选择

| 要做的事 | 从这里开始 |
| --- | --- |
| 首次安装、运行普通 Python 脚本 | [快速开始](docs/QUICK_START.md) → [环境登记与脚本编写](docs/ENVIRONMENT_REGISTRATION_GUIDE.md) → [脚本接口](docs/SCRIPT_API_ANALYSIS.md) |
| 拉起 UDK V100 环境、替换固件 | [UDK V100 两个工作流](docs/UDK_V100_WORKFLOWS.md)，共用 SingleUnions_S3 |
| 通过网页登记环境、运行脚本 | [网页用法](docs/web_usage.md)；实现接手读 [Web 架构](docs/WEB_ARCHITECTURE_AND_HANDOFF.md) |
| 在本地 CLI 或服务网页解析产品日志 | [产品日志使用说明](product_log/README.md) |
| 用一个 HTML 离线解析日志 | [独立离线日志网页使用文档](product_log/standalone/README.md)，含模块表、搜索、导出和样本 |
| 运行测试或复现协议问题 | [测试与排查](tests/README.md) → [本地模拟与实测结果](docs/LOCAL_SIMULATION.md) |
| 查看项目评审与设计决定 | [全项目评审](docs/PROJECT_REVIEW_2026-10-11.md)、[重构设计](docs/AutoEnv-Refactor-Detailed-Design.md)、[产品日志设计](docs/LOCAL_PRODUCT_LOG_DESIGN.md) |
| 让 Agent 生成脚本或扩展 Web Tool | 下方“项目 Agent 技能与合同” |
| 了解 SDD 方法、技能或模板 | [SDD 技能包说明](sdd/README.md) → [统一过程契约](sdd/common/PROCESS_CONTRACT.md) |
| 查某次功能的变更和验收证据 | 下方“功能与历史变更记录” |

普通服务网页保持唯一入口 `python -X utf8 startWeb.py`，固定地址 `http://127.0.0.1:8765/`。独立离线 HTML 由 `python -X utf8 tools/build_product_log_page.py` 生成，不启动服务；其结果仅在浏览器内存中，具体边界见对应使用文档。

## 基础入口与当前使用说明

| 文档 | 内容 |
| --- | --- |
| [项目 README](README.md) | 项目介绍、安装、最小脚本、CLI/Web 和产品日志入口 |
| [本文：README_GUIDE.md](README_GUIDE.md) | 全部文档清单及阅读路径 |
| [文档入口与能力边界](docs/DOCUMENTATION_MAP.md) | 常用/高级能力归属、文档权威来源、安装和本地数据边界 |
| [快速开始](docs/QUICK_START.md) | 安装、普通脚本与 CLI/Web 的最短操作流程 |
| [环境登记与脚本编写](docs/ENVIRONMENT_REGISTRATION_GUIDE.md) | 组网、连接配置、协议类型、绑定与脚本申请规则 |
| [UDK V100 环境拉起与固件替换](docs/UDK_V100_WORKFLOWS.md) | 包下载、共享组网、重启、占位启动脚本、串口登录/自检范围及固件退出码/业务判据 |
| [脚本接口与用法](docs/SCRIPT_API_ANALYSIS.md) | 当前公共 API、参数、返回值、文件操作和示例 |
| [Web 快速开始](docs/web_usage.md) | 页面操作、环境登记、脚本运行及产品日志入口 |
| [网页目录入口](webPage/QUICK_START.md) | 从 webPage 目录转到统一 Web 使用说明 |
| [本地产品日志](product_log/README.md) | Python CLI/服务网页的全量归档准备、diag/varlog 与旧 var 兼容、RAR 后端边界、三类日志及 3 GiB 回收 |
| [独立离线日志网页使用文档](product_log/standalone/README.md) | 单文件 HTML 导入、ZIP LZMA/RAR、模块 JSON、分类/统一时间、筛选、导出、样本和构建分发 |

## 实现设计、评审与文档维护

| 文档 | 内容与定位 |
| --- | --- |
| [四类接口重构计划与实施约定](docs/REFACTOR_PLAN_FOUR_API_CATEGORIES.md) | 重构决定、接口分类和第一版实施/验收约定 |
| [AutoEnv 第一版重构设计](docs/AutoEnv-Refactor-Detailed-Design.md) | 普通脚本静态发现、session、协议适配与日志/输出分离 |
| [Web 最简实现与接手说明](docs/WEB_ARCHITECTURE_AND_HANDOFF.md) | 唯一启动链、Web/CLI 桥、组件/API、产品日志服务和独立 HTML 边界 |
| [本地文件与产品日志设计书](docs/LOCAL_PRODUCT_LOG_DESIGN.md) | 初版及此前 diag/var、独立离线网页设计；当前默认目录、准备与格式边界见各使用 README |
| [全项目独立评审：2026-10-11](docs/PROJECT_REVIEW_2026-10-11.md) | 评审发现、证据、建议及用户后续取舍；不是全部建议已落实的声明 |
| [文档一致性检查](docs/DOCUMENTATION_AUDIT.md) | 文档职责、实现对应、公共接口修改后的检查方法和验证边界 |
| [仓库 Agent 指令](AGENTS.md) | 项目操作约束、适用技能、功能记录和相关文件检查要求 |

设计和评审保留其时间语境。查当前 API 用接口说明，查当前操作用使用文档；不要根据较早设计中的示例切换执行入口。

## 测试、模拟与日志知识库

| 文档 | 内容 |
| --- | --- |
| [本地协议测试与排查](tests/README.md) | 测试依赖、真实 loopback 协议、各测试文件范围、合成日志、验收结果及边界 |
| [本地模拟：搭建、命令与结果](docs/LOCAL_SIMULATION.md) | 本机协议服务搭建、CLI 流程、文件字节与日志证据；不代表真实设备/HDFS 已验收 |
| [产品日志归档测试夹具](tests/fixtures/product_log_archives/README.md) 与 [样本清单](tests/fixtures/product_log_archives/manifest.json) | 固定 RAR 构造样本、生成方式、原始字节摘要和测试覆盖 |
| [日志问题定位知识库](logKnowledge/README.md) | 组件资料、历史问题结论、证据和人确认后的维护规则 |
| [问题记录模板](logKnowledge/components/_template/issues/ISSUE_TEMPLATE.md) | 问题签名、组件边界、证据、排除项、处理和回归的填写模板 |

知识库 `_template` 是模板，不是已确认问题。本页仅导航，不新增或改变知识库结论。

## 项目 Agent 技能与合同

这些资料面向 Agent/开发维护者，普通使用者无需逐一执行。是否应用技能取决于任务和仓库规则。

| 文档 | 用途 |
| --- | --- |
| [快速生成 AutoEnv Python 流程](.agents/skills/autoenv-script-generator/SKILL.md) | 普通环境脚本生成、公共接口和静态合同要求 |
| [新增 AutoEnv Web Tool](.agents/skills/autoenv-web-tool/SKILL.md) | 本地 Web Tool 的注册、结构和验证流程 |
| [Web Tool 合同](.agents/skills/autoenv-web-tool/references/contract.md) | 工具参数、输入输出、运行与集成合同 |
| [导入 Python 工具](.agents/skills/import-python-web-tool/SKILL.md) | 把上传的 Python 文件/项目适配为 Web Tool |
| [Python 工具静态审查清单](.agents/skills/import-python-web-tool/references/review-checklist.md) | 导入前代码、依赖、文件操作和运行行为的检查资料 |
| [日志错误定位与组件定界](.agents/skills/log-error-triage/SKILL.md) | 用当前日志、代码及历史线索定位问题，结论写入需人确认 |

## SDD 方法、技能与模板

### 方法入口

| 文档 | 内容 |
| --- | --- |
| [底层软件 SDD 技能包](sdd/README.md) | 方法用途、技能入口、产物布局和验证边界 |
| [SDD 统一过程契约](sdd/common/PROCESS_CONTRACT.md) | 证据、追踪、状态、门禁建议与人批准的统一规则 |
| [方法参考](sdd/REFERENCES.md) | SDD 方法的参考来源 |

### 各阶段技能

| 文档 | 用途 |
| --- | --- |
| [完整 SDD 流程](sdd/sdd-process/SKILL.md) | 全生命周期编排 |
| [存量系统分析](sdd/analyze-existing-system/SKILL.md) | 从代码及证据建立系统说明、设计和规格 |
| [初始化 AGENTS.md](sdd/init-agents-md/SKILL.md) | 建立或修订仓库 Agent 工作约定 |
| [增量需求开发](sdd/develop-incremental-requirement/SKILL.md) | 增量设计、实现、验证与文档回写 |
| [架构评审](sdd/review-architecture/SKILL.md) | 审查架构及证据 |
| [SDD 检查点](sdd/run-sdd-checkpoint/SKILL.md) | 独立检查需求追踪及门禁条件 |
| [自评审与建议判决](sdd/self-review-and-verdict/SKILL.md) | 交付前自评审和建议结论 |
| [SDD 复盘](sdd/conduct-sdd-retrospective/SKILL.md) | 根据证据形成待人确认的流程改进提案 |

### 文档模板

| 模板 | 产物 |
| --- | --- |
| [基线清单](sdd/common/templates/manifest.template.md) | SDD manifest |
| [项目顶层设计](sdd/common/templates/project-design.template.md) | 顶层架构与设计 |
| [模块详细设计](sdd/common/templates/module-design.template.md) | 模块设计 |
| [功能规格](sdd/common/templates/feature-spec.template.md) | feature spec |
| [增量变更计划](sdd/common/templates/change-plan.template.md) | change plan |
| [架构决策](sdd/common/templates/adr.template.md) | ADR |
| [验证计划](sdd/common/templates/verification-plan.template.md) | 验证策略及证据计划 |
| [评审报告](sdd/common/templates/review.template.md) | 评审记录与建议判决 |
| [Agent 指令](sdd/common/templates/agents.template.md) | 仓库 AGENTS.md 模板 |
| [复盘提案](sdd/common/templates/retrospective-proposal.template.md) | 待人确认的流程修订提案 |

### 项目已有 SDD 资料

| 文档 | 定位 |
| --- | --- |
| [日志解析与浏览可用性规格](docs/sdd/specs/log-usability/spec.md) | 2026-08 的日志 metadata、strict block、虚拟浏览需求及验证约定 |
| [日志可用性增量计划](docs/sdd/changes/CHANGE-2026-08-18-LOG-USABILITY/change-plan.md) | 上述日志能力的增量变更计划与记录 |

这两份资料对应较早的日志能力，不替代后来独立产品日志的规则。技能包、模板和校验器存在，不等于整个项目已完成 SDD 基线批准；具体状态和证据看各资料。

## 功能与历史变更记录

以下记录按日期倒序列出，用于查当时需求、实现、文件和验收证据。`newFeature/` 的历史实现说明不覆盖当前使用文档。

| 日期 | 记录 |
| --- | --- |
| 2026-10-13 | [全量压缩包准备、varlog 与串口兼容](newFeature/2026-10-13-archive-and-serial-compatibility.md)：ZIP LZMA/RAR、完整归档森林、默认目录与常见串口自检适配 |
| 2026-10-11 | [UDK V100 两个工作流](newFeature/2026-10-11-udk-v100-workflows.md)：共享组网、真实本地协议测试及串口完成判断 |
| 2026-10-11 | [本地文件操作与独立产品日志解析](newFeature/2026-10-11-local-product-log-parser.md)：含混排样本、3 GiB 回收、diag/var、模块表、统一时间和单文件离线网页扩展 |
| 2026-10-10 | [普通 Python 脚本与四类接口第一版](newFeature/2026-10-10-simple-script-api-v1.md) |
| 2026-10-09 | [本地协议测试与脚本接口分析](newFeature/2026-10-09-local-protocol-testing.md) |
| 2026-08-18 | [日志元数据、strict block 与虚拟连续浏览](newFeature/2026-08-18-log-metadata-strict-block-virtual-view.md) |
| 2026-08-17 | [Web Tool 注册模板](newFeature/2026-08-17-web-tool-registration-template.md) |
| 2026-08-17 | [Web 唯一启动链与固定端口](newFeature/2026-08-17-single-web-startup.md) |
| 2026-08-17 | [Web Find 上下文与并行批量 SCP](newFeature/2026-08-17-log-find-context-parallel-scp.md) |
| 2026-08-17 | [日志错误定位与组件定界 Skill](newFeature/2026-08-17-log-error-triage-skill.md) |
| 2026-08-17 | [日志自动收集与关联分析 workflow Tool](newFeature/2026-08-17-log-collection-workflow.md) |
| 2026-08-17 | [Line-block 起始行与无时间戳续行匹配](newFeature/2026-08-17-line-block-matcher.md) |
| 2026-08-17 | [精简批量传输日志与 Web 进度条](newFeature/2026-08-17-concise-transfer-logging.md) |
| 2026-08-16 | [脚本化日志来源与 block 正文排除](newFeature/2026-08-16-scripted-log-source-groups.md) |
| 2026-08-16 | [多目录日志收集与 BusyBox 兼容枚举](newFeature/2026-08-16-multi-directory-log-collection.md) |
| 2026-08-15 | [历史变更总结](20260815_change.md)：包含后续删除/迁移的早期脚本和旧网页能力，阅读时注意文首后续状态 |

## 配套机读资料、示例与第三方许可

| 文件 | 用途 |
| --- | --- |
| [项目依赖与打包声明](pyproject.toml) | Python 版本、依赖、测试和包资源声明 |
| [已测直接依赖快照](constraints-local-tested.txt) | 本地已测版本，非完整依赖锁文件 |
| [脚本 API 机读目录](docs/script-api-catalog.json) | 当前接口说明的结构化摘要 |
| [LaunchRequest 示例](docs/examples/launch-request.json) | 保留接口的结构化启动请求示例，结合 Web 接手说明阅读 |
| [ToolRequest 示例](docs/examples/tool-request.json) | Web Tool 结构化请求示例，结合工具合同阅读 |
| [SingleUnions_S3 连接模板](docs/examples/SingleUnions_S3.connections.json) | UDK V100 两个脚本共用的 1260/BMC 网口与 BMC 串口占位登记 |
| [日志知识库组件模板](logKnowledge/components/_template/component.yaml) | 组件别名、代码范围、日志标记和依赖的模板 |
| [独立网页第三方许可总说明](product_log/standalone/THIRD_PARTY_NOTICES.txt) | 内嵌解压组件的许可与署名，随生成 HTML 保留 |
| [fflate 原许可](product_log/standalone/vendor/fflate-LICENSE.txt) | fflate MIT 许可 |
| [seek-bzip 原许可](product_log/standalone/vendor/seek-bzip-LICENSE.txt) | seek-bzip MIT 许可 |
| [内嵌解压资源清单](product_log/standalone/vendor/manifest.json) 与 [原始资源/许可目录](product_log/standalone/vendor/) | fflate、seek-bzip、XZ、libarchive-WASM、node-unrar 和纯 JavaScript LZMA 的固定版本、来源、包完整性及逐文件 SHA256；新增依赖原许可按目录和清单汇总，完整许可随 HTML 内嵌 |
| [SDK RAR 元数据资源清单](autoenv/_vendor/rarfile_manifest.json) | 固定 rarfile 版本、原始来源、逐文件大小和 SHA256；RAR 后端边界见产品 README |
| [rarfile 原许可](autoenv/_vendor/RARFILE_LICENSE.txt) | 随 SDK 保存的 rarfile ISC 原许可 |

普通脚本示例：[最小示例](scripts/example.py)、[可复制模板](scripts/template.py)、[全量接口示例](scripts/all_interfaces.py)。它们是可执行源码示例，具体接口语义看接口说明。

UDK V100 业务脚本：[环境拉起](scripts/UDK_V100.py)、[固件替换](scripts/UDK_V100_Firmware.py)。包与连接需要按使用说明填写，启动 shell 保留占位。

## 更新本清单

新增、移动或删除说明文档时，同步修改对应分类链接；更新当前功能的权威说明时，保持 [文档入口与能力边界](docs/DOCUMENTATION_MAP.md) 中的归属一致。功能记录、技能参考和模板也应列出，不能只扫描 `docs/` 或遗漏隐藏的 `.agents/`。

扫描时排除第三方源码目录 `**/vendor/**`，其资源与许可通过上方目录、清单和总说明聚合；不要用宽泛的 `*vendor*` 排除规则漏掉 `autoenv/_vendor/RARFILE_LICENSE.txt`。测试夹具说明属于维护文档，构造日志和二进制包本身不列入文档清单。

本文是文档导航，不引入新的接口、启动命令或开发门禁。样本、生成的 HTML、运行目录与测试输出的位置以各使用文档为准。
