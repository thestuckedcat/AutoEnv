# 独立离线日志网页使用文档

使用文件：`product-log-offline.html`。本项目生成位置为 `H:\Codex-autoenv\AutoEnv\dist\product-log-offline.html`，可以把这个文件复制到其他目录或电脑使用。

使用者只需要支持本地页面脚本的现代桌面浏览器，无需安装 Python、Node 或项目依赖，也无需联网、启动服务。Python CLI、`/product-logs/` 服务页面与离线页使用同一份显式初始配置，来源选择及解析器绑定规则保持一致。

## 1. 快速开始

1. 用现代 Edge、Chrome 或 Firefox 打开 `product-log-offline.html`。
2. 点击“选择压缩包”，选择 `product.tar.gz`；也可把压缩包拖入页面。已有解压目录时点击“选择文件夹”。
3. 常见日志可直接使用初始配置。需要调整目录、文件、来源或解析器时点击右上角“设置”；初始 dmesg 来源按顺序匹配 `varlog`、旧 `var`。
4. 点击“解析日志”。配置和模块编辑器只出现在“设置”中，主界面保留输入与日志浏览。
5. 完成后选择“固件日志”“驱动日志”或“统一时间视图”，再选择分块或模块、输入搜索文字。
6. 需要保留结果时点击“导出当前筛选”；需要复用规则时，在“设置”中下载完整配置 JSON。

日志结果只存在当前页面内存中，刷新或关闭页面后需要重新选择输入并解析。点击“保存并关闭”会尝试将规则保存到浏览器存储；浏览器禁用存储时，仍可下载配置 JSON 复用。原始日志不会被修改，浏览器存储中不保存日志正文。

## 2. 选择输入与整理规则

### 输入目录

选择包含目标目录的父文件夹，或包含这些目录的压缩包。例如：

```text
product/
├─ diag/
│  └─ slot62/
│     ├─ diaglog_62_20000106043418.log
│     ├─ history.tar.gz
│     └─ diag.log
└─ varlog/
   └─ log/
      ├─ cpdt_dmesg_current.log.1
      ├─ cpdt_dmesg_current.log.2
      ├─ cpdt_dmesg_current.log
      └─ older.tar.xz
```

在“设置 → 编辑来源、解析器与组件规则”中，来源的 `directory_patterns` 可设为 `["diag/slot62"]` 或 `["varlog", "var"]`。这些是所选输入中的目录规则，不是浏览器可直接访问的操作系统根目录。

配置中的目录规则按完整目录组件匹配，可用普通通配符；`slot62` 不会隐式匹配 `slot620`。部分路径按目标目录末尾的连续组件匹配，例如 `diag/slot62` 匹配 `product-A/diag/slot62`。来源的多个 `directory_patterns` 按顺序尝试，只在前项不存在时使用后一项；初始 dmesg 来源因此兼容旧 var。

目录定位先使用输入基准层：选择的文件夹或上传顶层包的内容。基准层没有命中时才搜索嵌套包。候选存在一个包含其他全部候选的祖先目录时，选择该祖先；不同分支仍报歧义，即使其中一条路径更短。例如外层 `diagnostic_information/varlog` 和其下普通、ZIP 内的 `varlog` 同时存在时，选外层目录，随后仍收集其子目录及包内日志。不同设备的同名目录需补充父路径消歧。

部分所选来源缺失时可继续解析其他来源；显式配置全部未命中、未选择任何来源或 `rules: []` 时得到零业务记录，不恢复初始规则或读取其他来源。不能填写 `H:\...` 等绝对路径；本页只能读取通过按钮选中的文件内容。文件夹请使用“选择文件夹”按钮，目录选择依赖浏览器支持。

### 日志选择与解压

