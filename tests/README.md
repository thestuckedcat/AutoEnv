# AutoEnv 本地协议测试与排查

默认全量测试会启动本机临时端口的协议服务。SSH、SCP、SFTP、Telnet、FTP 和 WebHDFS 使用生产客户端及真实 TCP/协议通道，文件传输方法不打桩。服务全部绑定 `127.0.0.1`；每条测试隔离文件与连接并清理资源。`conftest.py` 的连接守卫拒绝非 loopback 连接，避免测试意外访问已注册的实验室目标。

## 最简实现：运行

```powershell
python -m pip install -e ".[test]"
# 可选：复现当前已测直接依赖组合（非完整锁文件）
python -m pip install -c constraints-local-tested.txt -e ".[test]"
python -X utf8 -m pytest -q

# 新公开接口通过真正 CLI 验证，除 HDFS 外全部真实本地协议
python -X utf8 tools/local_simulation.py

# 网络能力与跨协议流程
python -X utf8 -m pytest tests/test_ssh_host.py tests/test_telnet_client.py tests/test_interfaces_web_ftp.py tests/test_package_extractor.py tests/test_local_protocol_workflow.py tests/test_log_collection.py -q

# 精确重跑失败项
python -X utf8 -m pytest <pytest输出的完整node-id> -vv

# 全部环境脚本静态契约
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py
python -X utf8 -m pytest tests/test_generated_script_contract.py -q
```

需要 `pyproject.toml` 声明的 Paramiko >=3、scp、requests 和 pytest。缺少协议依赖时测试应直接失败，不能跳过或恢复假客户端。本地协议服务不要求系统安装 sshd、telnetd、FTP 或 HDFS。

本次新接口的实测搭建、CLI 命令/日志路径与字节证据见 [LOCAL_SIMULATION.md](../docs/LOCAL_SIMULATION.md)。普通脚本使用 main()/init_script()/get_remote_info()/FileHandle，无需装饰器；老SDK测试仅证明保留的高级组件不回归。

## 补充：本地模拟远端

| 服务 | 真实通信 | 模拟内容与验证证据 |
| --- | --- | --- |
| `local_remote/ssh.py` | Paramiko SSH 握手/密码鉴权/channel、SFTP subsystem、SCP sink/source 消息与确认字节 | 文件落到隔离目录；内部 mkdir/MD5/大小/目录/mtime 查询读真实文件；业务命令仅记录和响应；记录传输路径、输入字节及并发峰值 |
| `local_remote/telnet.py` | TCP 连接、IAC 协商、提示符、退出码 marker、原始控制字节 | 业务命令和 probe 分开记录；POSIX 或设备 prompt、分片输出、缺 marker、超时、断线和重新连接 |
| `local_remote/ftp.py` | ftplib 控制连接、主动/被动数据连接、STOR/RETR/PWD/SIZE/MKD | 上传/下载实际字节；完整二进制、空文件、覆盖、目录、权限、鉴权、大小不一致和连接失败 |
| `local_remote/webhdfs.py` | requests HTTP LISTSTATUS、OPEN、307 重定向、流式正文 | 服务端目录元数据、真实请求与流式字节；newest/override、404 候选消失、408/500、大小不一致和 `.part` 清理 |

允许在**服务端**配置协议错误、截断数据、修改收到的文件、输出与退出状态。这代表可控的远端行为，客户端依然通过同一种真实协议访问它。禁止替换 Paramiko、SCPClient、SFTP、ftplib、requests、socket_factory 或文件传输方法来制造成功。

业务命令不会被本机 shell 执行。命令测试保证送达、输入响应、输出解析和状态分类；文件传输验证协议记录及接收后的完整字节/摘要，不以“调用 put/get”作为成功证据。

## 补充：组件测试范围

2026-10-20 的产品日志来源配置扩展增加 `test_product_log_configuration.py`、`test_product_log_source_python.py`、`test_product_log_source_offline.py`、`test_product_log_config_entrypoints.py` 和 `test_product_log_settings_ui.py`。覆盖显式初始 JSON 与 Python/Node 校验一致性、空规则和空来源选择不回退、来源绑定 ISO/dmesg/未知时间解析器、规则只引用所选来源、同文件解析器冲突、固件完整段筛选，以及 HTTP/CLI/离线构建的配置快照。设置测试验证配置控件只在关闭的设置对话框内、按规则选择来源、编辑/保存/导入/下载及错误配置反馈。

