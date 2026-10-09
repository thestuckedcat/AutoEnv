# AutoEnv 本地协议测试与排查

默认全量测试会启动本机临时端口的协议服务。SSH、SCP、SFTP、Telnet、FTP 和 WebHDFS 使用生产客户端及真实 TCP/协议通道，文件传输方法不打桩。服务全部绑定 `127.0.0.1`；每条测试隔离文件与连接并清理资源。`conftest.py` 的连接守卫拒绝非 loopback 连接，避免测试意外访问已注册的实验室目标。

## 最简实现：运行

```powershell
python -m pip install -e ".[test]"
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

| 文件 | 覆盖目标 | 失败优先检查 |
| --- | --- | --- |
| `test_ssh_host.py` | 真实命令、关键词响应、鉴权/断连/复用、双向 SCP/SFTP、二进制/空文件/Unicode、MD5、覆盖、唯一匹配、四路 SCP、截断/失败清理和占位符 | ssh_host、commands/transfers、run.log |
| `test_telnet_client.py` | 真实 TCP、模式、IAC 分片、marker、退格/ANSI/UTF-8、超时/断连、Ctrl+B/原始字节、占位符来源及重连 | telnet_client、commands/probes/raw_inputs |
| `test_interfaces_web_ftp.py` | FTP 主动/被动真实传输与校验；保留 LaunchRequest、固定 Web 启动、资源、Tool、PTY/UI 边界 | ftp_host、FTP 记录、Web/interface |
| `test_package_extractor.py` | HTTP 包选择/重定向/原子替换/清理；本地 TAR/ZIP 提取、安全与摘要 | package_manager、HTTP 记录、extractor |
| `test_runtime_registry.py` | 元数据、参数保存、HTTP 下载、newest/rerun、独立目录、组合脚本和 func 生命周期 | runtime/registry、params/result/last-run |
| `test_local_protocol_workflow.py` | WebHDFS → ZIP → SCP/SFTP → shell → SSH/Telnet → SCP/SFTP → FTP → 日志 → func → rerun | 每步结果、双方字节、最终记录及命令 |
| `test_log_collection.py` | 所有日志经真实 SCP 获取；归档、来源隔离、strict/legacy/metadata、SQLite/查询/export/Find、workflow | 真实 SSHHost、SDK 请求、logs/log_query |
| `test_command_files.py` | 上传映射、Host 隔离、占位符及原样 shell 文本 | command_files |
| `test_results_selectors.py` | 结果、选择器安全、序列化、脱敏及 recorder 摘要 | results/selectors/recorder |
| `test_generated_script_contract.py` | 全部 scripts 的共享 AST 契约及非法调用反例 | 验证器报错行 |
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