| 项目 | 默认行为 | 可填写的示例 |
| --- | --- | --- |
| 来源的 `file_patterns` | 初始 diag 为 `["diaglog_*.log", "diag.log"]` | `["*.log", "*.txt"]` |
| 来源的 `recursive` | 初始两种来源均为 `true`；压缩包准备始终递归 | `false` 限制该来源的普通目录深度 |
| 复制文件模式 | 默认不额外选择文件 | `debug*.txt` |
| 重命名映射 | 默认不重命名 | `{"diag.log":"latest.log"}` |

来源文件模式只匹配完整文件名，不填写路径；JSON 中使用字符串数组，区分大小写。支持 `*`、`?`、`[abc]`、`[!abc]`；没有通配符时按精确文件名匹配。附加 `file_regex` 用于限制有效后缀，初始 dmesg 因而排除 `.old` 等非数字轮转名。可选复制模式仍以逗号分隔，保留原文件名关键词/glob 语义。

首先递归展开所有支持的压缩包，再按基准层优先、祖先覆盖规则定位目录和选取日志，包括只藏在多层包里的来源。包文件名不算目录组件，允许部分路径跨打包边界；结构化来源链区分包内目录与普通名字中的字面 `!/`。旧 `archive_patterns` 请求字段不限制准备阶段。实际日志选择、递归和轮转由各来源配置决定。

初始 diag 来源的 `order` 配置为历史文件字典序、`diag.log` 最后；初始 dmesg 按 `.1`、`.2`、`.10` 数字升序，无后缀 current 最后。此顺序用于分段关联，最终时间视图仍按来源绑定解析器提取的日期排序。

### 来源选项与解析器

打开“设置”即可为每条用户态、内核态或固件规则勾选来源。规则的 `source_ids` 是实际选择：单选 dmesg 只读 dmesg，单选自定义 kbox 只读 kbox，明确多选才合并。取消全部来源即停用该规则；不会自动读取其他命中来源。

来源通过 `parser_id` 绑定 `parsers` 中的配置。初始 `diag → diag_v1` 同时识别用户态与固件，`dmesg → dmesg_v1` 使用 dmesg 日期格式。可增加基于 `diag` 或 `kernel` 引擎的解析器配置，字段和日期正则用编号捕获组；不执行配置内的任意脚本。kbox 的真实字段和日期格式需要按样本配置，初始配置不假设与 dmesg 相同。

同一文件只解析一次，再按规则匹配完整记录；固件段头和续行先完成结构识别，避免单行关键词过滤丢失正文。同文件被多个来源选中且解析配置相同可共享，配置不同时明确报冲突。已声明但未被规则引用的来源不解析。

