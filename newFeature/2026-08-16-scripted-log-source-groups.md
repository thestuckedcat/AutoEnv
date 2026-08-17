# 脚本化日志来源与 block 正文排除

## 目标

日志收集页面只让用户绑定 SSH 环境，不再接收远端目录、下载通配符或批次别名。远端读取范围成为注册脚本中的可审查配置：一条路径绑定一个 basename glob，下载和递归解压后的结果直接组成一个独立 group。

本功能延续 `2026-08-15-log-collection-analysis.md` 的日志流水线，以及 `2026-08-16-multi-directory-busybox-log-collection.md` 的多目录隔离和 BusyBox 兼容约束。

## 用户可见行为

- Tools 页的“日志收集与关联分析”只显示 SSH 资源选择器。
- 内置配置位于 `webPage/tools/log_collection.py::LOG_SOURCES`；默认来源为 `/var/log/product`，下载 glob 为 `cpdt_*`，group 名为 `cpdt`。
- 每个来源使用自己的下载 glob，并隔离到 `raw/source-NNN/`；任一来源失败时整个批次失败。
- ZIP、GZ、TAR.GZ、TGZ 仍会递归安全解压。
- 一个来源直接下载的普通文件和递归解压得到的所有非压缩叶子文件直接组成同名 group，不执行第二次 basename glob。压缩包文件保留在 `expanded/` 供审计，但不作为日志文本解码。
- group 继续使用 `match_line()` 和 `match_block()` 生成 target `.log` 文件。
- `match_block(..., exclude_regex=...)` 可从已经选中的 block 正文中删除匹配行。删行发生在 block 边界和关联时间确定之后，不会改变其他保留行的时间。

## 公共接口

```python
from autoenv import LogSource

sources = (
    LogSource(name="cpdt", remote_dir="/var/log/product", glob="cpdt_*"),
)
result = collection.download_sources(host, sources=sources)
result = collection.extract_all()
groups = collection.source_groups(timestamp=timestamp)
result = groups["cpdt"].match_block(
    begin_regex,
    end_regex,
    "database.log",
    exclude_regex=r"^debug=",
)
```

`LogSource.name` 只能使用字母、数字、点、下划线和连字符，且必须以字母或数字开头。同一批次的 name 必须唯一，相同的路径/glob 对也不能重复。兼容接口 `download()`、`download_many()` 和 `group()` 保留原有行为。

## 顺序与可追踪性

- 来源按 `LOG_SOURCES` 声明顺序处理。
- 每个来源内部按继承的远端 mtime、相对路径大小写折叠值和原始相对路径稳定排序。
- 不同 group 使用不重叠的全局文件顺序区间；多个 group 写入同一 target 时仍可确定性合并。
- `manifest.json.download.sources` 记录 name、remote_dir 和 glob；每个目录条目同时记录实际使用的 glob。

## 主要文件

- `autoenv/logs.py`
- `autoenv/__init__.py`
- `webPage/tools/log_collection.py`
- `tests/test_log_collection.py`
- `docs/ENVIRONMENT_REGISTRATION_GUIDE.md`
- `docs/WEB_ARCHITECTURE_AND_HANDOFF.md`

## 验证范围

- 不同来源分别使用各自 glob。
- 普通文件与压缩包内嵌套日志进入正确 group，压缩包容器不参与文本匹配。
- group 可分别生成 AUTH line target 和 DB block target。
- block 排除规则删除正文行，并保留由被删行提供的关联时间。
- 重复来源名在任何远端传输前失败。
- Web Tool 元数据没有普通输入字段，只暴露 SSH 资源。

未执行真实远端 SSH/SCP 冒烟测试；项目测试使用本地 fake host 覆盖传输结果、目录隔离和后续处理契约。
