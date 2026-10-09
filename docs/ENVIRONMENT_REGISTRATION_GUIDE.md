# 环境登记与脚本编写

## 最简实现

网页 `python -X utf8 startWeb.py` 的环境页登记组网和连接。一个 name 代表一个 SSH、Telnet 或 FTP 连接；脚本按两级身份申请：

```python
from autoenv import get_remote_info, init_script

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("双机主备", "主机-ssh")])
    console = get_remote_info("telnet", [("双机主备", "主机-串口")])
    print(dut.run("echo READY"))
```

运行：

```powershell
python -X utf8 -m autoenv.cli run demo --non-interactive --topology "双机主备" --bind "dut=主机-ssh" --bind "console=主机-串口"
```

不使用 `@register_script` 或 ctx 注册。申请对象前校验类型/白名单，完成一轮连接与功能自检，记录可用/不可用。不可用可显式 retry，不能默默换设备。

## 补充：配置与选择

配置保存在 `environments/topologies/<组网>/connections.json`，顶层数组：

```json
[
  {"name":"主机-ssh","type":"ssh","host":"192.0.2.10","port":22,"username":"root","password":"","upload_type":"scp","connect_timeout":30,"enabled":true},
  {"name":"主机-串口","type":"telnet","host":"192.0.2.20","port":23,"timeout":30,"shell_mode":"posix","enabled":true},
  {"name":"文件-ftp","type":"ftp","host":"192.0.2.30","port":21,"username":"user","password":"","passive":true,"timeout":30,"enabled":true}
]
```

SSH 的 upload_type 是环境字段，决定同一对象 upload/download 使用的协议。FTP passive 控制主动/被动模式。Telnet shell_mode 为 posix/prompt_only/auto；非 POSIX 的 prompt/bootloader 配置 `probe_command` 与 `probe_keyword`，只发送设备明确允许的自检命令，不能假定 printf 可用。

需要 Telnet 登录时配置 username/password；提示不同于默认 login:/Password: 时设置 login_prompt/password_prompt。登录后再按原命令模式自检；不在未登录时发送业务命令，也不把未知提示猜作成功。

每个固定 get_remote_info 赋值点是独立角色。白名单是字面量 pair 数组，组网名称不能与连接名拼成冒号字符串。全部角色可用组网取交集，执行只选一次；各角色的二级候选只在本组网自己的白名单。回车继承上次有效选择；改类型、改配置、删目标或变白名单都会使历史失效。完整非交互命令必须 bind 所有角色。

Telnet 串口是 TCP 网关，不是本地 COM，档案波特率不改变物理设备。Telnet 没有文件方法，FTP 没有命令方法。SSH 自检确认命令/退出状态与选定文件传输能力，FTP 自检认证控制通道；真实文件校验在传输时完成。

## 补充：命令、重启与文件

普通命令 `run()->str`；分支命令 `execute()->CommandResult`。SSH 退出码 0/非零能判断成功/失败；Telnet POSIX 可以读返回码标记，prompt 模式返回 RESULT_UNKNOWN，需要明确业务关键词。网络错误/超时/断连不能当非零业务失败，部分输出不能默认证明完整完成。

`console.mark_output()` 后从网口重启，再 `console.telnet_wait_exec("Press Ctrl+B", b"\x02")` 精确发送控制字；保持连接用于后续读取。`wait_reboot("BOOT READY")` 只监听，`retry(1, 5)` 只重连与自检，不重复发 reboot。

本地 `get_file(path, filename)` 不用通配符；`create_file(path, filename, content)` 可创建任意扩展名；`hdfs_download(localpath, config_name)` 独立复用 config.json。上传接收 handler，下载返回 handler，远端无需 handler。外部文件借用不删除，下载/生成文件在本轮 runtime 中可溯源。

## 组件与测试

完整脚本和调用表见 [all_interfaces.py](../scripts/all_interfaces.py)、[SCRIPT_API_ANALYSIS.md](SCRIPT_API_ANALYSIS.md)。静态检查：

```powershell
python -X utf8 .agents/skills/autoenv-script-generator/scripts/validate_environment_script.py scripts/demo.py
python -X utf8 -m autoenv.cli describe demo --json
```

真实协议的纯本地验证见 [LOCAL_SIMULATION.md](LOCAL_SIMULATION.md)。环境密码/历史参数文件保持本地，不提交到版本库。
