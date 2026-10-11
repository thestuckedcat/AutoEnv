# UDK V100 环境拉起与固件替换

两个普通 AutoEnv 脚本共用组网 `SingleUnions_S3`，可从现有 Web 脚本页或 CLI 运行：

| 脚本 | 用途 | 必须绑定的角色 |
| --- | --- | --- |
| [UDK_V100.py](../scripts/UDK_V100.py) | 下载三种包，重启 1260，再上传执行启动脚本并检查进程 | `dut=1260网口`、`bmc=BMC网口`、`console=BMC串口` |
| [UDK_V100_Firmware.py](../scripts/UDK_V100_Firmware.py) | 下载解压固件，经 BMC 网口上传，再从 BMC 串口执行升级脚本 | `bmc=BMC网口`、`console=BMC串口` |

第一份脚本会创建并自检全部三个对象，后续业务命令使用 `dut`。第二份脚本只创建需要的两个 BMC 对象，不依赖第一份脚本先运行。

## 配置组网和下载包

在环境页登记 `SingleUnions_S3`，或编辑本机 `environments/topologies/SingleUnions_S3/connections.json`。可复制 [连接配置模板](examples/SingleUnions_S3.connections.json)：

- `1260网口`：SSH，填写 1260 的实际地址、端口、账号、密码和 `upload_type`。
- `BMC网口`：SSH，填写 BMC 的实际地址、端口、账号、密码和 `upload_type`。
- `BMC串口`：Telnet TCP 串口网关，填写实际地址、端口及需要的登录信息，设置 `shell_mode: "posix"`。

模板的 `127.0.0.1`、22/23 端口、空密码和 `enabled: false` 都是占位。填写真实环境后启用连接；模板本身不能代表一个已可用的设备。这里的串口通过 TCP 网关访问，不能把本机 COM 名称填作 host。完整字段见 [环境登记说明](ENVIRONMENT_REGISTRATION_GUIDE.md)。本机连接文件被 Git 忽略，版本库只保存禁用模板。

项目根目录 [config.json](../config.json) 新增以下四个下载配置：

| 配置名 | 使用脚本 | 目标 |
| --- | --- | --- |
| `ube_driver` | `UDK_V100` | UBE 驱动包 |
| `ube_testcase` | `UDK_V100` | UBE 测试用例包 |
| `udk_drv_v100` | `UDK_V100` | UDK V100 驱动包 |
| `firmware` | `UDK_V100_Firmware` | 包含 `OnChipAsic.bin` 的固件压缩包 |

`ube_driver`、`ube_testcase` 沿用已有 UBE 包的匹配规则；UDK 和固件包名规则是可修改的占位，并未确认实际产物命名。请将配置中的 `image_name` 正则改成真实包名规则，并填写 `link`/`base_link`，或在每次运行时通过 `--package 配置名=HDFS目录` 指定版本目录。`--package` 指定的是 HDFS 路径，不能填本地包路径。下载仍经过原 `hdfs_download()` 和包管理器。

`firmware.target_file` 的 `OnChipAsic.bin` 只是保留的兼容配置，不使下载器自动提取固件；本次脚本下载完整压缩包，随后显式解压和搜索。

## 运行环境拉起

填写并启用组网连接后，先查看静态声明，不连接设备：

```powershell
python -X utf8 -m autoenv.cli describe UDK_V100 --json
```

`describe` 会校验当前登记的组网和候选连接。仅复制禁用模板时，可能因无可用绑定而失败；这属于配置未就绪，不会访问占位地址。`python -X utf8 -m autoenv.cli list --json` 可先查看发现的脚本。

填写组网和包配置后，以下命令会实际访问设备；把 `/实际版本目录/...` 换成真实 HDFS 路径：

```powershell
python -X utf8 -m autoenv.cli run UDK_V100 --non-interactive --topology SingleUnions_S3 --bind "dut=1260网口" --bind "bmc=BMC网口" --bind "console=BMC串口" --package "ube_driver=/实际版本目录/ube_driver" --package "ube_testcase=/实际版本目录/ube_testcase" --package "udk_drv_v100=/实际版本目录/udk_drv_v100"
```

