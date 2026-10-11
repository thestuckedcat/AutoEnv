# 本地模拟：搭建、命令与结果

## 最简实现：一条命令验证新流程

在仓库根目录执行，要求 Python 3.11+、Paramiko、scp、requests 和 pytest：

```powershell
python -m pip install -e ".[test]"
python -X utf8 tools/local_simulation.py
```

工具在 logs/local-simulation-时间/project 生成隔离脚本、连接配置和输出；使用本机临时端口启动真实 SSH/SCP/SFTP、Telnet POSIX/设备控制台、FTP 主动/被动服务，然后以 stdin=DEVNULL 执行真正的 `python -m autoenv.cli` 子进程。完成打印实际命令、退出码、完整日志路径及 report.json 路径。它不启动 HDFS、不下载 HDFS；config.json 中的 A1 只是全量示例的静态声明，include_hdfs=false。

Windows 受限沙箱可能禁止子进程连接本机端口并报 WinError 10013。本次在仅限 127.0.0.1 的本地验证授权下运行成功；正常本地终端不需要修改生产连接逻辑来规避此限制。

保留模拟服务供手动执行：

```powershell
python -X utf8 tools/local_simulation.py --serve
```

它先完成自动验证，然后保持服务。进入打印的 project 目录，复制 report.json 中的真实 CLI 命令即可；Ctrl+C 停止服务。不能复用服务结束后的临时端口；重新运行工具会新建配置和端口。

## 补充：实际运行命令与结果

2026-10-10 执行：

```powershell
python -X utf8 tools/local_simulation.py
```

冻结版本再次执行通过。每次工具运行动态生成 `logs/local-simulation-<run-id>/`，下文以 `<simulation-root>` 表示工具打印的实际目录；报告为 `<simulation-root>/report.json`。工具执行的命令如下，cwd为该目录的project，使用当前安装的Python解释器。

```powershell
python -X utf8 -m autoenv.cli run loopback_workflow --non-interactive --topology loopback --bind scp=ssh-scp --bind sftp=ssh-sftp --bind posix=telnet-posix --bind console=telnet-console --bind passive=ftp-passive --bind active=ftp-active
python -X utf8 -m autoenv.cli run all_interfaces --non-interactive --topology "示例组网" --bind "dut=设备-ssh" --bind "console=设备-串口" --bind "ftp=文件-ftp" --arg local_dir=inputs --arg filename=input.bin --arg include_hdfs=false
python -X utf8 -m autoenv.cli run runtime_failure --non-interactive --topology loopback --bind dut=missing
python -X utf8 -m autoenv.cli run runtime_failure --non-interactive --topology loopback --bind dut=ssh-scp
```

| 命令 | 实际退出码 / 状态 | 结果证据 |
| --- | --- | --- |
| loopback_workflow | 0 / success | 全协议流程、分支、Ctrl+B、恢复、完整输出捕获 |
| 仓库 all_interfaces 示例 | 0 / success | 直接复制当前示例，HDFS开关关闭，其余对象/文件/命令调用成功 |
| dut=missing | 2 / preparation_failed | 准备日志存在；错误绑定没有新增 SSH 认证/连接 |
| runtime_failure | 1 / command_failed | demo fail 实际送达，退出 7，run 抛异常；此前 print 与异常均入日志 |

四次执行stdout均只有一行实际存在的绝对run.log路径，stderr均为空。每个实际目录独立生成，报告的 `runs` 数组按上述命令顺序保存 `command`、`exit_code`、`status` 和 `log_path`；读取相应 `log_path` 即可定位本轮完整日志，不能复用别人的运行目录。

工具最终输出：`PASS: real protocol bytes, command delivery, branches, Ctrl+B, recovery, CLI and logs`。记录目录在 Git 忽略的 logs 内，重新运行的编号不同；report.json 保存本轮精确路径、命令、服务端收到的命令/传输记录和摘要。

审查完整协议流程时读取 `runs[0].log_path`，发布示例读取 `runs[1].log_path`；相应简洁输出是该日志同目录的 `output.log`。日志与报告只在本机生成、不提交版本库。其他checkout重新运行工具即可得到自己的地址和证据。

## 补充：模拟环境与验证内容

