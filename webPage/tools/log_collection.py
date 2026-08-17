from __future__ import annotations

from autoenv import LogSource, SSHDefaults, TimestampPattern
from autoenv.web_tools import register_web_tool


LOG_SOURCES = (
    LogSource(
        name="cpdt",
        remote_dir="/var/log/product",
        glob="cpdt_*",
    ),
)


@register_web_tool(
    name="log-collection",
    title="日志收集与关联分析",
    description="通过已注册 SSH 环境批量收集、解压、分类并关联查看日志。",
    kind="workflow",
    renderer="log_collection",
    fields=[],
)
def collect_logs(ctx):
    """Collect cpdt logs, extract archives, and build two analysis targets.

    This file is the product-rule layer.  When component log naming, timestamp
    syntax, or AUTH/DB markers evolve, refine the constants and matching calls
    here; archive safety, source ordering, and index generation remain framework
    responsibilities in ``autoenv.logs``.
    """

    # Resource registration is a literal call so AutoEnv can discover the SSH
    # dependency without executing the Tool.  The Web page then asks the user to
    # bind an environment containing the matching ``1260网口`` label.
    log_server = ctx.register_ssh_host(
        "log_server",
        resource_label="1260网口",
        alias="日志服务器网口",
        description="通过 SCP 按脚本固化的路径和通配符收集日志。",
        defaults=SSHDefaults(),
    )

    # The Web request selects only the registered SSH resource.  Remote scope is
    # code-reviewed here: every source binds one fixed path to one download glob.
    collection = ctx.create_log_collection()
    result = collection.download_sources(
        log_server,
        sources=LOG_SOURCES,
        protocol="scp",
    )
    if not result.success:
        return result

    # Phase 2: recursively expand supported ZIP/GZ/TAR archives.  The framework
    # enforces traversal, link, special-file, count, and total-size boundaries.
    result = collection.extract_all()
    if not result.success:
        return result

    # Phase 3: define the timestamp contract used by both rule types and by Web
    # time-window correlation.  Date is optional for logs that only print a
    # clock; hours/minutes/seconds are required by this concrete expression.
    timestamp = TimestampPattern(
        r"(?:(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\s+)?"
        r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    )
    # Each configured source becomes one group containing its direct plain files
    # plus all non-archive descendants produced by recursive extraction.  There
    # is no second filename glob after download, so the source table is the full
    # collection boundary.
    groups = collection.source_groups(timestamp=timestamp)
    group = groups["cpdt"]

    # AUTH is a line rule: emit only matching lines.  A line without its own
    # timestamp inherits the last timestamp from the same source file.
    result = group.match_line(r"\[AUTH\]", target_file="auth.log")
    if not result.success:
        return result
    # DB is a block rule: boundary markers are omitted and the enclosed payload
    # shares the begin time.  The framework also preserves implicit/truncated
    # blocks and records incomplete EOF blocks in SQLite.
    result = group.match_block(
        r"\[DB\] BEGIN\b",
        r"\[DB\] END\b",
        target_file="database.log",
    )
    if not result.success:
        return result
    # Phase 4: write target .log files, the per-line SQLite index, and finally a
    # ready manifest.  The Web query API ignores batches that never reach ready.
    return collection.finalize()
