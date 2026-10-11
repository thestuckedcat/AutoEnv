# 本地文件操作与独立产品日志解析

日期：2026-10-11。

用户要求增加本地 handler 解压、有序模糊/精确搜索、复制及重命名，整理压缩包/目录里的驱动和固件日志，并在网页分类、过滤、查看。特殊日志规则独立放在 `product_log/`，CLI 与网页共用 workflow；通用文件能力保留在 `autoenv/`。

## diag/var 两态驱动及统一时间视图扩展

用户确认：内核只按模块筛选、SlotId/CpuId 未知且保留来源；增加统一时间视图并保留分类视图；所有无日期内核行标记未知，绝不继承日期。本轮保持个人本机使用范围与 3 GiB 回收规则。

收集新增 auto/product/generic，网页采用 product：diag 历史名字典序、diag.log 最后；var 递归展开任意层支持包，数字轮转 `.1/.2/.10` 升序、current 最后。默认目录名精确匹配，部分路径仍可消歧；只缺一域继续解析。不同来源独立保留。独立评审复现 diag/var 相互嵌套会重复展开，同步修复为各域独占已指定子树，压缩包/文件/复制候选均隔离，并加入双向真实 ZIP 回归。

用户态 ModuleName 仅将 udk 及空白后缀别名归一，udk2 独立，原始名称保留。固件类型名称为 bios/IMP/IMU/HSM/其他数字。内核英文 dmesg 日期转设备本地微秒值；无日期为 NULL，时间视图排最后，范围过滤不命中。统一列表严格按时间排序，即使请求 stitched；原本模式仅影响驱动，固件保留段头时间。Slot/Cpu 只筛用户态/固件，Dev/type 只筛固件，模块表只筛两态驱动，不推导固件归属。

新增 `module_map.py`，默认 udk→udk/udk、UBE→UBE/mami，自动加入其他实际用户模块。表格可编辑，保存每批次 module-map.json；内核整行不区分大小写字面匹配，缺用户模块显示“不存在”，空关键词显示“未配置”。改表直接查已有索引，多重映射不复制记录。HTTP GET/POST modules，query/export 增加 kind=all、driver_domain、module；索引版本 2，旧版本需要重导入。通用 SDK 与环境脚本接口未改。

新增 `test_product_log_collection.py`、`test_product_log_domains.py`，扩充真实 CLI/HTTP 集成。`product_log_domain_sample.py` 生成 `logs/product-log-domain-demo/product.tar.gz` 与 source 原始目录：11 文件、2,003 用户态、408 内核态（8 未知）、3,600 固件、30 固件组、统一列表 6,011 条。UDK 用户态 500、内核 104；UBE 内核 mami 100。覆盖六压缩类型、var 多层嵌套、跨七文件十四 seg、多 Slot/Cpu、UDK 后缀和排除、未知日期、映射缺失/重叠/保存及来源。

最终全量离线回归：`python -X utf8 -m pytest -q --basetemp=logs/pytest-log-domains-full --tb=short`，**634 passed、2 skipped、1 warning，71.55s**；跳过为 Windows 符号链接权限，warning 为既有重复 ZIP fixture。包含 22 项新收集、30 项新解析/映射及新增真实 CLI/HTTP 验收。收集、解析、CLI、HTTP、回收聚焦在嵌套修复前 **113 passed，32.12s**；修复后收集与原解析 **51 passed，15.49s**，再由完整回归覆盖全部。

3 文件环境脚本静态合同、共享生成合同（包含在全量）、静态 CLI list、Python 编译、两份网页 JS 语法、三个相关 skill 校验、SDD 校验及 diff 检查通过。相关实现/导出/测试/文档/项目配置已复核；8 份同步文档的 86 个本地链接/锚点有效。环境登记、脚本示例、脚本生成 skill 和通用 API 目录没有本轮接口变化，保持既有内容并通过合同验证。

