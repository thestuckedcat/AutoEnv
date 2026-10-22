# 本地产品日志

本功能独立放在 `product_log/`。下面的 CLI 与服务端网页共用 Python 整理、解析和查询实现，仅复用 `autoenv` 的本地文件 SDK，无需登记 SSH/Telnet/HDFS 环境。另有 [单文件离线网页](standalone/README.md)，使用独立浏览器实现。

## 单文件离线网页

在源码根目录运行 `python -X utf8 tools/build_product_log_page.py`，输出 `dist/product-log-offline.html`；构建脚本仅依赖 Python 标准库，也可用 `python -I -S -X utf8 tools/build_product_log_page.py`。HTML 包含全部样式、脚本及解压器，使用者在现代桌面浏览器打开后选择压缩包/文件夹即可使用，无需安装 Python、Node 或启动服务、联网加载资源。

独立页保留三类日志、分段拼接、模块对应表、分类/统一时间、搜索与导出规则。解析在浏览器 Worker 中执行，来源为用户选择文件的相对路径；日志结果只存当前页面内存，设置可保存到浏览器存储并导入/下载完整配置 JSON。它没有服务端绝对路径导入、磁盘解压目录、历史批次或 3 GiB 清理；可选复制/重命名只操作内存副本。Python 与离线网页使用同一份初始配置，来源选择、解析器绑定和目录消歧语义保持一致。完整操作、兼容范围及验收边界见 [standalone/README.md](standalone/README.md)。

## 使用

在项目根目录执行：

```powershell
python -X utf8 -m product_log "D:\logs\product.tar.gz" --collection-mode product --diag-directory diag/slot62 --var-directory varlog --workers 4
python -X utf8 -m product_log "D:\logs\product.tar.gz" --collection-mode product --config "D:\logs\product-log-configuration.json"
python -X utf8 -m product_log "D:\logs\input" --collection-mode generic --output "D:\logs\batch-001" --file-pattern "*.txt"
python -X utf8 startWeb.py
```

唯一 Web 服务地址为 <http://127.0.0.1:8765/>，产品日志页面为 <http://127.0.0.1:8765/product-logs/>。可拖入/选择压缩包、上传浏览器选定文件夹，或填写 Web 服务所在机器可访问的绝对目录。浏览器文件夹上传显示的是服务端落盘路径；填写本地路径读取服务端目录，不从浏览器猜测文件系统路径。

CLI 默认创建 `runtime_path/product-log-<UUID>` 独立批次；`--output` 可指定新目录或预存上传输入的目录，但不能已含 `manifest.json/index.sqlite3/product_log/.shards`，不覆盖已有批次。网页使用 `product` 收集模式；未传自定义配置的 CLI 默认 `auto`，发现 diag/varlog（或兼容的旧 var）时使用初始产品规则，没有时进入通用流程。旧 `generic` 或显式 `--directory` 调用会生成一个本次有效的显式临时来源（目录、文件模式、diag 解析器和相关规则），并写入批次快照；它不新增隐藏解析路径。提供自定义配置后始终按该配置的来源和规则处理，不因空规则或缺少来源而恢复旧规则。

产品模式通过配置中的来源规则定位目录。初始来源 `diag` 精确匹配目录名 `diag`；`dmesg` 按 `directory_patterns: ["varlog", "var"]` 顺序查找，只有前项不存在才尝试后项。目录定位优先使用输入基准层（输入文件夹或顶层压缩包的内容），基准层无匹配时才搜索嵌套包。若候选中存在包含其余全部候选的祖先目录，选择该祖先；不同分支即使路径长短不同也继续报歧义，并列出候选。部分路径如 `diag/slot62` 匹配连续目录组件，包文件名不算目录组件，允许跨打包边界；来源边界使用结构化信息判断，普通目录中的字面 `!/` 不算压缩包。

例如 `diagnostic_information/varlog`、其子目录 `slot_62/varlog` 和其中的 `varlog.zip!/varlog` 同时存在时，选择外层 `diagnostic_information/varlog`，仍可收集其子目录及包内日志。一项已选来源缺失时可继续处理其他来源；显式配置未命中来源时允许零业务记录，不读取未选来源。未提供自定义配置的旧 product 调用全部未命中时仍报错。初始 diag 文件为 `diaglog_*.log`、`diag.log`，历史名字典序后追加最新 `diag.log`；初始 dmesg 文件为 `cpdt_dmesg_current.log` 及数字轮转后缀，`.1,.2,.10` 数字升序后追加无后缀文件。这些均来自显式配置。