流程按以下顺序执行：

1. 三个对象完成连接自检，三个包下载到本轮 `runtime_path/packages/` 的独立目录。
2. 用 `create_file()` 生成 `start_env.sh`，获得 `handler_start_env`。当前 shell 只打印 TODO，保留给实际安装和拉起命令填写。
3. 读取 1260 的 Linux `/proc/sys/kernel/random/boot_id`，执行一次 `reboot`。
4. 通过 `retry()` 重连和自检，确认有效 `boot_id` 已变化。网口恢复但仍是原 `boot_id` 不算重启完成。失败时不会重发 `reboot`。
5. 确认完成后真实等待 10 秒，再执行 `rm -rf /root/autoenv`。
6. 将三个包和 `start_env.sh` 上传到 `/root/autoenv`，执行 `cd /root/autoenv && sh start_env.sh`。
7. 执行原检查管道 `ps -ef | grep mami | grep devId`。命令必须成功且 stdout 有非空内容，否则脚本失败。

SSH 每条命令使用独立通道，所以 `cd` 和 `sh` 必须放在同一条命令。`&&` 保证目录切换成功后才运行脚本；用户流程中的 `&` 会使 `cd` 在后台执行，不能用于这个顺序。

启动 shell 当前没有真正安装或启动驱动。把 shell 内容补齐前，真实设备只有原先已存在的匹配进程可能让检查通过；脚本通过不能证明 TODO 已完成安装。包按下载时的文件名上传，不会在本步骤自动解包或安装。

进程检查按原管道确认匹配输出。某些系统中执行该管道的 shell 自身命令行也会同时包含 `mami`、`devId`；因此这项结果并不严格证明目标守护进程存在，后续若需要更强判据可再按真实进程名收紧。

| 入参 | 默认值 | 含义 |
| --- | --- | --- |
| `reboot_timeout` | `180.0` | 等待 SSH 恢复并观察到新 `boot_id` 的秒数 |
| `command_timeout` | `300.0` | 读取 `boot_id`、执行启动 shell 和进程检查的命令超时秒数 |

这两个参数必须为有限正数，例如追加 `--arg reboot_timeout=240 --arg command_timeout=600`。单次连接、自检也受连接配置中的超时控制；总重启等待不是强制中断正在进行的底层连接。该重启判据要求 1260 提供 Linux `boot_id`，缺失或格式无效会明确失败。

## 运行固件替换

```powershell
python -X utf8 -m autoenv.cli describe UDK_V100_Firmware --json
python -X utf8 -m autoenv.cli run UDK_V100_Firmware --non-interactive --topology SingleUnions_S3 --bind "bmc=BMC网口" --bind "console=BMC串口" --package "firmware=/实际版本目录/firmware"
```

脚本执行四步：

1. 从 `firmware` 配置下载压缩包，在本轮独立 `runtime_path/firmware` 目录解压。
2. 递归精确搜索 `OnChipAsic.bin`，获得 `fw_handler`。文件缺失会失败；若多个文件同名，使用现有 `accurate_search()` 排序规则中的第一个。
3. 经 BMC SSH 对象将 `fw_handler` 上传到 `/tmp`，实际远端文件名为 `/tmp/OnChipAsic.bin`。
4. 经 BMC 串口对象执行 `cd /data && sh unions.sh`，等命令返回，再检查返回状态和输出。

`unions.sh` 应已存在于 BMC 的 `/data`，并按设备约定使用上传的固件；脚本不创建或替换这个升级 shell。固件外层压缩包使用 SDK 的格式自动识别解压；本流程不反复解开内部压缩包，`OnChipAsic.bin` 应位于外层解压后的目录树中。

唯一业务入参 `command_timeout` 默认 `600.0` 秒，必须为有限正数。可追加 `--arg command_timeout=1200` 延长等待。升级命令不会因超时或断连自动重试，避免重复升级。

## 串口怎样判断执行完毕

