# CHANGE-2026-08-18-LOG-USABILITY 增量变更计划

> 状态：implemented | 基线 commit：9dd3be5 | 关联 REQ：REQ-LOG-101..117

## 1. 需求增量和非范围

在既有日志可用性改造上增加：服务端秒级时间关联、本地样本规则预览、规则/hash/schema 版本、全部 matcher 单次文件解析，以及原始/附加元数据双格式导出。slot/socket 仍只用于筛选。远端采集范围、SCP、解压安全和唯一 Web 启动入口不在范围内。

## 2. 现有 design/spec 冲突与影响面

现有 `match_block()` 为整块统一时间、支持 end 后隐式块，Web 使用 200 行可见分页；两者与确认需求冲突。影响 `autoenv.logs` 公共 API、每批 SQLite schema、只读查询接口、日志 renderer、文档和离线测试。旧批次必须继续可读。

## 3. 方案选项、取舍和 ADR

采用兼容模式扩展：`boundary_mode="legacy"` 保持旧调用，`strict` 实现确认语义。元数据使用正则命名组，原始文本不改写，匹配 span 单独持久化。Web 去掉页码但后端继续分块，前端仅渲染可见窗口。

## 4. 文件/符号级实施计划

- `autoenv/logs.py`：`MetadataPattern`、逐字段继承、strict block、SQLite 字段/索引。
- `autoenv/log_query.py`：旧 schema 兼容、slot/socket 筛选、窗口分块查询。
- `webPage/server.py`、`app.js`、`logs.css`：查询参数、虚拟滚动和交互。
- `webPage/tools/log_collection.py`：展示 strict/metadata 公共接口。
- `autoenv/log_query.py`：服务端关联与双格式导出。
- `webPage/tools/log_collection.py`：复用固化规则完成有界本地样本预览。
- tests/docs/newFeature：契约和追踪同步。

## 5. 兼容、迁移和回滚

现有 batch 不改写；旧 manifest 没有版本/hash 时显示 unknown，旧索引仍可原文/元数据导出。预览不持久化样本。回滚代码不会破坏已有 target/SQLite，新增 manifest 字段为向后兼容数据。

## 6. 测试矩阵与目标机计划

离线 UT 追加覆盖跨 target 秒级关联、slot/socket 与关联解耦、预览大小/结果、规则 hash 稳定性、旧 manifest、单次 `_read_lines` 和双格式导出。真实 SSH/BusyBox 不执行，因为处理发生在本地下载完成之后。

## 7. 文档回写清单

README、Web 架构、环境注册指南、Web quick start、tests README、script-generator skill 和 `newFeature/`。

## 8. 风险、未知项和用户确认

用户已确认边界规则。风险是超大日志的实际行高/浏览器性能缺少真实样本压测；采用固定单行和分块窗口隔离。

## 9. Gate G4 建议判决

`PASS`：语义、兼容、回滚和离线验收均可测试；目标设备行为未改变。
