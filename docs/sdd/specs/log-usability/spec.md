# 日志解析与浏览可用性 Specification

> 状态：implemented | 来源 NEED：NEED-LOG-101/102 | 关联设计：`docs/WEB_ARCHITECTURE_AND_HANDOFF.md` | 基线：9dd3be5

## 1. 范围、术语与参与者

`metadata carrier` 是能提取 timestamp/slot/socket、但可被配置为不输出的行；`strict block` 是确认的文件头容错 BEGIN/END 状态机。

## 2. 前置条件和环境

输入是已下载、解压并稳定排序的本地日志文件；继承状态以单个源文件为边界。

## 3. 规范性需求

| REQ ID | 系统必须/shall | 来源 | 优先级 | 验证方法 | 验收标准 |
|---|---|---|---|---|---|
| REQ-LOG-101 | 系统必须用命名捕获提取 slot_id/socket_id，并逐字段向后继承 | 用户 | P0 | UT | direct/inherited/unknown 均精确断言 |
| REQ-LOG-102 | strict block 必须忽略活动块内重复 BEGIN 的边界作用，但仍消费其属性 | 用户 | P0 | UT | 后续正文继承重复 BEGIN 属性 |
| REQ-LOG-103 | 活动块必须在首个 END 或 EOF 关闭，EOF 视为正常 END | 用户 | P0 | UT | EOF 正文保留且非 incomplete |
| REQ-LOG-104 | 文件头首边界为 END 时必须保留此前正文；首边界为 BEGIN 时必须丢弃此前正文 | 用户 | P0 | UT | 两种前缀精确输出 |
| REQ-LOG-105 | END 后正文必须丢弃，直到新的 BEGIN | 用户 | P0 | UT | 块外行不存在于 target/index |
| REQ-LOG-106 | consume 行必须先更新属性再从输出中移除 | 用户 | P0 | UT | context 继承但 carrier 不输出 |
| REQ-LOG-107 | 新批次索引必须保存元数据、来源状态和可隐藏匹配 span，旧批次仍可查询 | 用户 | P0 | UT | 新旧 schema 查询通过 |
| REQ-LOG-108 | Web 必须支持 slot/socket 筛选，source 不占用普通日志行 | 用户 | P1 | Web 契约/UT | 参数存在、普通行无 source small |
| REQ-LOG-109 | Web 必须呈现连续滚动且不显示页码按钮 | 用户 | P1 | Web 契约 | 无上一页/下一页，滚动触发窗口查询 |
| REQ-LOG-110 | Web 必须只渲染当前窗口，不能一次创建全部日志 DOM | 用户 | P0 | Web 契约 | 存在虚拟窗口和分块 limit |
| REQ-LOG-111 | Web 必须支持隐藏模板片段、可调日志字号和高对比显示 | 用户 | P1 | Web 契约 | 控件、CSS 变量、无整行 opacity |
| REQ-LOG-112 | Tools 导航和日志窗口必须支持水平/垂直空间调整 | 用户 | P1 | Web 契约 | 折叠导航及 pane resize |
| REQ-LOG-113 | 关联高亮必须由服务端按时间计算，窗口由 Web 以秒指定且不得使用 slot/socket 参与关联 | 用户 | P0 | UT/Web 契约 | 跨 target 返回时间窗内 sequence，属性筛选保持独立 |
| REQ-LOG-114 | Web 必须允许上传本地样本并用当前固化规则预览匹配、保留和属性继承 | 用户 | P1 | UT/HTTP | 不接受任意路径，返回每 target 计数和示例行 |
| REQ-LOG-115 | manifest 必须记录规则 schema/version/hash 和索引 schema version | 用户 | P0 | UT | 相同规则 hash 稳定，规则变化 hash 改变 |
| REQ-LOG-116 | 所有 matcher 必须复用单文件解码和元数据解析结果 | 用户 | P0 | UT | 多 matcher 每文件只调用一次 `_read_lines` |
| REQ-LOG-117 | Web 必须提供原始正文与附加元数据两种完整导出，隐藏状态不得影响导出 | 用户 | P1 | UT/Web 契约 | 两种内容精确且由服务端从 SQLite 生成 |

## 4. 接口、数据和兼容约束

新增 `MetadataPattern`；`group/source_groups` 接受 `metadata_patterns`；`match_block` 新增 strict/consume 参数。查询增加 correlation/export，预览只接收有大小上限的请求正文。旧参数、旧 batch 和旧 manifest 读取保持兼容。

## 5. 异常、边界与恢复

重复 END 在非活动状态忽略；属性跨 block 保留、不跨文件；无字段为 unknown；原始正文/source 始终留在索引供详情审计。

## 6. 非功能预算

普通浏览单次最多返回 500 行，Web 同时创建的日志行 DOM 不超过一个小窗口；预览样本最大 8 MiB；关联窗口为 1..86400 秒；不新增网络依赖。

## 7. 追踪矩阵

| NEED | REQ | 设计元素 | 代码 | TEST | EVID | 状态 |
|---|---|---|---|---|---|---|
| NEED-LOG-101 | REQ-LOG-101..107 | MetadataPattern/strict block/index | `autoenv/logs.py`, `autoenv/log_query.py` | `test_strict_match_block_*`, old-schema query | 296 passed | verified |
| NEED-LOG-101 | REQ-LOG-108..112 | virtual log renderer | `webPage/app.js`, `webPage/logs.css` | Web contract tests | pytest + local browser/no console errors | verified |
| NEED-LOG-102 | REQ-LOG-113..117 | correlation/preview/versioned manifest/export | `autoenv/log_query.py`, `autoenv/log_collection_rules.py`, `webPage/server.py`, `webPage/app.js` | correlation/export/preview/manifest/single-parse UT + Web contract | 296 passed + local browser/API smoke | verified |

## 8. 未知项和不适用项理由

真实浏览器百万行压测样本未知；硬件、ABI、字节序、功耗和目标板不适用于本地 Python/Web 解析变更。

## 9. 人工基线确认记录

2026-08-18：用户确认重复 BEGIN、EOF END、文件头隐式块、块外丢弃和 unknown 语义，并指示继续实现。

2026-08-18：用户确认关联只使用可配置秒级时间窗，slot/socket 仅用于筛选；同时批准规则预览、规则/hash/schema、单次解析和双格式导出优化。