所有支持的压缩包仍在目录定位前完整递归展开，包括未选中目录内的包；目录候选优先级只影响收集根选择，不禁止包内日志收集。旧 `--archive-pattern` 字段继续接收和校验，但不限制准备阶段。未提供 `--config` 时，旧 `--diag-directory`、`--var-directory`、`--file-pattern`、`--no-recursive` 参数转换为本次初始配置的覆盖值，保留原 CLI 用法；显式配置的来源目录、文件和递归选择以配置为准。`--copy-pattern`、重命名与并行数仍是独立整理参数。`--workers` 控制文件解析并发数。

成功退出码为 0，stdout 为 manifest JSON，阶段进度写 stderr。参数、文件或取消错误为退出码 2，并保留 failed/cancelled manifest；失败索引不发布为可查询结果。

可选整理：`--copy-pattern "extra*" --rename "extra.txt=driver.log"`。复制到任务工作区后才重命名，输入目录原件保持不变。任务保留输入目录现有日志与解出的日志，按来源映射消除同一文件副本的重复解析，不删除不同来源的重复内容。

## 设置与显式解析配置

主界面只保留导入、处理状态和日志浏览。点击“设置”后可选择每条规则的来源、编辑或导入/下载完整 JSON、恢复初始配置，以及进行可选整理和模块映射。修改来源、匹配或解析器后重新导入才会改变解析结果；设置不会重写已完成批次。

初始配置位于 [default_config.json](default_config.json)，由原代码中的目录、文件名、轮转、字段正则、日期、UDK 别名、固件名称与模块映射提取。Python 随包读取这份 JSON，离线构建将同一份内容内嵌至页面与 Worker。初始三条规则的 `component: "*"`、`match.operator: "any"` 明确表示保留原来的广泛识别；需要限制组件时应修改 `match` 条件。组件名称用于标识规则，实际过滤条件由 `match` 决定。

| 配置部分 | 内容与语义 |
| --- | --- |
| `version` | 当前为 `1` |
| `sources` | 来源 ID/名称、`directory_patterns`、`file_patterns`、可选附加 `file_regex`、`parser_id`、递归及轮转顺序 |
| `parsers` | 解析器 ID、`diag` 或 `kernel` 引擎、时间方式、编号捕获组字段正则及月份表 |
| `rules` | 固定三类 `user_driver`、`kernel_driver`、`firmware`；组件名称、`source_ids` 和完整记录匹配条件 |
| `module_aliases` | 显式模块名称归一规则；目标须为规范字面名称，再次按首条匹配规则归一后保持自身；拒绝链式/循环目标，清空后不进行 UDK 特例归一 |
| `firmware_types` | logType 到显示名称的映射；未命名值保留原数字 |
| `modules` | 用户态 ModuleName 与内核关键词的查询对应表；不再自动发现或补回模块行 |

来源的 `parser_id` 引用一个解析器配置；规则通过 `source_ids` 显式选择来源。例如内核规则选择 `["dmesg"]` 时不会读取另一个已声明来源；只有明确选择 `["dmesg", "kbox"]` 才合并两者。来源没有命中不会自动切换到未选择来源。新增 kbox 时需按实际目录、文件名和正文格式配置来源及解析器，初始配置不会假设 kbox 格式。

没有被规则引用的来源不参与解析；`rules: []` 或规则的 `source_ids: []` 不恢复隐藏默认值，允许得到零业务记录。完整空配置须保留所有顶层字段及空数组/对象；`null`、缺失必填字段、重复 ID、未知引用、非法类别或解析器冲突明确报错。恢复默认只通过显式“恢复初始配置”或未指定自定义配置的入口进行。

匹配字段支持原文、正文、ModuleName、SlotId、CpuId、DevId、logType；操作包括 `any/equals/contains/in/regex`。配置内的文件模式按完整 basename 匹配，目录规则按完整组件匹配，可使用 glob。正则使用两端共有的编号捕获组子集，不支持命名组、内联标志、反向引用或运行任意代码；共享编译器令 `\s/\S` 使用相同的 ECMAScript Unicode 空白集合（包含不换行空格、全角空格及 BOM），`\d/\w` 保持 ASCII 集合。`diag` 引擎支持 `iso/none` 时间方式；`kernel` 支持 `iso/dmesg/none`，ISO 日期正则的第一捕获组为完整日期，dmesg 依次为月、日、时分秒、小数秒、年。`none` 明确保留时间未知。

同一来源只解析一次，供多条组件规则匹配；diag 可同时产生用户态和固件候选。固件先完成段头、续行与分段识别，再按完整记录规则决定输出，避免先按单行关键词丢掉正文。同一实际文件被多个来源选中且解析配置相同则共享；解析配置不同时报冲突，不按顺序覆盖。

