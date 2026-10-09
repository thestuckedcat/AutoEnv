# AutoEnv 网页快速开始

## 最简实现

在仓库根目录执行唯一启动命令：

```powershell
python -X utf8 startWeb.py
```

固定打开 `http://127.0.0.1:8765/`；端口冲突明确失败，无备用入口或host/port参数。

1. 在“环境注册”登记一级组网和其中的SSH/Telnet/FTP连接。一个连接名代表一个对象，SSH上传方式在upload_type中选scp/sftp。
2. 在“运行脚本”选择普通scripts/*.py。main首先init_script，再显式get_remote_info(type, allowed_envs)，无需注册装饰器。
3. 选择一次一级组网，为各对象角色选择该组网白名单内的具体连接；填写所需HDFS/业务参数。
4. 检查命令预览后运行。页面调用真实非交互CLI，持续展示每条动作的简洁开始/结果，完成给出退出状态与完整日志地址。

最小脚本与可复制全部用法见 [项目快速开始](../docs/QUICK_START.md)、[all_interfaces.py](../scripts/all_interfaces.py)。

## 补充功能

环境数据在environments/topologies/<组网>/connections.json；用户名/密码集中登记，脚本不重复填写。SSH支持命令与SCP/SFTP文件，Telnet支持命令/被动串口读取/关键词CtrlB，FTP支持主动/被动上传与下载。对象创建完成自检，不可用在结果中清楚显示，重试只重连自检。

表单来自脚本静态描述：环境赋值变量为绑定角色，字面量白名单限定候选；SCRIPT_ARGUMENTS生成业务输入，hdfs_download配置名生成包输入。发现脚本不执行或连接目标。一级组网必须同时满足所有角色，不能从不同组网拼装一次流程。

run.log完整记录源文件行号、命令、执行结果、远端输出与print；output.log只记录简洁动作结果，实时输出框显示后者。workflow-source.json保存当次完整Python源码，脚本后来修改仍可追溯。CLI最终stdout只一行run.log地址，0/1/2分别成功/运行失败/准备失败。

普通脚本是常用入口。历史专用Tools/高级组件仍保留其SDK契约，但不在新主页面作为重复执行器推荐。SSH传输、Telnet命令framing与FTP真实数据通道复用原验证实现。

## 组件与测试

[网页用法](../docs/web_usage.md)说明实际表单与CLI；[接手设计](../docs/WEB_ARCHITECTURE_AND_HANDOFF.md)说明HTTP/子进程/日志轮询；[LOCAL_SIMULATION.md](../docs/LOCAL_SIMULATION.md)提供除HDFS外的真实本地协议搭建、命令/结果。Web bridge测试见tests/test_web_cli_bridge.py。