“解析配置”可导入/下载完整 JSON；展开编辑器可修改来源、解析器、分类/组件规则、别名、固件类型名称和模块表。三类业务分类固定为 `user_driver/kernel_driver/firmware`。初始规则的 `any` 明确保留旧代码的广泛识别，组件限制由 `match` 条件决定；删除规则不回退默认。空数组合法，`null`、未知引用和非法配置会阻止提交。“恢复初始配置”是明确操作。完整字段说明见 [设置与显式解析配置](../README.md#设置与显式解析配置)，原始配置见 [default_config.json](../default_config.json)。

复制与重命名只作用于内存副本，不修改磁盘原件；改名也不改变来源读取顺序。额外文件不在日志模式范围内时，先通过复制模式选择它；重命名规则未匹配文件会报错。新名字只能是单个文件名，不能包含目录。

支持 ZIP（存储、Deflate、BZIP2、LZMA/算法 14）、ZIP64、RAR4/RAR5（普通及固实压缩）、TAR、TAR.GZ/TGZ、TAR.BZ2、TAR.XZ，以及单文件 GZ/BZ2/XZ，按内容识别。全部解码器与 WASM 内嵌，不需要系统安装 WinRAR/7-Zip。7Z、加密或分卷包以及其他不支持的 ZIP 方法会报错；损坏包、链接/特殊成员和名称冲突也会明确失败。

## 3. 模块对应表怎么填

模块对应表位于“设置”中；解析后可编辑并应用到当前查询。每行把一个业务模块的用户态与内核态对应起来：

| 模块 | 用户态 ModuleName | 内核态关键词 |
| --- | --- | --- |
| udk | udk | udk |
| UBE | UBE | mami |
| MEME | MEME | MEME |
| HBOOT | HBOOT | HBOOT |

第一列是下拉框显示的名称，可以自定义，但不能空白或重复。第二列只写日志中的名称，例如 `UBE`，不要写 `ModuleName=UBE`。第三列填写内核整行中要寻找的普通文字，例如 `mami`，不要填写 `*mami*` 或正则表达式。

用户态按规范化后的 ModuleName 精确匹配，区分大小写；初始 `module_aliases` 将 `udk log`、`udk upgrade`、`UDK` 统一为 `udk`，`udk2` 保持独立；别名规则可在完整配置中调整。内核关键词在完整原行中做不区分大小写的子串匹配，因此 `mami` 可命中 `MAMI`、`mAmI`。

初始 `modules` 只包含前两行；其他模块需要显式添加，不再自动加入。后两行是填写示例，实际使用时替换为该模块真正的内核关键词。模块表负责查询现有记录，不能恢复已被采集规则排除的日志。

### 应用与保存

修改表格后点击“保存并应用”，匹配数量和下拉框立即更新，无需重新解压或解析。应用后的模块设置也同步到后续解析配置；可在设置中下载完整 JSON。日志结果仍只在当前页面内存中。

| 表格状态 | 含义 |
| --- | --- |
| 用户态显示“不存在” | 当前输入没有对应 ModuleName；仍可查询其内核匹配 |
| 内核态显示“未配置” | 内核关键词为空，暂不进行该模块的内核匹配 |
| 内核态显示“0 条” | 已填关键词，但当前输入没有命中 |

删除的模块行不会自动补回。同一内核行可以匹配多个模块，全部/统一视图只显示该行一次。

### 下载和复用 JSON

点击“下载 JSON”得到 `product-log-modules.json`。下载包含当前表格中的未应用修改；若希望查询结果与保存的表格一致，先点击“保存并应用”。

下次先导入并解析日志，再点击“导入 JSON”。导入成功后立即应用，不需要再点击保存。支持模块行数组，或包含 `modules` 数组的对象；可直接使用下面的内容：

```json
[
  {"module":"udk","user_module_name":"udk","kernel_keyword":"udk"},
  {"module":"UBE","user_module_name":"UBE","kernel_keyword":"mami"},
  {"module":"MEME","user_module_name":"MEME","kernel_keyword":"MEME"},
  {"module":"HBOOT","user_module_name":"HBOOT","kernel_keyword":"HBOOT"}
]
```

## 4. 查看三类日志

### 固件日志

点击“固件日志”，选择一个“解析出的分块”，或通过 SlotId、CpuId、DevId、LogType 联动缩小范围。例如 Slot=`62`、CPU=`0`、Dev=`4`、Type=`1`。字段组合定义固件分组，ModuleName 是打印组件的附加筛选。

| LogType | 显示名称 |
| --- | --- |
| 0 | bios |
| 1 | IMP |
| 2 | IMU |
| 3 | HSM |
| 其他数字 | 显示原数字，例如 4 |

每个 seg 从标准驱动头开始，正文继承该 seg 的读取时间；一个 seg 不跨文件，不同 seg 可跨文件，页面自动拼接。例如：

```text
[2000-01-06 03:51:36.383][87527.625][0-0][IMP-EVENT] some message
```

第一个时间是该段被读取的日期，`87527.625` 是固件内部计时，不会换算成日期。“固件顺序”默认按读取时间升序；选“完整 seg 拼接”保持每次读取的段连续，读取批次重叠时可能出现逐行日期倒退。若绑定的 diag 解析器配置为 `time_format: "none"`，段和读取批次保留未知时间，仍可按来源、流与 seg 拼接；统一时间视图将未知时间排在有效日期后。

### 用户态驱动日志

点击“驱动日志”，将“驱动来源”设为“用户态日志”。通过 SlotId、CpuId、用户态 ModuleName 或对应表模块筛选，也可直接选择解析出的分块。

“简洁模式”保留日期、Info 和正文：

```text
2000-01-06 03:51:36.383 Info=<28028:28041> 日志内容
```

“原本模式”显示完整驱动行，包括主机、SlotId、CpuId、ModuleName 等字段。

### 内核态驱动日志

点击“驱动日志”，将“驱动来源”设为“内核态日志”，再选择“对应表模块”，例如 `udk`。内核没有可靠的 SlotId/CpuId，因此设备和用户态 ModuleName 筛选禁用；设备显示未知，来源路径和行号仍保留。

`[Thu Jan 6 04:36:49 2000]` 标准化为 `2000-01-06 04:36:49`。每条无日期行独立显示 `[时间未知]`，不继承上一行日期。“原本模式”保留原行。

### 统一时间视图

把用户态、内核态、固件放在同一个日期列表中，已知日期从早到晚，时间未知的内核行放最后；相同时间按来源文件顺序与行号稳定排列。统一视图不选择单个分块；未分类内容、诊断告警在各自标签页查看。

统一视图中各筛选的作用范围如下：

| 筛选 | 作用范围 |
| --- | --- |
| 驱动来源、对应表模块 | 仅两态驱动；固件仍保留 |
| SlotId、CpuId、ModuleName | 用户态和固件；内核仍保留 |
| DevId、LogType | 仅固件；两态驱动仍保留 |
| 内容搜索、日志时间起止 | 所有三类日志 |

因此，想只看某个模块的驱动日志，应切换到“驱动日志”再选模块。“统一时间视图”的驱动原本模式不移除固件的读取时间前缀。

## 5. 搜索、时间与上下文

“内容搜索”是普通文字子串，支持中文，默认区分大小写；不是文件通配符或正则表达式。搜 `udk` 时若想同时命中 `UDK`，取消勾选“区分大小写”。模块表的内核关键词始终不区分大小写，不受这个复选框影响。

默认搜索正文；勾选“搜索完整原文”后，还可搜索完整驱动行或固件 seg 头部中的 `SlotId=62`、`Unit[4]` 等内容。这个选项不改变显示模式。

“日志时间起”“日志时间止”可留空，或填写设备日志时间：

```text
起：2000-01-06 03:51:36.383
止：2000-01-06 04:36:49
```

起止边界都包含，支持秒及 1–6 位小数，不做时区换算。仅填日期 `2000-01-06` 表示当日 `00:00:00`；若要查询全天，填写完整的起止时间，例如 `00:00:00` 至 `23:59:59.999999`。设置任意时间边界后，时间未知行不匹配。

有搜索文字时，可将“命中上下文”设为前后 2 或 5 行。上下文取同一来源文件的物理邻近行中仍满足分类、设备和模块筛选的记录，可能超出所填时间范围；按当前日志顺序展示。结果计数是实际命中数量，页面另标出本页包含上下文的条数。“上一页命中”“下一页命中”切换命中页。

## 6. 来源、导出与任务操作

点击日志行可查看来源路径、行号和附近原文。压缩包来源链用 `!/` 展示，例如 `older.tar.gz!/layer.zip!/cpdt_dmesg_current.log.1`；路径是相对来源，不代表浏览器创建了磁盘解压目录。

点击“导出当前筛选”，浏览器下载当前所有真正命中的日志，遵循当前显示模式与顺序。导出不限于屏幕或当前页，不包含仅用于展示的邻近上下文；“诊断 / 告警”页的导出按钮禁用。保存位置由浏览器下载设置决定。

| 操作 | 结果 |
| --- | --- |
| 清空筛选 | 清除内容/时间/设备/模块/来源条件，恢复区分大小写、无上下文；保留当前标签页、显示模式、固件顺序、模块表和导入规则 |
| 清除当前数据 | 终止 Worker，释放解析结果和模块表；原文件选择仍保留，可重新解析 |
| 停止任务 | 终止处理并清除本次内存结果；原件不变，可重新解析 |
| 刷新或关闭页面 | 丢失日志结果；下次重新选择输入并解析，可恢复浏览器已保存设置或导入配置 JSON |

本页不上传日志、不创建磁盘 product_log、不保存 SQLite 或服务器历史，也没有 3 GiB 磁盘自动清理。需要绝对目录、落盘批次和历史时，使用[原 CLI/服务页面](../README.md)。

## 7. 用项目样本试一次

已有演示包位于 `H:\Codex-autoenv\AutoEnv\logs\product-log-domain-demo\product.tar.gz`，未压缩原文在同目录 `source/`。

选择该包即可使用初始配置：diag 定位外层目录、dmesg 回退旧 var；需要限定范围时在设置中修改相应 `directory_patterns`。保留初始三条 `any` 规则时预期：

| 项目 | 数量 |
| --- | --- |
| 日志文件 | 11 |
| 用户态记录 | 2,003 |
| 内核态记录 | 408，其中 8 条时间未知 |
| 固件正文 | 3,600，共 30 个 Type/Dev 分组，每组 120 条 |
| 统一时间视图 | 6,011 |
| udk 模块 | 用户态 500、内核态 104 |
| UBE 模块 | 用户态 500、mami 内核态 100 |

可尝试驱动/内核态选择 `udk`，再搜索 `WITHOUT DATE`，查看未知时间行；填写时间范围后这些行应消失。在设置的模块表添加 `MEME/MEME/MEME` 并保存，可看到内核匹配 100 条。

需要重建样本时，在项目根目录执行：

```powershell
python -X utf8 tests/product_log_domain_sample.py logs/product-log-domain-demo/product.tar.gz --source-dir logs/product-log-domain-demo/source
```

## 8. 常见问题

| 问题 | 处理方式 |
| --- | --- |
| 找不到所选来源 | 在设置中检查来源目录、文件模式和来源选项；相对路径对应所选输入内容 |
| 目录多命中 | 祖先覆盖已自动处理；不同分支需根据候选补全来源父路径，例如 `product-A/diag/slot62` |
| 用户态或固件为 0 条 | 检查设置中的规则是否选择来源、匹配条件和文件模式，再查看诊断；空规则不会自动恢复初始配置 |
| 某模块内核没有结果 | 检查规则所选来源及其解析器，在模块表填写实际内核关键词并“保存并应用” |
| 有日志但列表为空 | 检查当前标签页及计数，点击“清空筛选”，再逐项设置条件 |
| 改表后筛选没变化 | 点击“保存并应用”；只下载 JSON 不会应用修改 |
| 刷新后数据不见了 | 日志结果需重新解析；规则可从浏览器存储恢复，或在设置中导入完整配置 JSON |
| 提示文件尚未打包 | 打开生成的 `dist/product-log-offline.html`，不要直接打开源码模板 `standalone/index.html` |
| 压缩包格式或校验报错 | 确认包完整且属于支持格式；加密/分卷包、7Z 或其他未支持的压缩方法仍不支持；算法 14 需使用本次重新生成的 HTML |

页面需要 Web Worker、Blob、ReadableStream、TextDecoder 和 WebAssembly。若浏览器或企业策略禁止本地页面脚本/Blob Worker，需要该环境允许此类页面才能使用；本工具不会自动启动 HTTP 服务。处理规模受浏览器可用内存限制。

## 9. 构建与分发（维护者）

在源码仓库根目录执行：

```powershell
python -X utf8 tools/build_product_log_page.py
```

默认生成 `dist/product-log-offline.html`；指定输出位置可执行：

```powershell
python -X utf8 tools/build_product_log_page.py --output "D:\工具\日志解析.html"
```

脚本只使用 Python 3.11+ 标准库，不导入 AutoEnv、不安装包、不下载资源、不启动服务。也可用 `python -I -S -X utf8 tools/build_product_log_page.py` 隔离构建；stdout 返回绝对路径、字节数与 SHA256，结果可重复。

分发只需复制生成的一个 HTML，使用文档可一并提供。样式、解析器、解压组件、Worker 及 XZ WebAssembly 均已内嵌，第三方许可在页面底部。`dist/` 是忽略的构建产物，版本控制保存源码和脚本；现有 wheel 安装不提供该打包工具。

## 10. 开发验证与实现归属

业务规则依据[服务版说明](../README.md)，浏览器 JavaScript 独立实现以真实合成样本、查询和完整导出对照 Python。后续修改规则时应同时维护这些测试：

```powershell
python -X utf8 -m pytest -q tests/test_product_log_configuration.py tests/test_product_log_source_python.py tests/test_product_log_source_offline.py tests/test_product_log_config_entrypoints.py tests/test_product_log_settings_ui.py tests/test_product_log_standalone_build.py tests/test_product_log_standalone_archives.py tests/test_product_log_standalone_parser.py tests/test_product_log_standalone_preparation.py tests/test_product_log_offline_rar_integration.py
```

开发测试需要 pytest、现有 Python 测试依赖以及 Node；它们不属于最终 HTML 的使用依赖。实际内嵌 Worker 在禁用网络 API 下完成真实多层包解压、解析、映射、查询、来源和导出，并与 Python 逐字节对照。最新全量验证见 [测试说明](../../tests/README.md)。内置浏览器安全策略拒绝 `file://`，因此尚未完成本地 HTML 双击打开与真实界面验收；Node 执行和静态检查不能替代该项验收。

| 文件 | 用途 |
| --- | --- |
| `index.html/styles.css/app.js` | 页面模板、样式与交互 |
| `archive.js/parser.js/worker.js` | 虚拟文件收集、解析/查询与消息桥 |
| `configuration.js/../default_config.json` | 两端共用的显式配置合同与初始规则；页面及 Worker 内嵌同一快照 |
| `libarchive-codec.js/unrar-codec.js` | 内嵌 WASM 适配；RAR5 成员元数据检查及 RAR4/5 字节解码 |
| `vendor/manifest.json` | 解压组件版本、来源、包完整性与逐文件 SHA256，构建前验证 |
| `THIRD_PARTY_NOTICES.txt` | 第三方许可与署名，全文嵌入生成页面 |
| `../../tools/build_product_log_page.py` | 标准库打包入口 |

组件来源：[fflate 0.8.3](https://github.com/101arrowz/fflate)、[seek-bzip 2.0.0](https://github.com/cscott/seek-bzip)、[xz-decompress 0.2.3](https://github.com/httptoolkit/xz-decompress)、[LZMA-JS 2.3.2](https://github.com/LZMA-JS/LZMA-JS)、[node-unrar-js 2.0.2](https://github.com/YuJianrong/node-unrar.js)、[libarchive-wasm 1.2.0](https://github.com/ofk/libarchive-wasm)。ZIP14 直接使用 LZMA-JS 保留原始字节；RAR4/5 使用 UnRAR WASM，RAR5 额外执行 libarchive 元数据检查。资源已随源码保存，构建和运行均不从这些地址加载文件。

展开与格式扩展的历史验证记录见 [2026-10-13 功能记录](../../newFeature/2026-10-13-archive-and-serial-compatibility.md)；本轮设置、来源绑定与目录消歧见 [来源配置功能记录](../../newFeature/2026-10-20-product-log-source-configuration.md)。