目录回归区分祖孙同名与不同分支：外层目录覆盖普通和包内后代时选择外层；不同分支即使深度不同仍报歧义；输入基准层优先，仅包内存在目标时仍可发现。顶层包、跨打包边界部分路径、字面 `!/` 与压缩包来源身份继续保留测试。

本轮于 2026-10-21 完成：配置合同专项 **58 passed**，最终全量离线回归 **978 passed、2 skipped**（238.14s），两项跳过均为 Windows 符号链接权限；既有 Paramiko 弃用警告和重复 ZIP fixture 提示保留。两版设置真实浏览器交互 **6 passed**，标准服务及生成 HTML 也验证同一混合日志样本。完整环境、构建和审查证据见 [来源配置功能记录](../newFeature/2026-10-20-product-log-source-configuration.md)。下面列出的 2026-10-13 等结果是相应历史版本的证据。

```powershell
python -X utf8 -m pytest -q tests/test_product_log_configuration.py tests/test_product_log_source_python.py tests/test_product_log_source_offline.py tests/test_product_log_config_entrypoints.py tests/test_product_log_settings_ui.py
```

2026-10-13 的压缩与串口兼容扩展新增 `test_product_log_preparation.py`、`test_product_log_standalone_preparation.py`、`test_local_rar_archives.py`、`test_product_log_offline_rar_integration.py`、`test_serial_compatibility.py`、`test_serial_readiness_logging.py`。准备测试覆盖全森林展开后才定位 diag/varlog、旧 var 回退、跨包部分路径、字面 `!/` 与包内路径的不同身份、非递归的普通目录深度；真实 RAR4/5、固实、ZIP14、损坏与不支持成员均有实际解码回归。

2026-10-13 历史全量离线回归：**873 passed、2 skipped、1 warning，195.22s**。两项跳过为 Windows 符号链接权限，warning 为既有重复 ZIP fixture。单入口静态检查排除 logs/runtime_path 等运行产物，避免历史完整源码快照被误当作当前服务实现；当轮固定端点及唯一启动方式检查通过。离线解码/构建 88 项、新增完整 RAR Worker 集成 2 项、串口/Telnet/Remote/CLI 专项 124 项均通过。

实际生成 HTML 的 Worker 在禁止网络 API 的 Node 环境处理 ZIP→ZIP-LZMA→RAR4/5，分别验证 64 条用户态和 1 条内核态、排序、模块表与 Python 全量导出摘要一致。串口使用真实 loopback TCP，覆盖登录/已登录/用户名后直接进入 shell、历史登录与密码横幅、POSIX/auto/prompt_only、失败业务探针和未知退出状态；自检摘要验证范围、原因且不复制原始设备输出。RAR 样本来源见 [合成夹具](fixtures/product_log_archives/README.md)，SDK 系统 bsdtar 的能力边界见 [产品说明](../product_log/README.md)。本轮验证结果见 [功能记录](../newFeature/2026-10-13-archive-and-serial-compatibility.md)。

`test_udk_v100_workflows.py` 将仓库中的两个 UDK V100 脚本交给真实 CLI 宿主，在隔离项目登记 `SingleUnions_S3`，使用真实 loopback SSH/SCP/SFTP/Telnet 和本地 WebHDFS 字节流。只替换环境信息与 HDFS 端点，10 秒等待、handler、解压、上传和命令客户端均执行实际实现；服务端在隔离目录模拟重启、启动和进程列表，绝不运行主机破坏性 shell。运行 `python -X utf8 -m pytest -q tests/test_udk_v100_workflows.py`；业务规则与现场尚未验证部分见 [UDK V100 工作流](../docs/UDK_V100_WORKFLOWS.md)。

