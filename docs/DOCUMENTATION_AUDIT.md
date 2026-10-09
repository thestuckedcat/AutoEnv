# 文档一致性检查

## 最简实现：当前入口

2026-10-10 基线为普通文件脚本第一版。首次使用读 [QUICK_START.md](QUICK_START.md)，复制 scripts/example.py 或 scripts/template.py；新脚本使用 main()/init_script()/get_remote_info()/FileHandle，不使用注册装饰器。

CLI 执行完成只输出实际 run.log 路径；Web 表单调用真实 CLI 并显示独立 output.log 简洁结果。Web 唯一启动命令 `python -X utf8 startWeb.py` 和固定端点 `http://127.0.0.1:8765/` 保持。

## 补充：文档职责与实现对应

| 文档 | 对应内容 |
| --- | --- |
| README / QUICK_START | 安装、最小脚本与CLI/网页入口 |
| ENVIRONMENT_REGISTRATION_GUIDE | 两级集中连接数组、SSH upload_type、自检/恢复、白名单与历史 |
| REFACTOR_PLAN_FOUR_API_CATEGORIES | 全部讨论决定、四类接口入参出参和图、实施/验收约定 |
| SCRIPT_API_ANALYSIS / script-api-catalog.json | 当前新业务接口表与机器目录 |
| AutoEnv-Refactor-Detailed-Design | 静态发现、session、原协议客户端适配、日志/输出分离 |
| LOCAL_SIMULATION | 除HDFS外真实本地协议搭建、CLI命令和实测字节/结果 |
| WEB_ARCHITECTURE_AND_HANDOFF / web_usage | 真实CLI启动链、常用页面、简洁输出实时同步 |
| tests/README | 真本地协议UT、旧SDK兼容组件、失败排查与边界 |
| script-generator skill / validator | 快速从脚本/流程生成普通Python，静态契约与执行边界 |

保留旧 RunContext/低层SDK供高级组件使用，旧feature记录具有其历史时间语境；它们不重新成为新用户默认入口。新增FTP下载使用真实RETR数据通道，SCP/SFTP/HDFS验证路径不改为假传输。

## 补充：检查与边界

公共名称和语义变化后，搜索实现/导出/例子/文档/skill/测试，核对本轮参数类型str/int/float/bool/list/dict、prompt_only模式、upload_type配置和FileHandle返回。Markdown本地链接、代码块与JSON应可解析；CLI list/describe只做静态发现。

本轮文档中实际结果对应 LOCAL_SIMULATION 的报告：2048字节真实SCP/SFTP/FTP主动被动双向与空文件；SSH/Telnet状态、CtrlB、retry与CLI0/1/2；全部print/stderr/fd/继承子进程输出及源码溯源。真实设备、HDFS集群与业务shell效果不由本地模拟证明。
