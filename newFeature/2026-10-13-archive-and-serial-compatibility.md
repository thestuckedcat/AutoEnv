# 全量压缩包准备、varlog 与串口兼容

## 用户行为与范围

解决独立日志网页的 ZIP 算法 14 报错，增加 RAR 解压，先展开所有支持的压缩包再定位日志目录。内核默认目录改为 `varlog`，仅当它不存在时使用旧 `var`。多目录命中仍报错，可使用 `diag/slot62` 等部分路径消歧。

同时改善 UDK 串口自检：已登录会话不发送账号密码；`auto` 区分 POSIX 与设备提示符，设备提示符允许只检查连接就绪或使用成对业务探针。命令退出码未知仍是 `RESULT_UNKNOWN`，固件流程继续要求退出码 0，并在命令返回后检查 success。

## 数据流与实现规则

- Python 和浏览器先建立完整压缩包森林，以队列展开所有层次及未选中的目录。目录选择与日志筛选在准备完成后进行。
- 压缩包各层保持独立来源组件，展示路径用 `!/`；目录的部分路径匹配跳过压缩包文件名，允许普通目录组件跨越打包边界。普通文件名里恰好含 `!/` 仍保留不同的结构化来源。
- 默认精确查找 diag/varlog；varlog 多命中直接失败，不能改选 var。显式指定旧 var 仍可使用。
- 原 archive_patterns/--archive-pattern 字段保留接收与校验，已不再限制准备阶段。网页不再提供这个展开过滤输入，日志文件模式及复制/重命名规则继续生效。
- 外部输入不修改，任务展开预算、取消、成员路径与类型检查、来源隔离和失败清理继续生效。文件排序、固件分段及日志时间规则保持原约定。
- 离线 HTML 内嵌原始解码器、WASM 与全部许可，生成只使用 Python 标准库，运行无网络或系统解压工具依赖。
- Python SDK 的 RAR 元数据由固定版本的原始 rarfile 模块读取；只用本机 bsdtar 的标准输出解压文件，由 SDK 验证并写入隔离临时目录。Windows 默认使用系统 tar.exe；其他环境需要可用的 bsdtar。它不会让外部工具选择磁盘成员路径，也不会安装依赖。
- 串口自检结果增加 check_scope 和 exit_status_available，分别表示 command/keyword/prompt 级检查与是否能获得退出状态。available 不意味着未知命令获得了退出码 0。

## 主要文件和接口

- [压缩包准备](../product_log/workflow.py)、[浏览器准备](../product_log/standalone/archive.js)。
- [本地归档 SDK](../autoenv/local_archives.py) 及 [RAR 元数据原始资源](../autoenv/_vendor/rarfile_manifest.json)。detect_archive 增加 rar 类型，unzip 仍只展开当前一个包，递归编排属于产品日志流程。
- [串口客户端](../autoenv/telnet_client.py)、[环境对象](../autoenv/remote_environment.py)、[UDK 使用文档](../docs/UDK_V100_WORKFLOWS.md)。
- [离线构建](../tools/build_product_log_page.py)、[解码资源摘要](../product_log/standalone/vendor/manifest.json)、[第三方许可](../product_log/standalone/THIRD_PARTY_NOTICES.txt)。
- 新增全量准备、RAR 和串口兼容测试；现有单层展开与必须 probe 的预期按新规则同步。

## 验证与边界

修改前已分别观察到准备顺序、varlog 默认规则、ZIP-LZMA 和串口状态兼容的失败测试，再执行对应实现。独立复核发现字面 `!/` 目录身份冲突、跨包非递归目录深度丢失，以及历史登录/密码横幅被误消费；对应真实回归先失败后通过。最终复核的解码器部分因代理额度限制未完成，由主代理继续检查解码、许可、摘要及测试证据。

- 最终全量离线 UT：**873 passed、2 skipped、1 warning，195.22s**。跳过为 Windows 符号链接权限，warning 为既有重复 ZIP fixture。首轮唯一失败是单入口静态检查把 logs 内历史源码快照也扫描为当前代码；检查现已排除运行和构建产物目录，定向和完整回归均通过。
- 离线解码及构建专项：**88 passed**；新增真实生成 HTML 的 ZIP→ZIP14→RAR4/5 Worker 集成 **2 passed**，每个样本 64 用户态、1 内核态，65 条排序、模块映射及全量导出与 Python 摘要一致。既有 6,011 条混排样本继续对照。
- 串口/Telnet/Remote/CLI 专项：**124 passed**；新增串口兼容 26 项，均使用真实 loopback TCP。
- 5 个环境脚本共享静态契约、编译与 JavaScript 语法、3 个项目 skill、SDD 合同均通过；CLI 静态发现及隔离组网的两个 UDK describe 通过。根目录禁用模板的 describe 明确失败，不连接占位地址。
- wheel 构建成功，包含 RAR 原始源码、原许可及固定摘要清单，逐字节校验通过；独立页用 `python -I -S` 构建，无 site-packages 依赖。
- 暂存树重新进行 Windows checkout，原始 SDK/浏览器资源摘要通过，构建出的 HTML 与工作区逐字节相同：2,034,230 字节，SHA256 `49a5a4ecb8ef58126517bd32cab7c1cd32ac16142ec5305fcad351eff8e95632`。281 个变更文档本地链接有效，README_GUIDE 覆盖全部 71 份维护文档。

未访问真实串口、1260、BMC 或 HDFS。用户仅提供 unavailable 摘要，不能确认其现场失败的具体原因；本轮覆盖常见登录、模式、自检与输出时序。特殊设备的登录提示仍按连接配置设置，不猜测业务自检命令。Python RAR 能力受系统 bsdtar 版本和文件名支持限制；报错明确失败，不公布部分解压结果。独立页浏览器直接打开的限制与前一版相同，执行的 Worker 与真实解码器由 Node 验证。

此前能力见 [本地产品日志](2026-10-11-local-product-log-parser.md) 和 [UDK 工作流](2026-10-11-udk-v100-workflows.md)。
