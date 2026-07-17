# AutoEnv UT 目标与排查指南

本文说明离线 UT 为什么存在、使用了什么简单实现，以及失败后先检查哪里。测试函数名描述被保护的行为；参数化测试会把同一目标应用到多组输入，因此 pytest 显示的每个参数 case 都属于同一契约。

## 1. UT 如何做到不访问真实环境

- `tmp_path`：为每条测试创建隔离的临时项目、`logs`、`state` 和 `packages`。
- fake HDFS：用内存目录列表和本地字节写入代替 WebHDFS。
- fake SSH/SFTP/SCP：用内存 Channel、Transport 和远端文件字典代替真实服务器，同时保留连接、上传、MD5、超时和断连行为。
- fake Telnet Socket/Clock：预置收发字节并推进虚拟时间，测试提示符、IAC、退出码、超时和重连。
- `monkeypatch`：替换 UUID、CLI 调用或系统能力，保证结果确定且不产生外部副作用。
- AST 契约验证器：只解析 `scripts/*.py`，不导入或执行环境函数。

因此，UT 通过表示本地契约成立，不表示真实 HDFS、SSH、Telnet、远端路径或业务脚本已经验证成功。

## 2. 失败后先怎么做

先单独重跑 pytest 输出中的 node id：

```powershell
python -X utf8 -m pytest tests/test_runtime_registry.py::test_registered_funcs_reuse_context_and_loop_until_exit -vv
```

排查顺序：

1. 看第一条失败，不先处理由它连带产生的后续失败。
2. 对照断言左值/右值，判断是实现行为变化，还是测试预期已经过时。
3. 查看对应运行日志或临时 `result.json`；需要保留临时目录时使用 `pytest --basetemp=<目录>`。
4. 只修复相关实现或同步确认后的契约，不通过放宽断言掩盖真实回归。
5. 单条通过后重跑对应测试文件，再跑统一契约和全量 UT。

常用命令：

```powershell
# 精确重跑失败项
python -X utf8 -m pytest <pytest输出的node-id> -vv

# 验证所有生成环境脚本
python -X utf8 -m pytest tests/test_generated_script_contract.py -vv

# 全量离线回归
python -X utf8 -m pytest -q
```

## 3. 测试文件总览

| 文件 | 简单实现 | 主要目标 | 失败优先检查 |
|---|---|---|---|
| `test_runtime_registry.py` | 临时项目、注入输入、内存 console | 注册、RunContext、last-run、组合脚本、`register_func` 生命周期 | `autoenv/registry.py`、`autoenv/runtime.py`、`result.json` |
| `test_command_files.py` | 纯内存上传映射、临时输出文件 | `S{file_name}`、Host 隔离、完整 shell 文本生成 | `autoenv/command_files.py` |
| `test_generated_script_contract.py` | 临时 Python 片段和 AST 验证器 | skill 生成脚本的统一静态契约，包括两类命令接口的上传目标 | 失败消息对应行、验证器和生成脚本 |
| `test_ssh_host.py` | fake Paramiko/SFTP/SCP/远端文件系统 | SSH 状态、连接复用、按输出响应、上传校验、占位符目标 | `autoenv/ssh_host.py` 和 fake 收到的命令/响应/文件 |
| `test_telnet_client.py` | fake Socket、Clock、提示符字节流 | Telnet 模式探测、按输出响应、退出结果、断连、上传来源 | `autoenv/telnet_client.py` 和预置收发字节流 |
| `test_package_extractor.py` | fake HDFS、临时压缩包、注入 `.run` runner | 下载路径、原子文件、提取安全和摘要 | `package_manager.py`、`extractor.py`、临时包内容 |
| `test_results_selectors.py` | 临时文件树和 recorder | 结果模型、选择器安全、序列化、脱敏 | `results.py`、`selectors.py`、`recorder.py` |
| `test_cli.py` | monkeypatch 注册表和执行函数 | 菜单选择、命令模式和退出码 | `autoenv/cli.py` 的参数/输出映射 |

## 4. `register_func` 每条 UT 的目的

| UT | 简单实现 | 为什么存在 | 失败时怎么查 |
|---|---|---|---|
| `test_register_func_requires_an_active_registered_script` | 在运行上下文外应用装饰器 | 防止把 func 错放到模块顶层，导入时静默注册 | 检查 `_CURRENT_FUNCS` 判定和脚本缩进位置 |
| `test_registered_funcs_reuse_context_and_loop_until_exit` | 注入 `1,2,3,0`，运行成功、异常和失败结果三种 func | 保证菜单循环、同一 Context/Host、异常隔离及 `func_runs` 顺序 | 检查 `_run_func_menu()`、ContextVar reset、`result.json` 摘要 |
| `test_func_menu_reprompts_after_invalid_selections` | 输入非数字、越界值、退出 | 防止错误输入执行错误 func 或直接终止运行 | 检查数字解析、范围判断和 `continue` 分支 |
| `test_func_menu_is_not_opened_when_main_flow_fails` | 主流程返回失败，输入函数若被调用就让测试失败 | 保证只有环境真正拉起后才开放附加流程 | 检查 `if success and registered_funcs` 的调用条件 |
| `test_duplicate_func_name_fails_the_main_program` | 同一运行注册两个同名 func | 保证菜单编号不会对应两个含义不同的流程 | 检查当前运行列表的名称去重和名称规范化 |
| `test_registered_func_requires_one_context_argument` | 注册零参数 func | 保证执行时总能传入与主流程相同的 `RunContext` | 检查 `inspect.signature()` 校验和 func 定义签名 |

