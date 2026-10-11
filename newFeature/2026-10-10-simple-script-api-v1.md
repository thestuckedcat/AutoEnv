# 普通 Python 脚本与四类接口第一版

## 最简实现

脚本先 `init_script()`，再 `get_remote_info(type, allowed_envs)` 申请已自检对象，文件来源统一为 FileHandle。普通 `scripts/*.py` 的 main() 不需要注册装饰器。CLI 一次提供组网、对象绑定、HDFS路径和业务参数，完成只返回完整日志路径与退出码；Web使用真实CLI并显示独立简洁输出。

意图、全部讨论决定和入参出参图归档在 [重构计划](../docs/REFACTOR_PLAN_FOUR_API_CATEGORIES.md)。最小模板和全部用法在 scripts/example.py、template.py、all_interfaces.py；快速开始在 docs/QUICK_START.md。

## 补充接口与架构

- 独立 `get_file(path, filename)`、`create_file(path, filename, content)`、`hdfs_download(localpath, config_name)`；不需要 ctx。upload 成功 None，SSH/FTP download 返回本地 handler。
- 两级 environments/topologies/<组网>/connections.json 集中登记；SSH upload_type 为 scp/sftp配置字段，FTP支持真实下载。各申请角色固定类型/白名单，组网交集与历史预检在连接前完成。
- SSH/Telnet run返回字符串，失败/未知异常；execute返回CommandResult用于status/exitcode/output分支。TelnetPOSIX返回码与prompt未知保持区别，不伪造业务成功退出码。
- 串口 read/wait_reboot/mark_output/telnet_wait_exec 被动读流与精确Ctrl+B保留连接，retry只恢复自检不重放命令。已验证原连接/传输细节不改成新实现。
- script_files静态发现 → catalog/session预检 → 实际CLI执行 → init/session owner → 原生产client → recorder/run.log及output.log。每条动作溯源到脚本源码行号和原结果。
- workflow-source.json保存当次脚本路径/revision/完整source，后续修改仍可按当次流程溯源；final_operation保留返回失败/未恢复自检，不被随后read覆盖。可选None参数历史保持有效，直接文件guard也转发同CLI。
- autoenv-script-generator skill/validator更新普通Python生成规则；旧低层SDK保留给现有高级组件，新默认文档不推荐decorator/ctx/资源标签/隐式占位符。

主文件：autoenv/script_files.py、remote_catalog.py、script_session.py、file_handles.py、remote_environment.py、cli.py；原ssh_host/telnet_client/ftp_host承担协议；Web bridge/pages、script skill、示例与tools/local_simulation.py同步。

## 测试与实际边界

2026-10-10冻结后的最终全量离线回归：**401 passed、1 skipped、1 warning，37.77s**。Windows符号链接权限导致tests/test_results_selectors.py:150跳过；test_log_collection.py重复ZIP成员fixture产生预期warning。合法keyword参数、别名、with初始化、Expr初始化、默认参数与零参数main静态合同已同步实现；共享脚本契约3文件通过，tests/test_generated_script_contract.py 16 passed。

真实本地CLI模拟完成4次执行：loopback_workflow和仓库all_interfaces（不下载HDFS）退出0；无效bind退出2且零连接；命令明确失败退出1。SCP/SFTP、FTP主动/被动双向2048字节摘要一致及空文件、SSH0/7与Telnet0/3、prompt未知业务分支、CtrlB02、一次重启/重连、stdout/stderr/fd/子进程输出与源行号日志全部验证。

精确命令和冻结版报告位置见 [LOCAL_SIMULATION.md](../docs/LOCAL_SIMULATION.md)。compileall、node --check、3个相关skills quick_validate、SDD检查、list静态发现与git diff --check通过；测试矩阵含test_script_files_session.py、test_cli_protocol_flow.py与test_web_cli_bridge.py。此前真实协议基线见 [2026-10-09-local-protocol-testing.md](2026-10-09-local-protocol-testing.md)。本次未访问真实HDFS或设备，不改变原验证路径，真实服务/串口时序仍待授权验收。

脚本skill另经最小上下文独立Agent前向验证：从给定SSH/Telnet操作流程生成logs/skill-forward-check/generated.py，保留完整shell、参数/白名单、exit7恢复和READY判据、CtrlB/等待/重连顺序及同CLI guard；仅静态validator与compile通过，未连接设备，无额外草案审批或生成API不一致。
