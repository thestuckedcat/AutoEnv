# AutoEnv 第一版重构设计

## 最简实现

新流程只有普通 Python 脚本、统一本地 handler 和带方法的环境对象。用法先看 [QUICK_START.md](QUICK_START.md)；设计决定和完整入参出参归档在 [REFACTOR_PLAN_FOUR_API_CATEGORIES.md](REFACTOR_PLAN_FOUR_API_CATEGORIES.md)。

执行路径：CLI 静态发现脚本 → 校验参数/组网交集/角色绑定 → 建立唯一 session → 执行 main → init_script 初始化 runtime → 申请对象并自检 → 顺序操作 → 写结果/释放连接 → stdout 输出完整日志路径。

网页不会复制 Python 执行器：表单构造实际 CLI 参数或 UTF-8 request 文件，启动 CLI 子进程，实时读取 output.log 的简洁操作结果；完成后展示退出状态与 run.log 链接。

## 补充组件

| 层 | 职责 |
| --- | --- |
| script_files | AST 读取 main/顶层、SCRIPT_ARGUMENTS、环境白名单与 HDFS 配置名；发现不执行 |
| catalog | 两级连接数组、类型/名称/白名单、配置快照和历史有效性 |
| session/ScriptPaths | 唯一 owner、ContextVar 隔离、路径、只读参数、生命周期与源码溯源 |
| 文件服务 | FileHandle、有序搜索组、唯一目录定位、安全解压、复制/重命名、原子生成、独立 HDFS 下载 |
| 环境对象 | 简洁方法、可用性、自检、恢复、结果/异常转换 |
| 原连接实现 | SSH/Telnet/FTP 握手认证、命令 framing、真实 SCP/SFTP/FTP 字节通道 |
| recorder | run.log 完整输出/结果；output.log 每条动作简洁结果；操作编号关联 |
| CLI/Web | 同一启动预检与宿主；Web 使用真实 CLI 子进程 |
| product_log | 独立本地产品日志整理/流式解析/SQLite 查询；模块 CLI 与独立网页共用 workflow |

`FileHandle` 只表示本地已经完成的文件；upload 返回 None，download 返回 handler。SSH upload_type 固定在环境配置，Telnet 不传文件，FTP 提供 upload/download。run 返回字符串但未知/失败抛异常，execute 返回完整 CommandResult 供明确分支。底层退出码和 Telnet 标记语义不改。

原 RunContext、selectors、日志提取/查询与 Tool SDK 保留给现有高级组件；新普通脚本不需要 decorator、ctx、register_func 菜单、资源标签或上传映射。接口一致性要求新默认入口只推荐四类简单接口，不能一半新调用一半旧声明。

## 补充日志与结果

产品日志功能按用户要求独立放在 `product_log/`。`python -m product_log` 不绑定远端环境；`/product-logs/` 由唯一 Web 服务提供，在专属任务线程调用同一 workflow，任务生命周期不复用远端脚本的 ProcessSession。固件先按文件/行序组装跨文件 seg，再提供严格读取时间升序与连续批次两种视图。详细规则见 [产品日志说明](../product_log/README.md)。

每条操作关联脚本路径:行号、operation_id、协议/目标、状态、退出码/异常与完整远端输出。简洁 output.log 不复制大段远端文本；用户 print 与完整结果保存在 run.log。CLI 准备失败也要有实际日志文件，最终 stdout 单路径、退出码 0/1/2。print/异常/低层输出捕获的边界见测试记录，不以“可成功执行”代替“全部输出已落日志”验收。

workflow-source.json保留当次脚本路径/revision/完整源码，保证脚本后续修改仍能按行号追到当次流程。result.json的final_operation选择返回的早期失败或未恢复自检，不被随后read记录覆盖。可选未传参数保存None后有效rerun，历史与声明类型持续校验。

已处理的 execute 失败可以进入恢复分支，不永久污染脚本结果；返回失败结果、未处理异常或未恢复自检才使流程失败。Telnet prompt 业务关键词成功仍保留 RESULT_UNKNOWN，不伪造返回码。

串口先观察实际登录提示或 shell 提示，已登录不再发送凭据；历史 Last login 横幅不触发认证。非 POSIX 自检可仅确认提示符或执行成对配置的业务探针，结果提供 check_scope 与 exit_status_available；简洁日志显示检查范围及失败原因。UDK 固件流程仍等待已知退出 0，再检查 success。

产品日志准备阶段先展开全部支持的压缩包，再定位唯一 diag/varlog 目录；varlog 不存在时兼容旧 var。结构化来源组件区分压缩包边界和字面文件名，普通目录深度决定递归选择。离线 HTML 内嵌 ZIP-LZMA 和 RAR4/5 解码器；Python SDK 的 RAR 依赖本机 bsdtar，具体后端边界见 [产品日志说明](../product_log/README.md)。SDK unzip 仍只展开当前一层。

## 补充测试与边界

本轮验证除 HDFS 外全部连接与调用，用真实 loopback SSH/SCP/SFTP、Telnet、FTP 服务，命令只确保送达，不执行远端 shell。文件实际通过协议传输、落盘并比较字节。命令/结果和搭建步骤见 [LOCAL_SIMULATION.md](LOCAL_SIMULATION.md)；UT 入口见 [tests/README.md](../tests/README.md)。

不新增 COM 驱动、目录传输、DAG 或并发业务调度。Web 唯一启动命令/固定端点保持不变。真实 HDFS、真实设备/串口启动时序与网络服务兼容需另行授权验收。

UDK V100 的 [环境拉起与固件替换](UDK_V100_WORKFLOWS.md) 直接复用上述脚本/session/协议层：静态声明相同组网，包下载返回 handler，1260 以 Linux boot_id 变化和 SSH 自检判断重启恢复；BMC 网口上传固件后由串口等待命令返回，再作 success 输出检查。安装 shell 暂为占位，不增加业务调度框架。