已注册 func 的执行失败但主结果仍为成功是有意设计：主环境已经启动，func 的结果单独记录。`register_func()` 自身若因重复名称、错误签名或错误作用域注册失败，主流程会是 `program_error`。若这些语义需要改变，应同时修改实现、本文、README、详细设计、注册指南、skill 和对应 UT。

## 5. 上传占位符与 shell 生成 UT

| UT | 保护目标 | 失败时怎么查 |
|---|---|---|
| `test_uploaded_file_registry_resolves_repeated_placeholders` | 同一占位符出现多次时全部替换 | 查看 selector 到实际 basename 的记录 |
| `test_selector_with_braces_can_be_used_as_an_exact_placeholder` | 正则选择器自身含 `{}` 仍能精确替换 | 检查已知 selector 优先匹配逻辑 |
| `test_placeholder_must_reference_a_successful_upload` | 未上传文件不能进入命令 | 检查上传成功后才调用 `record()` |
| `test_malformed_placeholder_is_rejected` | 空、缺右括号、嵌套括号不能发送 | 检查占位符剩余文本解析和错误消息 |
| `test_selector_cannot_resolve_to_two_actual_names` | 同一目标上一个 selector 不得指向两个包名 | 检查上传目标、selector 和实际文件名 |
| `test_uploaded_file_names_are_isolated_by_target` | 不同 SSH Host 的实际包名互不串用 | 检查 `target_name` 是否在上传和执行两侧一致传递 |
| `test_unbound_command_rejects_an_ambiguous_upload_target` | Telnet 未指定来源且存在多个候选时拒绝猜测 | 设置 `uploaded_files_from` 或排除重复来源 |
| `test_script_placeholders_must_share_an_upload_target` | 一个生成脚本的依赖必须在同一 Host 可用 | 检查所有依赖上传顺序和目标 Host |
| `test_generate_sh_file_resolves_a_complete_script_without_modifying_its_layout` | 只换包名，保留 CRLF、shebang、布局和末尾形式 | 比较输入/输出字节，检查 `newline=""` |
| `test_generate_sh_file_requires_one_complete_script_string` | 禁止恢复成命令列表 API | 检查调用方是否传入一个三引号字符串 |
| `test_generate_sh_file_rejects_invalid_filename` | 输出只能是 packages 根目录的 `.sh` 文件 | 检查文件名后缀、绝对路径、子目录和 `..` |

SSH/Telnet 文件中的占位符 UT 进一步证明：只有通过 MD5 校验的上传才可替换，SSH 绑定当前 Host，Telnet 使用明确的 `uploaded_files_from`。SCP UT 还检查目标参数使用远端目录，并确认目录、覆盖状态和 MD5 检查只使用普通 SSH 命令，整个 SCP 上传不会打开 SFTP 会话。

## 6. 统一环境脚本契约每条 UT 的目的

| UT | 为什么存在 | 失败时怎么查 |
|---|---|---|
| `test_all_repository_environment_scripts_satisfy_the_shared_contract` | 让所有 `scripts/*.py` 使用同一份规则 | 先运行验证器；按 `文件:行号:消息` 修脚本，不修改聚合断言 |
| `test_contract_rejects_a_command_list_instead_of_complete_shell_text` | 保证粘贴脚本保持为一整段文本 | 检查 `generate_sh_file()` 第二个参数 |
| `test_contract_rejects_inline_selectors_and_generate_before_upload` | 保证集中声明、变量复用和先上传后生成 | 把 selector 移到函数开头，并调整操作顺序 |
| `test_contract_rejects_execute_on_a_different_upload_target` | 防止 A Host 上传的文件在 B Host 命令中误替换 | 对齐上传对象与执行对象 |
| `test_contract_checks_execute_on_output_upload_target` | 保证按输出响应接口也执行相同的上传目标检查 | 将初始命令使用的文件上传到执行对象 |
| `test_contract_accepts_an_exact_regex_selector_with_braces` | 防止验证器误伤合法正则 selector | 检查验证器是否先匹配已声明 selector |
| `test_contract_rejects_module_level_and_non_final_registered_funcs` | 保证 func 位于主流程成功路径末尾且签名正确 | 移入 `@register_script` 函数末尾，保留一个 ctx 参数 |

如果反例 UT 突然“不再失败”，通常是验证器漏检；如果合法示例突然失败，通常是验证器误报。两者都应同时查看测试片段、验证器实现和 `scripts/example.py`。

## 7. 按输出关键词响应 UT

SSH 和 Telnet 测试都把关键词拆成两个接收分片，证明接口按累计输出匹配而不是只检查单包。成功 case 检查 Ctrl+B 的实际字节 `b"\x02"`，失败 case 检查关键词缺失、命令提前退出或响应发送失败时的状态、阶段和部分输出。

Telnet 成功发送后主动关闭 fake Socket，是为了验证旧 Linux Shell 提示符不会带到 Bootloader 会话；对象本身没有永久关闭，后续操作仍可懒重连。SSH 只关闭本次 Channel，Transport 保持可复用。
