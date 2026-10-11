# UDK V100 环境拉起与 BMC 固件替换

## 意图和用户行为

新增两个可直接由已有 CLI/Web 发现的普通 Python 脚本，共用 `SingleUnions_S3` 组网：`UDK_V100` 负责三种包下载、1260 重启、上传启动 shell 和进程检查；`UDK_V100_Firmware` 负责下载解压固件、上传 `OnChipAsic.bin` 并从 BMC 串口运行升级 shell。详细配置与运行命令见 [使用说明](../docs/UDK_V100_WORKFLOWS.md)。

真实环境信息和 HDFS 包路径允许明确占位，生成的 `start_env.sh` 按用户要求只保留 TODO。业务脚本复用现有 SDK，不另建 SSH、Telnet、文件传输或 Web 执行实现。

## 架构和数据流

```text
CLI / 现有 Web
  -> 静态发现 scripts/*.py 的 main、连接白名单和 SCRIPT_ARGUMENTS
  -> 一次选择 SingleUnions_S3、绑定对象及 HDFS 路径
  -> init_script / get_remote_info / 对象连接自检
  -> hdfs_download -> config.json -> 本轮 FileHandle
  -> UDK_V100: 读 boot_id -> 单次 reboot -> retry/self-check + boot_id 变化
       -> 等待 10 秒 -> 清理 /root/autoenv -> 上传包和 TODO shell
       -> 同一 SSH 通道 cd && sh -> 进程检查
  -> UDK_V100_Firmware: 本地解压 -> 精确递归搜索 OnChipAsic.bin
       -> BMC SSH 上传 /tmp -> BMC Telnet 执行 unions.sh
       -> 正常完成且退出 0 -> 额外 success 单词检查
  -> run.log / output.log / result.json / 本轮 runtime
```

每条 `get_remote_info` 在 main 初始化区直接使用字面量白名单。环境脚本宿主统一管理连接、日志、源码溯源和退出码，直接执行 `.py` 的 guard 也转发同一 CLI。

## 接口与重要规则

| 脚本 | 角色 | HDFS 配置 | 业务入参 |
| --- | --- | --- | --- |
| `UDK_V100` | SSH `dut=1260网口`、SSH `bmc=BMC网口`、Telnet `console=BMC串口` | `ube_driver`、`ube_testcase`、`udk_drv_v100` | `reboot_timeout: float = 180.0`、`command_timeout: float = 300.0` |
| `UDK_V100_Firmware` | SSH `bmc=BMC网口`、Telnet `console=BMC串口` | `firmware` | `command_timeout: float = 600.0` |

- 第一脚本创建并自检全部三个对象，再用 1260 执行业务。第二脚本只要求 BMC 两个连接，可以独立运行。
- HDFS 包保存在每轮独立目录；配置的实际路径与 UDK/固件包命名尚未提供，版本库清晰保留占位。SSH 上传方式沿用连接的 `scp`/`sftp` 配置。
- 重启只发送一次。确认 Linux `boot_id` 有效且变化，配合连接和命令自检，不能把 `reboot` 返回或旧系统 SSH 仍可连当作完成。观察变化后真实 sleep 10 秒。
- 环境拉起保留用户固定清理命令 `rm -rf /root/autoenv`，上传三个包和 `start_env.sh`；shell 中只打印 TODO，不虚构实际驱动安装流程。
- `cd` 与 `sh` 使用 `&&` 放同一远端命令，确保目录切换后运行；不使用会后台执行 `cd` 的 `&`。
- 进程检查保留 `ps -ef | grep mami | grep devId`，要求命令成功且 stdout 非空。
- 固件使用已有自动识别解压和递归 `accurate_search`，同名多文件按现有排序返回第一个；没有目标文件明确失败。只解压下载的外层包。
- BMC `/data/unions.sh` 由设备提供，上传的固件为 `/tmp/OnChipAsic.bin`。脚本等待正常命令返回，然后要求 `SUCCESS`、退出 0 及不区分大小写的独立单词 `success`。
- success 输出不提前终止执行；非零、超时、未取得返回码的断连、prompt_only 未知或有效返回码标记缺失都失败，不伪造完成或自动重放升级。收到有效退出码后断连保留 SDK 的已知结果语义，仍检查退出码与业务输出。
- POSIX 串口完成依赖随机返回码标记和提示符解析。本次修正读取边界，等待有效标记之后的提示符，避免业务命令与标记之间的中间提示符被误判为完成。缺失或畸形标记不会因提示符直接宣告完成；真实交互 shell 时序仍需设备验收，后台刷写的完成应由 `unions.sh` 自身负责。