2026-10-11 UDK V100 扩展最终全量离线回归：**781 passed、2 skipped、1 warning，160.44s**。新增 22 项工作流测试（单独运行 39.10s）和 5 项 Telnet 返回码/中间提示符回归，完整 Telnet 测试为 68 passed。跳过仍为 Windows 符号链接权限，warning 仍为既有重复 ZIP fixture。三个环境拉起测试均真实等待重启后的 10 秒，成功分支还验证首次 SSH 恢复时 boot_id 未变化不能继续。

独立单文件网页新增 `test_product_log_standalone_build.py`、`test_product_log_standalone_archives.py`、`test_product_log_standalone_parser.py`。运行 `python -X utf8 -m pytest -q tests/test_product_log_standalone_build.py tests/test_product_log_standalone_archives.py tests/test_product_log_standalone_parser.py`，需要 Node 执行实际浏览器 JavaScript；最终 HTML 使用者只需浏览器，打包只需 Python 标准库。测试覆盖生成产物的资源全部内嵌、许可、确定性、vendor SHA256、`-I -S` 构建、已有代码不变、真实 codec、多层压缩、编码、分段/查询/模块/日期及 Python 对照。实际生成 HTML 的 Worker 在禁用网络 API 下处理 6,011 条样本，全量统一导出与 Python 一致。内置浏览器拒绝 `file://`，未做直接打开/交互验收；边界和构建命令见 [独立版说明](../product_log/standalone/README.md)。

2026-10-11 单文件网页扩展最终全量离线回归：**754 passed、2 skipped、1 warning，121.22s**。新增 120 项 standalone 测试（构建/完整 Worker/高亮 9、归档 49、解析/查询 62）；两项跳过仍为 Windows 符号链接权限，warning 仍为既有重复 ZIP fixture。GZIP 填充、可选头与各成员完整性、Unicode casefold 高亮均有真实回归。

2026-10-11 diag/var 扩展最终全量离线回归：**634 passed、2 skipped、1 warning，71.55s**。两项跳过均为本机 Windows 不允许创建符号链接；warning 是既有重复 ZIP 成员 fixture。新增 22 项产品收集和 30 项解析/映射测试，另以真实 CLI/HTTP 验证完整多层合成包；浏览器结果见 [功能记录](../newFeature/2026-10-11-local-product-log-parser.md)。

新增本地产品日志测试直接构造真实 ZIP/TAR/GZ 与多文件用户态/内核态/固件日志，不访问设备。运行 `python -X utf8 -m pytest -q tests/test_local_file_operations.py tests/test_product_logs.py tests/test_product_log_collection.py tests/test_product_log_domains.py tests/test_product_log_web.py tests/test_product_log_integration.py tests/test_product_log_retention.py`，验证搜索/解压/复制、跨文件 seg、重复段、编码、时间顺序、分组与查询、独立 CLI、真实 loopback HTTP 分块上传及任务回收。

`test_product_log_collection.py` 验证 auto/product/generic 分流、初始显式 diag/dmesg 目录及分支消歧、仅一类存在、diag 历史字典序/current 最后、dmesg 数字轮转/current 最后、多层 var 及两来源的独立选择。`test_product_log_domains.py` 使用真实文件与索引验证初始配置的 UDK 后缀归一和 udk2 排除、12 个英文月份、无日期绝不继承、来源保留、显式模块表/不存在/重叠匹配、固件类型名称、统一时间与未知上下文及旧批次重导。当前索引 schema 为 3，固件段及读取批次允许未知时间并继续拼接；schema 2 批次需重新导入。删除模块行不再自动补回；采集规则改变需重导入，模块查询表改变无需重新解析。

3 GiB 自动清理以 `test_product_log_retention.py` 的小阈值真实目录测试覆盖，不创建数 GiB 的测试文件；验证精确触发边界、旧数据优先、活动/浏览/最新结果保护、上传及受管路径输入引用、重启恢复、外部原件不变、部分删除后的真实统计及 Windows junction 跳过。`test_product_log_web.py` 另验证真实 HTTP 占用接口、解除浏览保护后的回收、历史列表同步和仅保护数据超阈值时仍可查询。

可运行 `python -X utf8 tests/product_log_sample.py logs/product-log-demo/product.tar.gz --source-dir logs/product-log-demo/source` 重建网页混排样本：Type 0–4 × Dev 0–5 共 30 组固件、每组 120 条；`udk log/UBE/MEME/HBOOT` 四种驱动、各 500 条。`test_product_log_integration.py` 通过真实 CLI 核对所有分组的精确数量、跨 7 文件的 14 段组装和时间排序；名称含空格的 ModuleName 另有解析回归。

