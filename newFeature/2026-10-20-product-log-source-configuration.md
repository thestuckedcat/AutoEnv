# 产品日志显式来源配置与解析器绑定

## 意图与用户行为

将原产品日志代码中的来源目录、文件名、轮转、字段识别、日期、模块别名、固件类型名称和模块对应表提取为 `product_log/default_config.json`。业务输出仍固定为用户态驱动、内核态驱动和固件三类。通过显式配置选择来源和识别组件，未配置或未选择的内容不进入业务解析结果。

主界面保留输入、处理结果和日志浏览；来源选项、完整 JSON、可选整理与模块表集中在默认关闭的“设置”对话框。每条规则可单选或多选来源，取消全部来源即停用该规则。设置可保存至浏览器存储、导入/下载完整 JSON；日志正文仍不存入浏览器设置存储。修改解析设置作用于下一次导入，已完成批次保留配置快照。

本轮接续 [本地产品日志初版](2026-10-11-local-product-log-parser.md) 和 [归档与串口兼容](2026-10-13-archive-and-serial-compatibility.md)，不改变环境脚本注册及远端执行流程。

## 架构与数据流

```text
显式来源选项 rules.source_ids
  → sources（目录/文件/顺序，parser_id）
  → parsers（diag/kernel 引擎，字段和时间参数）
  → 完整记录与固件段识别
  → rules 匹配
  → 用户态 / 内核态 / 固件索引与浏览
```

Python 的 `configuration.py` 与离线 `configuration.js` 校验同一合同。初始配置由 Python 包含在安装资源中，构建器将同一 JSON 内嵌到独立网页的应用与 Worker；配置导入不访问网络。解析器代码保留字段提取、状态转换、固件拼接和来源追踪能力，配置决定启用哪些规则，不执行任意代码。

来源携带 `configured_source_ids`、`parser_profile` 和配置快照。相同实际文件被相同解析配置选中时共享一次解析；配置不同时明确报冲突。固件按完整段而不是先逐行关键词过滤，保留段内续行与正文。同一记录命中多条规则时不重复插入；不同来源的相同文本保留各自出处。

## 配置合同与决定

- `version: 1`；顶层字段为 `sources/parsers/rules/module_aliases/firmware_types/modules`，必填但允许显式空数组/对象。
- 来源使用 `id/name/directory_patterns/file_patterns/file_regex/parser_id/recursive/order`。目录列表的顺序表示前一项不存在时才尝试后一项。初始来源是 `diag` 和 `dmesg`，初始 dmesg 目录顺序为 `varlog,var`。
- 解析器使用 `id/engine/time_format/patterns/months`。`diag` 支持 `iso/none`，`kernel` 支持 `iso/dmesg/none`；时间未知不继承上行或其他文件日期。
- 规则使用 `id/category/component/source_ids/match`。分类只接受 `user_driver/kernel_driver/firmware`；来源与引擎输出类别必须兼容。匹配支持 `any/equals/contains/in/regex` 及原文、正文或元数据字段。
- 初始三条 `any` 规则显式表达旧代码的广泛识别，`component: "*"` 是规则标识；过滤条件由 `match` 决定。删除规则不回退旧代码。
- `rules: []` 或 `source_ids: []` 允许零业务记录。未引用来源不解析；缺失字段、重复 ID、未知引用、错误正则或不兼容类别明确报错，`null` 不作为自定义空配置。
- 模块别名、固件名称和查询模块表来自配置。模块不再自动发现或补回；查询表只过滤已建立的索引，不补回采集时排除的记录。
- 别名 replacement 是规范字面目标，必须在首条匹配规则下保持自身；配置校验拒绝 A→B→C 链、循环及带待裁剪空白的目标，避免导入、保存与查询重复归一时漂移。
- 初始配置未凭名称添加 kbox；其文件名、字段及时间格式需依据真实样本配置。用户可以创建该来源并绑定支持的解析器配置。
- 配置合同 `version` 保持 1，磁盘索引 schema 升为 3。segments/dumps 的时间值允许 NULL，diag `time_format: "none"` 的固件仍可按来源、流及 seg 结构拼接；统一视图把未知时间排在有效日期后。恢复/查询继续要求完全相同的索引版本，旧 schema 2 等批次必须重新导入，不保留透明旧格式回退。
- 未提供自定义配置的 legacy generic 调用生成明确的本次临时来源（目录、文件模式、diag 解析器及相关规则），并写入配置快照；显式配置无命中或空规则不会进入隐藏通用解析。该兼容转换不添加任何 kbox 默认规则。
- 正则采用编号捕获组的共同子集，不支持命名组、内联标志、反向引用或任意脚本。
- 共享 `compile_regex/compileRegex` 将 `\s/\S` 统一为 ECMAScript Unicode 空白集合（包含 NBSP、全角空格、BOM，排除 U+0085 和 U+001C–U+001F），保留 ASCII 数字/词字符集合；字段空值按空字符串匹配，避免 Python None 与 JavaScript null 的字符串差异。