## 主要文件

- [scripts/UDK_V100.py](../scripts/UDK_V100.py)、[scripts/UDK_V100_Firmware.py](../scripts/UDK_V100_Firmware.py)：两个普通业务流程。
- [config.json](../config.json)：四种包的选择配置。
- [禁用连接模板](../docs/examples/SingleUnions_S3.connections.json)：三个连接的可提交占位；实际连接文件保留本机。
- [使用文档](../docs/UDK_V100_WORKFLOWS.md)：步骤、CLI/Web、超时、串口完成和模拟边界。
- [autoenv/telnet_client.py](../autoenv/telnet_client.py)：POSIX 标记与后续提示符的完成边界修正，沿用原 execute 接口和结果模型。
- [tests/test_udk_v100_workflows.py](../tests/test_udk_v100_workflows.py)：生产 CLI 与 loopback 设备/HDFS 的两个完整流程和失败分支。
- [tests/test_telnet_client.py](../tests/test_telnet_client.py)：中间提示符、旧标记、退出 0/非零与缺少标记的协议回归。
- 测试、公共入口和脚本 skill 的同步由本次实现一并完成；测试结果在下节记录。

## 验证和实际边界

新工作流 UT `tests/test_udk_v100_workflows.py`：**22 passed，39.10s**。使用真正的 CLI、生产 SSH/SCP/SFTP 和 Telnet 客户端访问本地 loopback 模拟器，并经真实 HTTP 调用本地 WebHDFS 服务；仅替换设备环境配置和 HDFS endpoint factory。包及固件上传检查实际字节，命令顺序与失败分支检查服务端记录，10 秒等待不跳过。

覆盖两个脚本的静态发现和同组网声明、HDFS 下载失败提前停止、有效及未变化/无效 boot_id、单次 reboot、真实 10 秒等待、三包及 TODO shell 上传、启动失败和空进程输出；固件覆盖 ZIP/TAR.GZ、SFTP/SCP、分片 SUCCESS、非零退出、提前 success、超时、断连、缺少返回码标记、prompt_only 未知和精确镜像缺失。

其他验证：

- Telnet 回归：**68 passed，2.09s**，含 5 个新增中间提示符/标记边界用例。
- 公共环境脚本合同：**16 passed，0.13s**；静态脚本 validator 对全部 5 个脚本通过。
- `compileall autoenv scripts tests`、CLI `list --json` 发现两个新脚本通过；两个 `describe` 由已启用隔离组网的 UT 验证，根目录禁用模板保持禁用。
- 3 个相关项目 skills 校验、SDD 校验及 `git diff --check` 通过。根目录文档指引包含全部 68 份维护文档，82 个本地目标链接存在。
- 公共导出和 API 目录的已有 `execute`/结果语义复核一致，无新增公共 API；最小示例和 `all_interfaces.py` 无需改变。

最终完整离线回归：**781 passed、2 skipped、1 warning，160.44s**，临时目录为 `logs/pytest-udk-full`。两个跳过均为既有 Windows 符号链接权限限制；warning 为既有重复 ZIP 日志成员 fixture，未新增测试失败。

本轮未授权也未访问真实 HDFS 或硬件。真实包路径/命名、设备连接信息、TODO 驱动启动命令、BMC 升级 shell 及其刷写效果、真实重启和串口时序均未验收。

普通脚本宿主与协议接口延续 [四类接口第一版](2026-10-10-simple-script-api-v1.md)，通用本地协议模拟见 [2026-10-09-local-protocol-testing.md](2026-10-09-local-protocol-testing.md)。
