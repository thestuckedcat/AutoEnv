# 脚本接口与用法

## 最简实现

```python
from autoenv import create_file, get_remote_info, init_script

def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("示例组网", "设备-ssh")])
    file = create_file(paths.runtime_path, "start.sh", "#!/bin/sh\necho READY\n")
    dut.upload(file, "/tmp/demo")
    print(dut.run("echo READY"))
```

不需要 @register_script。完整可复制例子见 [all_interfaces.py](../scripts/all_interfaces.py)，完整流程图与规则见 [重构计划](REFACTOR_PLAN_FOUR_API_CATEGORIES.md)。

## 补充接口：四类

| 接口 | 入参 | 成功出参 | 支持 |
| --- | --- | --- | --- |
| init_script | 无 | ScriptPaths | 脚本初始化 |
| get_remote_info | required_type；允许的 (组网,连接) 数组 | 对应环境对象 | ssh/telnet/ftp，自检 |
| upload | FileHandle；远端目录 | None | SSH/FTP |
| download | 远端目录；filename | FileHandle | SSH/FTP |
| run | command | str | SSH/Telnet；失败/未知异常 |
| execute | command；timeout=None；expect_disconnect=False | CommandResult | SSH/Telnet；分支 |
| read | 无 | str | Telnet |
| wait_reboot | keyword | str | Telnet，被动等待 |
| telnet_wait_exec | keyword；str 或 bytes | str | Telnet，命中发送一次 |
| mark_output | 无 | None | Telnet，更新匹配起点 |
| retry | interval；max_try_times | None | 网络重连、自检；不重放 |
| get_file | 本地目录；精确 filename | FileHandle | 无 glob |
| hdfs_download | 本地目录；config.json 名称 | FileHandle | 独立，无 ctx |
| create_file | 本地目录；filename；str 或 bytes | FileHandle | 任意扩展名 |
| close | 无 | None | paths/remote，幂等 |

FileHandle 提供本地 path、filename 与完整性信息。ScriptPaths 提供 run_id/run_dir/runtime_path/log_path/output_path/params_path/result_path，以及只读 arguments。CommandResult 提供 status、exit_code、stdout、stderr、output、raw_output、phase、时间和错误；`with_failure(message)` 表达业务失败。

SSH upload_type 在环境配置中为 scp/sftp。Telnet 没有文件方法，FTP 没有命令方法。上传失败异常携带操作结果，execute 普通非零/超时/未知返回结果供脚本处理；入参或能力错误仍抛异常。

## 补充分支

SSH 0/非零退出码可判断成功/命令失败。Telnet POSIX 返回码标记有同等语义；prompt/设备 CLI 只有 RESULT_UNKNOWN，需要设备约定的输出关键词。success=False 不能区分命令失败与未知/网络错误，应看 status。关键词控制动作、预期断连 SUCCESS 只证明动作条件满足。

```python
from autoenv import CommandStatus

def check(dut):
    result = dut.execute("status")
    if result.status != CommandStatus.SUCCESS:
        return result
    if "READY" not in result.output:
        return result.with_failure("输出缺少 READY")
    return dut.execute("next_command")
```

串口重启：先 mark_output，再从 SSH reboot，然后 telnet_wait_exec("Press Ctrl+B", b"\x02") 和 wait_reboot("BOOT READY")。bytes 精确发送不补换行，str 按命令发送；发送后保留连接。retry 仅重连自检。

## 补充组件与测试

旧 RunContext、SSHHost/TelnetClient/FTPHost、结果模型、selectors 和日志采集 SDK 是已有高级组件使用的底层接口；新业务脚本不要默认回到旧声明/占位符。低层连接传输细节继续由原客户端负责，新入口只做适配。

机读目录：[script-api-catalog.json](script-api-catalog.json)。本地实际协议验证：[LOCAL_SIMULATION.md](LOCAL_SIMULATION.md)。每条命令/文件动作在 run.log 保留源码位置与结果，output.log 则用于 Web 简洁实时展示。