## 目录匹配修复

压缩包森林仍完整递归准备，目录定位与日志收集分开：

1. 优先匹配输入基准层，即输入文件夹或用户上传顶层包的内容。
2. 一个候选包含全部其他候选时选择该祖先，避免外层 `varlog` 与其普通/包内后代竞争。
3. 不同分支保持歧义，即使路径长度或深度不同，也不任取较短路径。
4. 基准层无候选时才搜索嵌套包，保留只在包内存在目标的能力。
5. 选定收集根后继续按来源配置收集其子目录及包内日志。

判断依据为结构化来源，不以字符串包含 `!/` 判定归档；普通名字中的字面 `!/` 保持独立。通用 SDK `find_subdir()` 的严格唯一合同没有扩展。

## 公开接口与主要文件

- Python：`run_analysis(..., analysis_config=config)`；`configuration.default_config/normalize_config/active_sources/record_enabled/normalize_module/firmware_type_name`。
- CLI：`python -X utf8 -m product_log INPUT --config FILE`；未传自定义配置时原目录/文件参数可转为本次初始配置覆盖。
- HTTP：`GET /api/product-logs/configuration` 返回初始配置；`POST /api/product-logs/start` 接收 `analysis_config`。原查询、来源、导出和模块接口继续存在。
- 批次 `manifest.json.analysis_config` 保存实际规则；离线 Worker 使用导入时的同一快照。
- 核心：`product_log/default_config.json`、`configuration.py`、`workflow.py`、`parser.py`、`index.py`、`module_map.py`、`web.py`、`__main__.py`。
- 离线：`standalone/configuration.js`、`archive.js`、`parser.js`、`worker.js`；两端 HTML/app/CSS 的设置界面；`tools/build_product_log_page.py` 和 Python 包数据清单。
- 文档：产品日志说明、离线使用文档、测试说明、Web 接手说明及主 README 的入口段落。注册指南、`scripts/example.py` 和环境脚本生成 skill 未涉及该本地解析流程，无需改变接口。

## 验证结果与边界

验证于 2026-10-21 完成，测试均先观察失败再实现修复。

- 配置合同专项 **58 passed**：Python/Node 初始配置一致、空规则及来源选项不回退、严格引用、捕获组、可移植正则、Unicode 空白与字面匹配、别名规范目标和空值语义。
- 最终全量离线回归 **978 passed、2 skipped**，238.14 秒；两项跳过均为 Windows 符号链接权限。警告来自环境既有 Paramiko 弃用提示及重复 ZIP 成员测试样本。
- 两版设置交互测试 **6 passed**，包含真实 Edge；实际构建 HTML 与标准 Web 服务上传同一 ZIP，用户态 1、内核态 1、固件 2，均无页面异常。390px 主界面与设置无横向溢出。
- `python -I -S -X utf8 tools/build_product_log_page.py` 成功；wheel 构建成功并确认 JSON、Python 配置模块及浏览器共享配置脚本随包保存。
- Python 编译、6 个业务 JS 文件语法检查、环境脚本静态合同 5 文件、注册发现、项目 skill 校验和 `git diff --check` 通过。
- 独立审查发现的非幂等别名、显式嵌套来源解析器冲突和两端正则数字选择差异均已修复并补充回归。

当前 Anaconda 测试环境缺少项目已经声明的 `scp` 包，使用 `pip install --no-deps --target logs/test-dependencies 'scp>=0.14'` 补齐到工作区，并以该目录作为测试进程的 `PYTHONPATH`；未修改全局 Python 环境。最终回归命令：

```powershell
$env:PYTHONPATH = 'H:\Codex-autoenv\AutoEnv\logs\test-dependencies'
python -B -X utf8 -m pytest -q --basetemp logs/pytest-source-config-final --tb=short --disable-warnings
```

真实 kbox 格式与设备日志变体尚无本轮样本验收；未加入推测的 kbox 默认来源或格式。本功能只处理本地输入，没有执行真实 HDFS、SSH 或 Telnet 目标。索引 schema 3 支持固件时间未知；旧 schema 2 批次须重新导入。