CLI 使用 `--config FILE`，SDK 使用 `run_analysis(..., analysis_config=config)`，HTTP `POST /api/product-logs/start` 接收 `analysis_config`；`GET /api/product-logs/configuration` 返回初始配置。每个批次在 `manifest.json` 保存实际配置快照，来源另保留所选来源 ID 与绑定解析器信息。浏览器设置存储只保存规则，不保存日志正文；可下载完整 JSON 跨浏览器或电脑复用。

## 输出与读取规则

每任务工作区包含 `product_log/input/`（顶层包）、`product_log/expanded/`（每个子包独立目录）、`product_log/selected/`（可选复制结果），批次根有 `manifest.json` 和 `index.sqlite3`。目录输入直接读原目录；网页显示实际输入根目录与工作区绝对路径。

支持内容识别 ZIP（含 LZMA/算法 14）、RAR、TAR、TAR.GZ/TGZ、TAR.BZ2、TAR.XZ 和单文件 GZ。先用队列准备整个压缩包森林，直到没有待处理包，再按基准层优先和祖先覆盖规则定位收集根；保留完整来源链。拒绝目录穿越、绝对成员路径、链接、特殊文件及名称冲突，非空输出目录不覆盖，损坏包明确失败。7Z、加密和分卷包不支持。

Python RAR 元数据使用随 SDK 保存的固定版本 rarfile，实际解压使用 Windows 自带 tar.exe/libarchive 或可用的 bsdtar，无需安装 Python 包；后端仅输出文件字节，由 SDK 验证并写入隔离目录。本机 bsdtar 对某些 RAR4 固实压缩或 Unicode 文件名不支持时会明确失败，且不发布部分结果。独立 HTML 内嵌另一条 RAR 解码路径，支持 RAR4/RAR5 普通及固实压缩，不依赖系统解压程序。

固件每段必须由标准驱动头开始，含日期、SlotId、CpuId、ModuleName、Unit、logType、seg。后续固件正文不用日期开头。一段不跨文件；多个段可跨文件。产品模式使用上述 diag 历史/current 顺序，通用模式按 `(文件名, 相对路径)` 字典序；文件内保持原行序。seg=0 开新读取，非零 seg 接续同一流最近的读取；EOF 仅结束当前段。seg 编号排序完成拼接，重复非零段和重复正文都保留，缺失/重复/倒序段以诊断呈现。不同主机、服务、模块和设备来源隔离，避免合并不相关日志。

固件正文展示为 `[2000-01-06 03:51:36.383][87527.625][0-0][IMP-EVENT] some message`，每行使用所属段的真实外层读取时间。默认**读取时间升序**，同时间按来源/行号稳定排序；可选拼接视图保持各读取批次完整连续，读取区间重叠时该视图可能逐行时间倒退。内部固件计时保持原文，不推算日期。

用户态驱动按 `(SlotId, CpuId, ModuleName)` 自动分组，已被规则接受的字段组合进入下拉框。初始 `module_aliases` 将 `udk` 及空白后缀名称（`udk log`、`udk upgrade`，不区分大小写）归一为 `udk`，原始字段仍保留；`udk2` 和其他模块不合并。固件按 `(SlotId, CpuId, DevId, LogType)` 分组，打印组件作为附加字段。初始类型映射为 `0=bios`、`1=IMP`、`2=IMU`、`3=HSM`，其他值显示数字。

初始内核规则将所选来源的每条非空行作为内核态驱动索引；自定义规则只输出匹配的记录。初始 dmesg 解析器将 `[Thu Jan 6 04:36:49 2000]` 解析为 `2000-01-06 04:36:49`，按配置月份表显式转换；每条无日期行一律标记时间未知，简洁模式显示 `[时间未知]`，不继承前一行或其他文件日期。SlotId/CpuId 显示未知，来源路径/行号保留。用户态简洁模式输出 `日期 Info=<...> 日志内容`，内核简洁模式输出日期及线程/正文；驱动原本模式保留完整原行。

“设置”中的模块表包含“模块 / 用户态 ModuleName / 内核态关键词”，初始 `modules` 为 `udk/udk/udk`、`UBE/UBE/mami`；其他模块需要显式添加，删除的行不会自动补回。内核关键词留空表示“未配置”。指定用户模块未出现则显示“不存在”，仍可按内核关键词查询。用户态名称按规范化字段精确匹配，内核关键词在原始整行上按不区分大小写的字面子串匹配。修改并保存表格立即查询现有索引，不重新解压/解析；它不会补回采集规则已排除的记录。保存结果位于每批次 `module-map.json`，重新打开批次恢复。