真实浏览器导入新演示包：11 文件/2,411 两态驱动/3,600 固件/30 次读取/0 诊断，耗时 1.22s；UDK 用户 500/内核 104，八条缺日期正文均显示未知。验证内核设备筛选禁用、原本模式和来源路径、三类统一日期排序、MEME 改表即时匹配 100、缺用户映射显示不存在且内核 100、重叠映射不增总数、刷新恢复配置。product 结果同时显示两个域的绝对目录，缺一域显示不存在。浏览器和页面无错误；截图为忽略目录 `logs/product-log-domain-demo/web-domains.jpg`。本任务固定入口 Web 已重启加载代码，schema 1 历史批次明确需重导入；新 ready 批次 ID 为 c9514aa89b0f41849be8ff6b4a779b5d。

所有新输入均为合成日志；未访问真实设备或 HDFS，也未将真实未知格式与 GB 级规模当作已验证能力。本轮没有新增该范围外的安全、并发或性能改造。

## 2026-10-11 独立离线单文件网页扩展

用户要求把当前日志功能打包到一个无额外运行依赖、直接打开的网页，且不影响已有代码。本轮仅新增 `product_log/standalone/`、`tools/build_product_log_page.py` 和三份 standalone 测试；已有 Python SDK、CLI、Web 代码、配置、公共导出和 wheel 声明不改。说明入口同步 README、quick start、文档地图、网页接手/使用、日志设计书及 tests README；本节延续同一未提交功能记录。

打包脚本只用 Python 3.11+ 标准库，可 `-I -S` 执行，不导入项目 SDK、下载资源或启动服务。默认输出 `dist/product-log-offline.html`，支持 `--output`，stdout 为绝对路径/字节数/SHA256。最终 HTML 内嵌页面、样式、Worker、独立 JavaScript 收集/解析/查询实现与三种解压组件（fflate/seek-bzip/xz-decompress），包含 XZ WASM 和完整第三方许可；使用者不需要 Python、Node、安装依赖、联网或服务端。

数据流：浏览器文件/目录选择 → ArrayBuffer 转移至 Blob Worker → ZIP/TAR/GZ/BZ2/XZ 内容识别与内存展开 → diag 单层/var 递归收集及来源排序 → 两态驱动/固件分段解析 → 动态模块表/分类与统一时间查询 → 有界页面列表/来源/全量导出。UDK 后缀、固件 Type 名称、时间未知、复制/重命名和目录唯一性延续产品规则，通过 Python 行为对照而非直接执行原 Python。

浏览器只读取显式选中的文件，来源显示相对路径及归档链；不能输入任意绝对目录或创建磁盘 product_log。复制/重命名只作用于内存副本，原件保持；刷新或关闭清除解析结果，模块表可 JSON 下载/导入。没有服务器历史、SQLite 或 3 GiB 磁盘清理。页面使用 Web Worker/Blob/ReadableStream/TextDecoder/WebAssembly，规模受浏览器可用内存限制；独立源码/打包用法见 [standalone README](../product_log/standalone/README.md)。

验证：新增产物测试直接执行 HTML 中实际内嵌 Worker，禁止 fetch/XMLHttpRequest/importScripts/WebSocket，真实多层混合压缩包得到 11 文件/2,003 用户态/408 内核态/3,600 固件/6,011 统一记录，30 组固件各 120 条；改模块表、过滤/分页/来源/导出正常，完整统一导出 SHA256 与原 Python 一致。验证标准库隔离构建、确定性、资源与 DOM、vendor 校验和原生产文件构建前后不变。

新增测试共 **120 项**：产物/隔离构建/完整 Worker/Unicode 高亮 9，真实归档 49，解析/查询与 Python 对照 62。独立综合审查修复 GZIP 尾部/成员间零填充兼容，仍逐成员校验 CRC/ISIZE/FHCRC 并拒绝非零垃圾；修复 Unicode casefold 命中高亮与未打包提示，许可支持小屏换行滚动。相关实现、测试、导出、示例、配置与文档已复核，103 个本地文档链接/锚点有效。

最终全量：`python -X utf8 -m pytest -q --basetemp=logs/pytest-offline-page-full --tb=short`，**754 passed、2 skipped、1 warning，121.22s**。两项跳过为本机 Windows 符号链接权限，warning 为既有重复 ZIP fixture。3 文件环境脚本合同、16 项共享脚本测试、静态 CLI 发现、Python 编译、4 份新 JS 语法、三个项目 skill 校验、SDD 校验与 diff 检查通过。当前服务保持运行，本轮没有重启或新增启动器。