这里调用现有 `console.execute()`，按 POSIX shell 的命令结果判断完成。客户端向串口发送业务命令及一个随机、每次不同的返回码标记命令，后者读取 `$?`；正常读取到有效标记及其后的返回提示符才结束。业务命令与标记之间的中间提示符不算完成。结果包含 `status`、`exit_code` 和去掉协议标记的输出。

固件脚本的成功条件同时要求：

1. `status == SUCCESS` 且 `exit_code == 0`，证明 SDK 得到正常完成结果。
2. 返回后的 stdout 包含独立单词 `success`，大小写不敏感，例如 `SUCCESS` 也匹配，`unsuccessful` 不匹配。

`success` 是正常返回之后的额外业务检查，不会提前停止读取。即使先输出 `success`，命令随后非零退出、超时或未取得返回码就断连，也会失败。退出 0 但缺少 `success` 同样失败。`prompt_only` 模式只能给出 `RESULT_UNKNOWN`，不满足本脚本条件；POSIX 模式一直缺少有效返回码标记时也失败。若有效返回码已收到，随后连接在提示符前关闭，SDK 可依据已知退出码返回结果；仍须满足退出 0 和业务 success 的共同条件。

真实 BMC 必须允许普通 POSIX shell 命令和返回码标记，需要在真实目标验收提示符、终端输出方式和命令时序。若 `unions.sh` 自己把升级放到后台并立即退出，上述完成只证明 shell 已返回，实际刷写完成还要由该 shell 自身等待并输出最终 `success`；不能用客户端推断后台工作结束。

## 从网页运行和定位结果

只使用现有入口 `python -X utf8 startWeb.py`，访问 `http://127.0.0.1:8765/`。环境页登记组网后，在脚本页分别选择 `UDK_V100` 或 `UDK_V100_Firmware`，按表绑定对象、选择 HDFS 包路径并设置超时。页面使用同一真实 CLI，不增加另一套执行逻辑。

CLI 完成时 stdout 给出 `run.log` 绝对路径；退出码 `0` 为成功、`1` 为运行失败、`2` 为准备失败。同目录 `output.log` 是网页展示的简洁动作结果。每轮包文件、生成的 shell 和固件解压目录均位于自己的 runtime，不会借用上一轮下载结果。

## 本地模拟与实际验收边界

本地验证使用可配合这三个角色的 loopback SSH/SCP/SFTP 与 Telnet 模拟设备，并启动本地 WebHDFS 服务，通过真实 CLI 和生产客户端执行。只替换环境配置和 HDFS endpoint；HDFS 选择/下载、包上传、连接、自检、命令发送、结果解析、固件解压和重启后的 10 秒等待不替换成假成功。

模拟设备记录 shell 命令并返回受控输出；`rm -rf`、`reboot`、升级 shell 不会在测试宿主的操作系统执行。上传仍走真实协议并落盘，校验包、启动 shell 和固件字节。覆盖成功以及重启身份未变化、无匹配进程、固件缺失、串口非零、缺少 success、超时/断连和未知结果等失败路径。最终执行结果见 [本次功能记录](../newFeature/2026-10-11-udk-v100-workflows.md) 和 [测试说明](../tests/README.md)。

可在项目根目录运行相应本地 UT：

```powershell
python -X utf8 -m pytest tests/test_udk_v100_workflows.py tests/test_telnet_client.py tests/test_generated_script_contract.py -q
```

这些测试启动临时 loopback 服务，动态填写隔离组网，不会使用本机 `SingleUnions_S3` 的真实地址；每个测试的输出和临时设备目录相互隔离。

本轮没有访问真实 HDFS、1260 或 BMC。真实包名/版本路径、设备账号及端口、驱动拉起 shell、`unions.sh` 的刷写逻辑、串口提示符时序和硬件重启耗时仍需针对实际环境确认。

其他公共 API 用法见 [接口说明](SCRIPT_API_ANALYSIS.md)；本地协议模拟的通用机制见 [LOCAL_SIMULATION.md](LOCAL_SIMULATION.md)。
