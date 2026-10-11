# 本地产品日志

本功能独立放在 `product_log/`。下面的 CLI 与服务端网页共用 Python 整理、解析和查询实现，仅复用 `autoenv` 的本地文件 SDK，无需登记 SSH/Telnet/HDFS 环境。另有 [单文件离线网页](standalone/README.md)，使用独立浏览器实现。

## 单文件离线网页

在源码根目录运行 `python -X utf8 tools/build_product_log_page.py`，输出 `dist/product-log-offline.html`；构建脚本仅依赖 Python 标准库，也可用 `python -I -S -X utf8 tools/build_product_log_page.py`。HTML 包含全部样式、脚本及解压器，使用者在现代桌面浏览器打开后选择压缩包/文件夹即可使用，无需安装 Python、Node 或启动服务、联网加载资源。

独立页保留 diag/var 收集、三类日志、分段拼接、模块对应表、分类/统一时间、搜索与导出规则。解析在浏览器 Worker 中执行，来源为用户选择文件的相对路径；日志与配置只存当前页面内存，模块表可导入/下载 JSON。它没有服务端绝对路径导入、磁盘解压目录、历史批次或 3 GiB 清理；可选复制/重命名只操作内存副本。浏览器 JavaScript 与现有 Python 通过真实样本对照验证，现有 SDK、CLI、HTTP 和 wheel 入口均不调整。完整操作、兼容范围及验收边界见 [standalone/README.md](standalone/README.md)。

## 使用

在项目根目录执行：

```powershell
python -X utf8 -m product_log "D:\logs\product.tar.gz" --collection-mode product --diag-directory diag/slot62 --var-directory var --workers 4
python -X utf8 -m product_log "D:\logs\input" --collection-mode generic --output "D:\logs\batch-001" --archive-pattern "target*" --file-pattern "*.txt"
python -X utf8 startWeb.py
```

唯一 Web 服务地址为 <http://127.0.0.1:8765/>，产品日志页面为 <http://127.0.0.1:8765/product-logs/>。可拖入/选择压缩包、上传浏览器选定文件夹，或填写 Web 服务所在机器可访问的绝对目录。浏览器文件夹上传显示的是服务端落盘路径；填写本地路径读取服务端目录，不从浏览器猜测文件系统路径。

CLI 默认创建 `runtime_path/product-log-<UUID>` 独立批次；`--output` 可指定新目录或预存上传输入的目录，但不能已含 `manifest.json/index.sqlite3/product_log/.shards`，不覆盖已有批次。网页使用 `product` 收集模式；CLI 默认 `auto`，发现 diag/var 时使用产品规则，没有时使用通用规则。显式 `--directory` 在 auto 中保留通用目录选择；`generic` 可显式指定原整理流程。

产品模式通过 `--diag-directory`、`--var-directory` 分别定位唯一目录，默认精确目录名 `diag`、`var`；`var` 属于输入内容。允许部分路径如 `diag/slot62` 在任意深度匹配连续组件；多命中报错并给出候选。一类目录缺失时解析另一类，两类都缺失时 product 报错。diag 默认只收集 `diaglog_*.log`、`diag.log`；历史文件名字典序升序，最新 `diag.log` 最后。var 默认收集 `cpdt_dmesg_current.log.<数字>`，数字升序 `.1,.2,.10`，最新无后缀文件最后。

`--archive-pattern`、`--file-pattern` 和 `--copy-pattern` 可重复，使用文件名关键词或 glob。产品模式中的 archive/file 模式只控制 diag，文件模式留空使用标准名；var 始终递归展开全部受支持压缩包并按 dmesg 文件名收集。`--no-recursive` 限制通用/diag 搜索，var 仍递归。`--workers` 控制文件解析并发数。

成功退出码为 0，stdout 为 manifest JSON，阶段进度写 stderr。参数、文件或取消错误为退出码 2，并保留 failed/cancelled manifest；失败索引不发布为可查询结果。

可选整理：`--copy-pattern "extra*" --rename "extra.txt=driver.log"`。复制到任务工作区后才重命名，输入目录原件保持不变。任务保留输入目录现有日志与解出的日志，按来源映射消除同一文件副本的重复解析，不删除不同来源的重复内容。

## 输出与读取规则

每任务工作区包含 `product_log/input/`（顶层包）、`product_log/expanded/`（每个子包独立目录）、`product_log/selected/`（可选复制结果），批次根有 `manifest.json` 和 `index.sqlite3`。目录输入直接读原目录；网页显示实际输入根目录与工作区绝对路径。

支持内容识别 ZIP、TAR、TAR.GZ/TGZ、TAR.BZ2、TAR.XZ 和单文件 GZ。顶层、diag 和通用模式保持原一轮匹配子包展开；产品 var 使用队列递归展开，包括压缩包内的压缩包，直到无待处理包，保留完整来源链。拒绝目录穿越、绝对成员路径、链接、特殊文件及名称冲突，非空输出目录不覆盖，损坏包明确失败。RAR/7Z 与加密包不支持。