| 服务/组件 | 搭建方式 | 实际验证 |
| --- | --- | --- |
| SSH | tests/local_remote/ssh.py，真实 Paramiko 握手/密码认证/channel | self-check、execute/run、退出 0/7、stderr、预期断连、retry后命令送达 |
| SCP | 同一 SSH peer 的 SCP sink/source wire protocol | 上传和下载确认字节、实际落盘、大小/摘要 |
| SFTP | 同一 SSH peer 的 subsystem / disk server | 双向传输，使用生产 SFTP 方法，实际落盘 |
| Telnet POSIX | tests/local_remote/telnet.py，真实 TCP/IAC/提示符/返回码 marker | 发送命令，退出 0/3，输出解析 |
| 设备串口网关 | 被动 TCP peer，prompt_only，status返回READY，允许probe | 协议状态 RESULT_UNKNOWN、业务关键词分支、mark_output、关键词动作、精确Ctrl+B、后续等待/read |
| FTP | tests/local_remote/ftp.py，真实控制和主动/被动数据socket | STOR/RETR/PWD/SIZE，二进制及空文件双向字节 |
| 文件服务 | create_file/get_file/各远端download | 文本、二进制、精确filename，下载handler继续上传 |
| CLI/session | 真CLI子进程，无交互stdin | 退出0/1/2、准备零连接、全输出run.log、源文件:行号溯源 |

二进制 payload 为 bytes(range(256)) 重复 8 次，2048 字节，包含 NUL、IAC、控制字符及非 UTF-8 数据。SHA-256 为 `10fc3c51a152e90e5b90319b601d92ccf37290ef53c35ff92507687d8a911a08`。SCP → SFTP → FTP被动 → FTP主动逐次双向传输，每次检查收到的磁盘字节和最终 handler 内容。额外验证空文件，FTP data_modes 同时包含 active/passive。

服务端只记录任意业务 shell，返回预设 stdout/stderr/exitcode，不执行这些命令。SCP/SFTP/FTP 传输没有替换客户端、没有假返回成功、没有本地 copy 冒充网络下载。SSH metadata 命令针对隔离真实文件计算 mkdir/MD5/大小。

`demo fail` 退出 7 后按 status/exitcode 执行 `demo recover`；Telnet POSIX `demo fail` 退出 3。prompt设备 `status` 输出 READY，协议仍 RESULT_UNKNOWN，脚本显式业务判定。网口 `demo reboot` 只收到一次；工具观察命令后让串口 emit Press Ctrl+B，实际收到十六进制 02；模拟设备随后发 BOOT READY，wait_reboot读取，SSH retry重连自检后 demo after reboot 送达。Ctrl+B后的BOOT READY只是此模拟设备约定，真实bootloader应按自己的模式标志调整。

run.log 验证 SCRIPT_STDOUT_MARKER、SCRIPT_STDERR_MARKER、FD_STDOUT_MARKER、CHILD_STDOUT_MARKER 和 BINARY_ROUNDTRIP_OK 全部存在；普通 print、Python stderr、os.write 与继承输出的本地子进程均被捕获。每条 API 的 FLOW START/END 和原命令结果能追到 loopback_workflow.py 的行号。output.log 只显示带源行号的简洁开始/结果，网页无需同步大段远端输出。

每轮另外保存workflow-source.json中的路径/revision/source。即使之后修改本地.py，日志行号仍对应已归档流程；result.json的final_operation保留真正决定最终状态的返回失败或未恢复自检。

## 组件、UT 与未验收部分

UDK V100 两个仓库脚本的完整本地模拟位于 `tests/test_udk_v100_workflows.py`，使用隔离的 `SingleUnions_S3` 登记和本地 WebHDFS 端点，实际执行生产 CLI、解压、文件上传、命令与 10 秒等待。业务 shell 由模拟设备响应，不在本机执行；运行方法和判据见 [UDK V100 工作流](UDK_V100_WORKFLOWS.md)。

```powershell
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py
python -X utf8 -m pytest tests/test_generated_script_contract.py -q
python -X utf8 -m pytest tests/test_remote_environment.py -q
python -X utf8 -m pytest
```

本轮共享脚本契约实际通过3文件；在补充keyword/别名/with/默认类型一致性后，新/旧静态合同16 passed，与script_files/session联合32 passed。完整新UT结果见 [tests/README.md](../tests/README.md) 与本次feature记录。旧WebHDFS真HTTP UT继续保留，但本轮本地工具没有HDFS调用，不代表真实HDFS集群验收；真实设备、串口启动时序、FTP服务器实现差异和业务命令本身仍需针对目标验证。