`python -I -S -X utf8 tools/build_product_log_page.py` 已生成 **275,837 字节**的 `dist/product-log-offline.html`，SHA256 为 `822922d4dbeea95ad46bc4b6be0caf5390948a7b24b42b5e5c7af557a0b7a899`。产物在忽略目录，可复制一个文件分发；所有解压资源和许可已内嵌。

验收缺口：内置浏览器安全策略明确拒绝 `file://`，本轮未尝试绕过，未完成直接双击打开、真实 Blob Worker 与界面操作验收。Node VM 的实际内嵌代码执行及静态 HTML 检查不能替代该浏览器验收。未访问真实 SSH/Telnet/FTP/HDFS 目标，未开展额外 GB 级压测。相关 SDK/注册指南/机读接口/脚本示例/skills/config 不更新，因为此轮没有这些接口或执行合同变化；全量回归仍核对。

## 界面与接口

- SDK：`FileHandleGroup`、`fuzz_search`、`accurate_search`、`get_handler`、`find_subdir`、`FileHandle.unzip/move_to/rename`。
- 独立 CLI：`python -X utf8 -m product_log INPUT`，支持目录部分路径、可重复文件模式、复制/重命名、并发和输出目录。
- 唯一 Web 启动保持 `python -X utf8 startWeb.py`；`/product-logs/` 支持压缩包/文件夹上传或服务端目录、分类/时间/关键词过滤、来源和导出。
- HTTP：`/api/product-logs/` 的上传、任务生命周期、批次、分组、查询、来源、导出接口。

## 数据流与重要语义

输入 → 独立任务目录 → 唯一目录匹配 → 一轮匹配子包展开 → 可选复制/重命名 → 收集有序来源 → 并发流式解析/分片 → 恢复文件/行序并组装 seg → SQLite 索引 → ready 批次 → 网页分页/导出。

固件每个 seg 都有标准日期头，一段不会跨文件，多段可以跨文件。seg=0 开新读取，后续非零段按已确认的文件名字典序接续；不同来源/主机/服务/模块/设备隔离。重复正文和非零段保留，缺段有诊断。每行保留所属 seg 真实读取时间；默认时间升序，可切批次连续拼接。驱动按 Slot/Cpu/Module、固件按 Slot/Cpu/Dev/LogType 分类。`move_to` 按用户要求为复制；rename 返回新 handler。目录多命中报错，`diag/slot62` 可消歧。工作区每任务独立，页面显示绝对路径。

## 主文件

`autoenv/file_handles.py`、`autoenv/local_archives.py`、`autoenv/__init__.py`；`product_log/{workflow,parser,index,web,__main__}.py` 与 `static/`；`webPage/server.py/index.html`；`pyproject.toml`；对应 SDK、核心、HTTP、CLI 集成测试。说明见 [product_log/README.md](../product_log/README.md)，设计来源见 [设计书](../docs/LOCAL_PRODUCT_LOG_DESIGN.md)。

## 验证与边界

- 前一阶段全量回归（含混排样本和 3 GiB 回收）：`python -X utf8 -m pytest -q --basetemp=logs/pytest-retention-full --tb=short`，**580 passed、2 skipped、1 warning，57.69s**。两项跳过为 Windows 不允许创建符号链接；warning 来自已有重复 ZIP 成员 fixture。新增回收测试中的四种 Windows junction 场景均实际通过；本轮最新结果见上方两态扩展记录。
- SDK 聚焦及相关会话回归通过；审查修复后的核心/CLI/真实 loopback HTTP 聚焦 **35 passed，11.58s**。构造 ZIP/TAR/TAR.GZ/TGZ/GZ/BZ2/XZ、错误后缀、损坏/恶意成员、多文件、多维度、重复正文、缺段、复制/重命名和取消。
- 既有环境脚本静态校验 3 个文件通过；共享生成脚本合同 **16 passed**；CLI list 静态发现通过，未执行环境脚本。
- Python compileall、两份网页 JS 语法、接口 JSON、git diff --check 通过；三个相关项目 skill 的 quick_validate 与 SDD skill pack 校验通过。原 `scripts/example.py/all_interfaces.py` 保持最小远端协议示例，新增本地用法放到 product_log README，不改变其实际执行步骤。
- 本地 wheel 构建确认 product_log 模块、CLI 及三份静态资源均已包含；Web 本身仍按源码仓库唯一入口启动，没有宣称独立 wheel 的完整 Web 安装验收。
- 真实浏览器已验证文件选择上传压缩包、本机路径读取、7 个日志文件/7 条驱动/3 条固件/1 个 dump、动态分块、读取时间/拼接顺序、中文搜索、驱动原本模式和来源行。另以 1,000 条驱动验证虚拟列表（顶部仅 35 个 DOM 行，末尾正确到达第 1,000 行）及搜索上下文（1 个命中、前后各 2 行共 5 行）；浏览器控制台无错误。验收截图保存于忽略的 `logs/product-log-demo/web-final.png`。分块上传/导出/取消/忙碌/历史隔离均有真实 loopback HTTP 回归。
- 最终合成基线：8 文件、12,271,816 输入字节，48,000 驱动+48,000 固件正文；4 workers 解析及索引 **6.281s**。关键词 960 命中，首/次页 **0.0841/0.0205s**，索引 62,832,640 字节。单次本机测量，不承诺 GB 级吞吐或真实样本性能；未测峰值内存。