diag/var 演示样本使用 `python -X utf8 tests/product_log_domain_sample.py logs/product-log-domain-demo/product.tar.gz --source-dir logs/product-log-domain-demo/source` 生成。网页使用初始配置，或在设置中限定 diag 来源 `directory_patterns: ["diag/slot62"]`、dmesg 来源 `["var"]`；旧 CLI `--collection-mode product --diag-directory diag/slot62 --var-directory var` 仍可用。保留初始三条 `any` 规则时预期 11 文件、2,003 用户态、408 内核态（8 条时间未知）、3,600 固件、统一时间 6,011 条；udk 用户态 500 条、内核匹配 104 条，UBE 的 mami 内核匹配 100 条。样本覆盖六种压缩格式和两条多层 var 链，包含 `.1/.2/.10/current`、UDK 两种后缀、udk2 与多 Slot/Cpu；原始文件及概要在 `logs/product-log-domain-demo/source/`。

| 文件 | 覆盖目标 | 失败优先检查 |
| --- | --- | --- |
| `test_ssh_host.py` | 真实命令、关键词响应、鉴权/断连/复用、双向 SCP/SFTP、二进制/空文件/Unicode、MD5、覆盖、唯一匹配、四路 SCP、截断/失败清理和占位符 | ssh_host、commands/transfers、run.log |
| `test_telnet_client.py` | 真实 TCP、模式、IAC 分片、marker、退格/ANSI/UTF-8、超时/断连、Ctrl+B/原始字节、占位符来源及重连 | telnet_client、commands/probes/raw_inputs |
| `test_interfaces_web_ftp.py` | FTP 主动/被动真实传输与校验；保留 LaunchRequest、固定 Web 启动、资源、Tool、PTY/UI 边界 | ftp_host、FTP 记录、Web/interface |
| `test_package_extractor.py` | HTTP 包选择/重定向/原子替换/清理；本地 TAR/ZIP 提取、安全与摘要 | package_manager、HTTP 记录、extractor |
| `test_runtime_registry.py` | 元数据、参数保存、HTTP 下载、newest/rerun、独立目录、组合脚本和 func 生命周期 | runtime/registry、params/result/last-run |
| `test_local_protocol_workflow.py` | WebHDFS → ZIP → SCP/SFTP → shell → SSH/Telnet → SCP/SFTP → FTP → 日志 → func → rerun | 每步结果、双方字节、最终记录及命令 |
| `test_log_collection.py` | 所有日志经真实 SCP 获取；归档、来源隔离、strict/legacy/metadata、SQLite/查询/export/Find、workflow | 真实 SSHHost、SDK 请求、logs/log_query |
| `test_product_logs.py` / `test_product_log_collection.py` | 独立本地整理、模式分流、diag/var 来源与嵌套压缩、跨文件固件组装、稳定业务排序 | product_log/workflow、manifest、来源路径 |
| `test_product_log_configuration.py` | Python/JavaScript 配置合同、可移植字段正则、来源/解析器引用、空选择、别名与类型名称 | default_config.json、configuration.py、standalone/configuration.js |
| `test_product_log_source_python.py` / `test_product_log_source_offline.py` | 目录祖先覆盖与分支歧义、基准层/包内回退、来源选择、时间解析、同文件冲突及固件完整段 | workflow/parser、standalone/archive/parser、结构化来源 |
| `test_product_log_config_entrypoints.py` / `test_product_log_settings_ui.py` | HTTP/CLI 配置快照、空规则、离线同源内嵌、设置内来源多选与配置操作 | web/__main__/builder、两端设置对话框 |
| `test_product_log_domains.py` | 两态驱动、UDK 归一、dmesg 日期与未知、模块表动态查询、统一时间和固件类型 | parser/index/module_map、原始来源和记录字段 |
| `test_product_log_web.py` / `test_product_log_integration.py` / `test_product_log_retention.py` | 真实 HTTP/CLI、样本精确计数、模块表恢复、任务隔离与受管目录回收 | product_log/web、batch manifest、storage 状态 |
| `test_command_files.py` | 上传映射、Host 隔离、占位符及原样 shell 文本 | command_files |
| `test_results_selectors.py` | 结果、选择器安全、序列化、脱敏及 recorder 摘要 | results/selectors/recorder |
| `test_generated_script_contract.py` | 全部 scripts 的共享 AST 契约及非法调用反例 | 验证器报错行 |
| `test_udk_v100_workflows.py` | 两个发布脚本的真实 CLI、本地 WebHDFS 下载、重启/真实等待、SCP/SFTP 字节、固件解压、串口结束和业务检查 | 脚本/本轮 runtime、peer 命令/传输、boot_id、final_operation |
| `test_cli.py` | 真 CLI 子进程、非交互参数、静态发现、准备/执行状态、单log路径、全部输出捕获 | cli/script_files/session、params/result/run.log/output.log |
| `test_remote_environment.py` | 新对象自检、真实 SCP/SFTP/FTP 双向、Telnet被动流/关键词/登录、retry与失败分类 | remote_environment/原client、peer原始字节与传输记录 |
| `test_script_files_session.py` | AST普通main/顶层/with初始化、零参数、角色/白名单/组网交集、历史与只读参数、生命周期/源码快照 | script_files/session/catalog、workflow-source/params/result |
| `test_cli_protocol_flow.py` | 真CLI连接本地协议，上传下载handler链、命令分支、CtrlB、恢复与最终自检状态 | CLI/对象/真实peer、源行号与final_operation |
| `test_web_cli_bridge.py` | 新环境登记、描述/表单绑定、真实CLI subprocess、独立输出轮询与日志定位 | webPage/server与output.log/run.log |
| `test_log_error_triage_skill.py` / `test_sdd_skills.py` | 分析辅助脚本、知识模板及技能/SDD 合同 | 项目技能及本地子进程输出 |

