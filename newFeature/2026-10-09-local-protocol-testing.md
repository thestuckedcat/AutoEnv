# 本地协议测试与脚本接口分析

## 意图与行为

创建新脚本前能够查阅完整可调用接口、入参默认值、出参模型和通常流程；测试默认在本机模拟远端，业务命令仅记录与响应，文件经过真实协议传输。此前连接/下载/上传假客户端被替换，纯计算/CLI/静态合同测试保留有意义的隔离。

## 架构与数据流

测试生产客户端 → loopback 临时端口 → 本地 SSH/SFTP/SCP、Telnet、FTP、WebHDFS 服务。SSH/FTP 服务文件落到隔离远端目录；SCP/SFTP/FTP 字节穿过真实套接字与协议消息，WebHDFS 经 HTTP OPEN 重定向和流式正文；接收文件与源字节/摘要比对。服务端记录命令、原始响应、传输和并发。连接守卫只允许 loopback。

完整流程覆盖注册脚本、HTTP 下载、ZIP 提取、SCP/SFTP 上传、占位符/shell 生成、SSH/Telnet 命令、远端下载结果复用到 FTP、真实 SCP 日志收集、func 和 rerun。日志处理 UT 的请求代理实际委托 SSHHost/SCPClient，没有本地 copy2 传输或伪成功结果。

## 接口与重要语义

生产 API 签名保持不变；`docs/script-api-catalog.json` 和 `SCRIPT_API_ANALYSIS.md` 收录 80 接口、30 模型。测试端点使用 context manager/start/close 管理；文件 seed 只准备远端输入，不参与客户端传输。

真实协议测试发现并修复：HTTP 404 缺少目录不存在映射、408/504 下载超时分类、Paramiko -1 无退出状态误判为非零命令失败、Telnet 退格正则误用 word boundary。其他公共脚本例子和技能无需变更，这些修复恢复原有结果契约。

迟缓 SSH banner 当前在 Paramiko 3.5 返回 PROTOCOL_ERROR/CONNECT，是已测试的现有分类，尚未统一为 CONNECTION_TIMEOUT。模拟 shell 不执行安装/重启业务；Telnet 登录能力仍不提供。

## 主要文件

- `tests/local_remote/{ssh,telnet,ftp,webhdfs}.py`、`tests/conftest.py`
- 重写的 SSH/Telnet/FTP/HDFS/runtime/log tests 与 `test_local_protocol_workflow.py`
- `autoenv/{ssh_host,telnet_client,package_manager}.py` 的结果/解析修复
- 接口分析、机器目录、tests README、README、快速入门、详细设计和注册指南

## 验证

- 使用项目本地 `.test-deps` 中的 Paramiko 3.5.1 / scp 0.16.2 及 Python 3.11.4 执行全量：`python -X utf8 -m pytest -q --disable-warnings`，346 passed、1 skipped（Windows 不允许创建符号链接）、1 dependency warning，22.19 秒。协议传输用例没有跳过。
- 环境脚本 AST 契约通过：2 个文件；注册发现只导入并列出 7 个注册脚本，没有执行函数体。
- `compileall autoenv scripts tests`、SDD 技能验证器及 `git diff --check` 通过。直接相关实现、导出、示例、指南和脚本生成技能已复核；生产 API 签名未变，无需修改 scripts/config/生成技能。
- 交互图遍历 12 个节点、全部 80 个接口，在 736/320 像素宽度下无横向溢出或 JavaScript 错误；结果模型含 30 项。

未运行注册的真实设备环境脚本；真实 HDFS/SSH/Telnet/FTP 设备与业务逻辑仍未验证。