分类视图保留用户态/内核态/固件；统一时间视图合并三类并严格按日期升序，同时间按来源/行号稳定排序，无日期放最后，时间范围条件排除未知日期。Slot/Cpu 只筛用户态和固件；Dev/类型只筛固件；内核只按模块及内容筛选。对应表模块作用于两态驱动，不推导固件模块归属。同一内核行可匹配多个表格模块，在全部及统一视图只显示一次。统一视图的原本模式仅影响驱动，固件始终保留读取时间前缀。

时间、维度、关键词条件先过滤再分页；关键词默认查正文，勾选完整原文搜索可匹配外层字段。不同来源的重复日志不去重。

每个文件流式解码/解析并写临时 SQLite 分片，并发处理不同文件；恢复来源顺序后合并索引。查询有分页上限，网页仅渲染可见行；导出流式读取全部匹配。关键词是字面子串，不是正则；任意子串可能扫描当前分组。单个大文件无法按文件并发加速。来源文件/行号、未识别内容及解析诊断保留，不把不完整结果发布为 ready。

关键词查询在独立 `query-cache.sqlite3` 缓存有序命中 ID：最多 12 个筛选组合，每个最多 500,000 个 ID，空闲 1 小时失效；超额、缓存锁竞争或只读目录回退 SQL 分页，不改变筛选语义。默认每页最多 500 个命中；启用上下文时附加有界邻近行，网页按每页 200 个命中提供前后页。原目录文件之后发生大小/修改时间变化，来源查看会要求重导入，已解析数据库快照仍可查询。

标准日期可有 1..6 位小数，排序和时间过滤用整数微秒，不把同一毫秒内的记录误认为同时间。配置合同的 `version` 仍为 `1`；当前磁盘索引 schema 为 **3**，manifest 声明索引版本和时间单位。`diag` 的 `time_format: "none"` 允许固件段和读取批次的时间值为 NULL，时间未知时仍按来源、流及 seg 结构拼接，不伪造绝对日期；统一时间视图将未知时间放在有效日期后。索引恢复/查询要求版本完全一致，旧 schema 2 等批次需重新导入，不能原地当作新索引继续读取。

查询接口 `query_records/iter_export` 支持 `kind="all"`、`driver_domain="user"/"kernel"` 和对应表 `module`；`list_groups` 可按 driver_domain 分类。`GET/POST /api/product-logs/modules` 读取/保存 `{batch_id, modules:[{module,user_module_name,kernel_keyword}]}`，返回存在状态与两态数量。原查询、来源和导出路由保持。

## 任务数据与自动清理

Web 产品日志数据存放在 `webPage/.runtime/product-logs/`。仅其中 `uploads/<upload_id>` 和 `batches/<batch_id>` 的受管目录纳入统计与清理，包括上传、展开文件、SQLite 索引、查询缓存和诊断。默认触发阈值为 **3 GiB（3 × 1024³ 字节）**；达到或超过阈值后，按最旧顺序删除可回收批次及遗留上传，直到占用低于阈值。删除的历史批次同步从页面列表移除，需要长期保存的结果可导出到管理目录外。

清理在管理器加载历史、新上传/任务准备、上传完成、任务结束和页面检查占用时执行。保留正在运行的任务、正在查看的批次、最新成功批次、最近结束的任务，以及活动上传和受保护批次引用的输入。重启后没有活动会话的遗留上传可回收；上传目录作为日志来源时随引用它的批次保留，避免历史来源查看失效。若通过本机路径再次导入旧受管批次内的文件，该输入所属批次也会保留，直到引用它的任务被回收；关联记录支持重启恢复。

3 GiB 是自动清理触发阈值，**不是硬配额**。受保护数据本身超过阈值时继续保留，页面显示剩余占用及原因；不会为达到数值删除当前结果，也不会因此禁止新任务。删除失败或目录内存在链接/Windows junction 时跳过并显示错误。统计为普通文件的逻辑字节数，不等于文件系统实际分配空间。

页面“任务数据”显示占用、阈值和最近回收结果，“检查占用”会重新统计并执行必要清理。`GET /api/product-logs/storage?batch_id=<id>` 同时保护该浏览批次；显式空 `batch_id=` 解除浏览保护，无该参数则保持原保护。`GET /api/product-logs/batches` 附带同样的 storage 状态。

用户提供的管理目录外原始日志、CLI 的 `runtime_path/product-log-*` 与指定 `--output` 均不自动删除。真实日志可以直接在本机使用，不要求先脱敏；运行数据与真实样本不作为代码提交内容。