测试解释器为本机 Python 3.11，PYTHONPATH 加载仓库已有 `.test-deps`（Paramiko 3.5.1/scp），真实 TCP 回归在允许 loopback 的权限环境执行。所有输入为本地构造，不访问真实设备、串口/HDFS。

## 独立评价与本轮修复

编码收尾阶段新开独立 subagent 完成 [全项目评审](../docs/PROJECT_REVIEW_2026-10-11.md)。它实际复现了微秒排序截断及 `!/` 展示路径来源 ID 冲突；两者已修复并加入回归：使用整数微秒、索引格式守卫及结构化 provenance_parts，歧义相对重命名明确失败。独立评审的相关 4 项聚焦回归也通过。

原评审前三建议为 Web 请求来源边界、历史配置/脚本启动并发事务、任务磁盘保留/回收。报告区分已复现、静态推断及未验证场景；依据用户后续取舍，本轮落实磁盘回收和文档整理，其他建议保持现状。真实格式变体、GB 级规模、真实设备/HDFS、浏览器攻击验收仍未验证。

## 混排样本扩充

依用户追加要求，`tests/product_log_sample.py` 可生成 `logs/product-log-demo/product.tar.gz` 和未压缩原始目录。Type 0–4 × Dev 0–5 共 30 种固件组合，各 120 条正文、14 个不等长 seg 跨 7 文件；`udk log/UBE/MEME/HBOOT` 四种驱动各 500 条。随机混排保留每段完整性和每组来源顺序，不同组合读取时间不规则、同组 seg 读取时间递增，覆盖来源组装与显示时间两个顺序。嵌套六种压缩格式保留，样本固定种子、内附数量概要。

真实 CLI 回归逐组验证数量、seg/正文序号及驱动名称；同时修复 `ModuleName=udk log` 被空白截断的问题，保留名称内空格。操作说明和 tests README 已补充样本重建及重新导入规则；SDK 导出、环境登记/脚本、quick start、配置和项目生成技能没有公共接口变化。

扩充后核心/CLI 聚焦 **32 passed，11.07s**，全量 **556 passed、2 skipped、1 warning，53.40s**；编译、3 文件静态脚本合同、静态登记发现、git diff --check 均通过。直接解析实际演示包得到 7 文件、3,600 固件正文、2,000 驱动、30 次固件读取、0 未分类、0 诊断；所有固件组跨 7 个来源，驱动精确名称各 500。核对批次在忽略目录 `logs/product-log-demo/mixed-20261011`，原始文本在 `source/diag/slot62`。已重启此前本任务启动的 Web 服务以加载名称空格修复，历史批次仍须用户重新导入新样本；本次没有重复浏览器验收或访问真实远端。

## 本地使用取舍、自动回收与文档整理

用户明确本项目用于个人、本机、单任务操作：采纳评审 004，达到 3 GB 后自动清理，并优先处理 007/008 的文档说明。本轮按 **3 GiB（3 × 1024³ 字节）** 实现阈值。001 来源/令牌保护、002 配置并发治理、003 旧脚本生命周期、005 额外性能优化、006 主机身份校验不在本轮范围；真实日志无需先脱敏。评审原文保留，追加用户取舍，避免把未采纳项表述为已修复。