固件每段必须由标准驱动头开始，含日期、SlotId、CpuId、ModuleName、Unit、logType、seg。后续固件正文不用日期开头。一段不跨文件；多个段可跨文件。产品模式使用上述 diag 历史/current 顺序，通用模式按 `(文件名, 相对路径)` 字典序；文件内保持原行序。seg=0 开新读取，非零 seg 接续同一流最近的读取；EOF 仅结束当前段。seg 编号排序完成拼接，重复非零段和重复正文都保留，缺失/重复/倒序段以诊断呈现。不同主机、服务、模块和设备来源隔离，避免合并不相关日志。

固件正文展示为 `[2000-01-06 03:51:36.383][87527.625][0-0][IMP-EVENT] some message`，每行使用所属段的真实外层读取时间。默认**读取时间升序**，同时间按来源/行号稳定排序；可选拼接视图保持各读取批次完整连续，读取区间重叠时该视图可能逐行时间倒退。内部固件计时保持原文，不推算日期。

用户态驱动按 `(SlotId, CpuId, ModuleName)` 自动分组，所有实际字段组合进入下拉框。仅 `udk` 及空白后缀名称（`udk log`、`udk upgrade`，不区分大小写）归一为 `udk`，原始字段仍保留；`udk2` 和其他模块不合并。固件按 `(SlotId, CpuId, DevId, LogType)` 分组，打印组件作为附加字段。类型名为 `0=bios`、`1=IMP`、`2=IMU`、`3=HSM`，其他值显示数字。

var 的每条非空行作为内核态驱动索引。`[Thu Jan 6 04:36:49 2000]` 解析为 `2000-01-06 04:36:49`，英文月份显式转换；每条无日期行一律标记时间未知，简洁模式显示 `[时间未知]`，不继承前一行或其他文件日期。SlotId/CpuId 显示未知，来源路径/行号保留。用户态简洁模式输出 `日期 Info=<...> 日志内容`，内核简洁模式输出日期及线程/正文；驱动原本模式保留完整原行。

网页模块表包含“模块 / 用户态 ModuleName / 内核态关键词”，默认 `udk/udk/udk`、`UBE/UBE/mami`；其他解析出的用户模块自动补入，内核关键词留空表示“未配置”。指定用户模块未出现则显示“不存在”，仍可按内核关键词查询。用户态名称按规范化字段精确匹配，内核关键词在原始整行上按不区分大小写的字面子串匹配。修改并保存表格立即查询现有索引，不重新解压/解析。保存结果位于每批次 `module-map.json`，重新打开批次恢复；删除自动发现行后若其 ModuleName 仍未映射，会再次补入。

分类视图保留用户态/内核态/固件；统一时间视图合并三类并严格按日期升序，同时间按来源/行号稳定排序，无日期放最后，时间范围条件排除未知日期。Slot/Cpu 只筛用户态和固件；Dev/类型只筛固件；内核只按模块及内容筛选。对应表模块作用于两态驱动，不推导固件模块归属。同一内核行可匹配多个表格模块，在全部及统一视图只显示一次。统一视图的原本模式仅影响驱动，固件始终保留读取时间前缀。

时间、维度、关键词条件先过滤再分页；关键词默认查正文，勾选完整原文搜索可匹配外层字段。不同来源的重复日志不去重。

每个文件流式解码/解析并写临时 SQLite 分片，并发处理不同文件；恢复来源顺序后合并索引。查询有分页上限，网页仅渲染可见行；导出流式读取全部匹配。关键词是字面子串，不是正则；任意子串可能扫描当前分组。单个大文件无法按文件并发加速。来源文件/行号、未识别内容及解析诊断保留，不把不完整结果发布为 ready。

关键词查询在独立 `query-cache.sqlite3` 缓存有序命中 ID：最多 12 个筛选组合，每个最多 500,000 个 ID，空闲 1 小时失效；超额、缓存锁竞争或只读目录回退 SQL 分页，不改变筛选语义。默认每页最多 500 个命中；启用上下文时附加有界邻近行，网页按每页 200 个命中提供前后页。原目录文件之后发生大小/修改时间变化，来源查看会要求重导入，已解析数据库快照仍可查询。

标准日期可有 1..6 位小数，排序和时间过滤用整数微秒，不把同一毫秒内的记录误认为同时间。manifest 声明索引版本和时间单位；当前索引版本为 2，旧版本批次需要重新导入。

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

`detect_archive(path)` 返回 `zip/tar/gz/None`；TAR 的压缩变体均归为 tar。`find_subdir(..., exclude_paths=[output_dir])` 可排除生成目录；`unzip/verify` 的 `check_cancelled` 回调可中断流式摘要和展开。文件 handler 构造时仍会计算完整 SHA-256。

## 测试

```powershell
python -X utf8 -m pytest -q tests/test_local_file_operations.py tests/test_product_logs.py tests/test_product_log_collection.py tests/test_product_log_domains.py tests/test_product_log_web.py tests/test_product_log_integration.py tests/test_product_log_retention.py
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

网页填写 diag=`diag/slot62`、var=`var`，日志模式留空。预计 11 文件、2,003 用户态、408 内核态（8 条未知时间）、3,600 固件、统一视图 6,011 条；UDK 用户态 500 条、内核 104 条，UBE 内核 mami 100 条。样本包含 UDK 两种后缀、udk2 排除、多 Slot/Cpu、六种压缩格式、多层 var 嵌套、`.1/.2/.10/current`，未压缩文件及预期概要在 `logs/product-log-domain-demo/source/`。旧批次需要重新导入。