纯解析、菜单、系统/PTY 能力和静态契约测试仍允许局部隔离；`.run` 提取测试注入本地 runner。它们不替代网络传输。日志测试的薄代理记录 SDK 请求并调用生产 `SSHHost.scp_download_many()`，没有伪成功结果或本地 copy2 下载。

## 补充：结果和已知边界

- `execute_on_output()` 成功仅表示关键词出现且响应已发出；测试额外在服务端确认完整字节收到。
- Paramiko 无 exit-status 时返回 `-1`；实现按断连处理，`expect_disconnect=True` 仅在命令已发出后允许成功。
- WebHDFS 404 映射为 `FileNotFoundError`，消失的 newest 候选继续跳过；HTTP 408/504 映射为下载超时。
- Telnet 退格清理使用实际 `\x08`，避免把正则 word boundary 当作退格而破坏输出和 marker。
- 迟缓 SSH banner 在 Paramiko 3.5 下可能抛 `SSHException("No existing session")`，当前为 `PROTOCOL_ERROR/CONNECT`，不会发送命令；不靠假 socket.timeout 强行改变分类。
- 新 Telnet 对象可按配置 username/password 及 login_prompt/password_prompt 进行登录；无配置时不猜登录，不把普通业务命令当凭据。具体设备登录提示仍需目标验收。
- 本机协议测试不能证明真实 HDFS 集群、BusyBox applet、设备登录策略或业务命令语义。

先重跑失败 node id，再对照生产实现、服务端记录和临时 run.log；不要放宽断言、跳过依赖或注入假客户端来消除失败。

2026-10-10 冻结后的最终全量离线回归：**401 passed、1 skipped、1 warning，37.77s**。跳过tests/test_results_selectors.py:150是Windows符号链接权限；warning是test_log_collection.py重复ZIP成员fixture的预期提示。共享脚本契约3文件通过、静态合同16 passed；compileall、JS语法检查、skill quick_validate与git diff --check均通过。

local_simulation真CLI4流程退出码0/0/2/1，直接复制并运行仓库all_interfaces.py（include_hdfs=false）。2048字节SCP/SFTP/FTP主动被动摘要一致、空文件、CtrlB02、一次重启与恢复、源码溯源和print/stderr/fd/继承child输出都通过。