新增 `product_log/retention.py`，只管理 `webPage/.runtime/product-logs/{uploads,batches}/<opaque_id>` 的普通文件逻辑字节数，含上传、展开文件、索引、查询缓存和元数据。达到阈值后按创建时间回收最旧可删除目录，直到实际占用低于阈值；删除失败后重新计量，部分删除已降到阈值以下就停止继续回收。受管目标及祖先经过检查，含 symlink/junction/reparse 的目录跳过并提示。CLI 输出、外部原始日志和普通脚本运行目录均不纳入。

`product_log/web.py` 在加载历史、创建上传/任务、上传完成、任务结束、历史或占用检查时维护数据；活动任务、当前浏览批次、最新成功结果、最近终态任务及当前上传受到保护。上传与批次引用关系、原始输入路径持久化到 `web-task.json` 和最终 manifest，重启恢复；通过本机路径再次导入旧受管批次时，源批次随引用它的任务保留。仅保护数据已超限时继续保留并提示，因此这是清理触发阈值，不是硬配额。

新增 `GET /api/product-logs/storage?batch_id=<id>`，显式空值解除浏览保护，无参数保持原保护；返回 usage/limit、回收 ID、实际回收字节、错误及最近回收信息。`GET batches` 附带 storage 状态。网页新增“任务数据/检查占用”，清理后同步历史列表。

文档新增 [DOCUMENTATION_MAP.md](../docs/DOCUMENTATION_MAP.md)，明确常用脚本、SDK/CLI、源码 Web、独立产品日志与保留高级 API 的归属和权威入口；README、两份快速开始、网页用法、接手说明、日志设计书和测试说明同步。新增 [constraints-local-tested.txt](../constraints-local-tested.txt) 记录 Windows/Python 3.11.4 下实测的六项直接依赖版本；不是传递依赖完整锁文件，不宣称洁净安装或多平台验收。通用 SDK 签名、脚本登记规则、示例与生成技能没有新增接口变化，相关合同已重新核对。

验证：回收 **23 项**均通过（真实临时目录、精确触发、小阈值、旧数据优先、查看/活动/最新保护、上传和受管输入引用、重启、部分删除和四种 Windows junction）；核心/CLI/HTTP/回收聚焦 **61 passed，16.73s**；最终全量 **580 passed、2 skipped、1 warning，57.69s**。3 文件环境脚本静态校验、静态 CLI 登记发现、Python 编译、两份网页 JS 语法、三个相关 skill 校验、SDD 校验和 diff 检查通过，依赖快照逐项匹配本机版本。测试仅在隔离小目录内触发删除，没有创建数 GiB 文件或访问真实远端。

真实浏览器重新导入混排 `product.tar.gz`，得到 7 文件/2,000 驱动/3,600 固件/30 固件分组/0 诊断；四驱动名称各 500 条，选中 `udk log` 结果为 500 条。占用显示 11.2 MiB/3.00 GiB，“检查占用”正常，页面与控制台无错误。固定入口服务已重启加载代码，验收截图为忽略的 `logs/product-log-demo/web-retention.png`。实际演示目录远低于阈值，浏览器验收未触发其删除；回收行为由真实目录及 HTTP 测试证明。最近清理记录保存在服务内存，重启不保留回收历史。

Git 分发时，`.gitattributes` 对 `product_log/standalone/vendor/` 禁用文本换行转换，保留原始第三方资源及许可字节；否则 Windows `core.autocrlf=true` 的新 checkout 会改变资源 SHA256，导致独立网页构建失败。第三方原包中的空白保留，作者文件继续检查 whitespace。

提交前以 `git checkout-index` 在忽略的隔离目录实际导出暂存树：八个 vendor 资源 SHA256 全部匹配；从该 Windows checkout 用 `python -I -S -X utf8 tools/build_product_log_page.py` 成功构建，得到同一 275,837 字节 HTML，SHA256 为 `822922d4dbeea95ad46bc4b6be0caf5390948a7b24b42b5e5c7af557a0b7a899`。暂存区 diff whitespace 检查通过，原库字节未改动。