## 文件 SDK

```python
from autoenv import accurate_search, find_subdir, fuzz_search, get_file, get_handler

archive = get_file("D:/logs", "product.tar.gz")
root = archive.unzip("D:/logs/batch/product_log/input")
target = find_subdir(root, "diag/slot62")
for number, package in enumerate(fuzz_search(target, "target*", recursive=True)):
    package.unzip(f"D:/logs/batch/product_log/expanded/{number:04d}")
file = accurate_search(target, "extra.txt", recursive=True)
if file:
    copied = file.move_to("D:/logs/batch/product_log/selected")  # 复制，源文件保留
    renamed = copied.rename("driver.log")
handlers = get_handler("D:/logs/batch/product_log", recursive=True)
for handler in handlers:
    print(handler.filename, handler.path)
```

`fuzz_search` 在没有通配符时匹配文件名子串，`accurate_search` 此时匹配精确文件名；含 `* ? [abc] [!abc]` 时匹配整个 basename。空模式匹配全部，区分大小写；结果统一按文件名字典序，再按相对路径排序。handler 组支持重复遍历、长度、索引，首次取用成员建立并缓存完整性快照。`get_file()` 的 SHA-256 合同保持不变。`move_to/rename` 返回新的不可变 handler，同名文件默认报错。

`detect_archive(path)` 返回 `zip/tar/gz/rar/None`；TAR 的压缩变体均归为 tar。`find_subdir(..., exclude_paths=[output_dir])` 可排除生成目录；`unzip/verify` 的 `check_cancelled` 回调可中断流式摘要和展开。文件 handler 构造时仍会计算完整 SHA-256。

## 测试

```powershell
python -X utf8 -m pytest -q tests/test_product_log_configuration.py tests/test_product_log_source_python.py tests/test_product_log_source_offline.py tests/test_product_log_config_entrypoints.py tests/test_product_log_settings_ui.py tests/test_local_file_operations.py tests/test_product_logs.py tests/test_product_log_collection.py tests/test_product_log_domains.py tests/test_product_log_web.py tests/test_product_log_integration.py tests/test_product_log_retention.py
```

测试生成真实压缩包，涵盖多文件、驱动与固件混排、跨文件分段、重复段、编码、时间顺序、目录消歧、异常包和真实 loopback HTTP/CLI。实际设备日志格式变体与大规模吞吐需要真实样本补充验证。

手动测试样本生成命令（项目根目录）：

```powershell
python -X utf8 tests/product_log_sample.py logs/product-log-demo/product.tar.gz --source-dir logs/product-log-demo/source
```

原通用样本使用固定随机种子，固件包含 Type 0–4 与 Dev 0–5 的全部 30 种组合，每组 120 条正文、14 个不等长 seg，分布于 7 个日志文件；驱动 `ModuleName` 为 `udk log`、`UBE`、`MEME`、`HBOOT`，各 500 条。不同固件组合的 seg 与驱动行随机穿插，seg 正文完整保留，外层时间也打乱。原文保留名称内部空格；用户态分类将 `udk log` 归一为 `udk`。

生成的顶层包内含 ZIP/TAR/TAR.GZ/TAR.BZ2/TAR.XZ/GZ 子包，可用 CLI `--collection-mode generic --directory diag/slot62 --file-pattern "*.log"` 读取。预计得到 7 个文件、3,600 条固件、2,000 条用户态驱动、30 个固件分组与 4 个驱动分组。未压缩原始日志位于 `logs/product-log-demo/source/diag/slot62`。

本轮 diag/var 网页演示样本：

```powershell
python -X utf8 tests/product_log_domain_sample.py logs/product-log-domain-demo/product.tar.gz --source-dir logs/product-log-domain-demo/source
python -X utf8 -m product_log logs/product-log-domain-demo/product.tar.gz --collection-mode product --diag-directory diag/slot62 --var-directory var
```

网页使用初始配置，或在“设置”中把 diag 来源的 `directory_patterns` 设为 `["diag/slot62"]`、dmesg 来源设为 `["var"]`。保留初始三条 `any` 规则时预计 11 文件、2,003 用户态、408 内核态（8 条未知时间）、3,600 固件、统一视图 6,011 条；UDK 用户态 500 条、内核 104 条，UBE 内核 mami 100 条。样本包含 UDK 两种后缀、udk2 排除、多 Slot/Cpu、六种压缩格式、多层 var 嵌套、`.1/.2/.10/current`，未压缩文件及预期概要在 `logs/product-log-domain-demo/source/`。旧批次需要重新导入以应用解析设置。
