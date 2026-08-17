from __future__ import annotations

from autoenv import SSHDefaults, TimestampPattern
from autoenv.web_tools import register_web_tool


def _parse_remote_dirs(value: object) -> list[str]:
    """Convert the Web textarea into an ordered list of remote directories.

    One path per line avoids ambiguous separators: spaces and commas are legal
    POSIX filename characters and therefore must not split a remote path.  Empty
    lines are ignored so operators can visually group entries.  Duplicate paths
    are left intact here and rejected by ``LogCollection.download_many()`` with
    an explicit message rather than being collected twice silently.
    """

    if not isinstance(value, str):
        raise TypeError("remote_dirs must be submitted as text")
    directories = [line.strip() for line in value.splitlines() if line.strip()]
    if not directories:
        raise ValueError("at least one remote log directory is required")
    return directories


@register_web_tool(
    name="log-collection",
    title="日志收集与关联分析",
    description="通过已注册 SSH 环境批量收集、解压、分类并关联查看日志。",
    kind="workflow",
    renderer="log_collection",
    fields=[
        {
            "name": "remote_dirs",
            "label": "远端日志目录（每行一个）",
            "type": "textarea",
            "required": True,
            "placeholder": "/var/log/product\n/var/log/product-backup",
        },
        {
            "name": "alias",
            "label": "本次日志别名（可选）",
            "type": "text",
            "required": False,
            "placeholder": "例如：问题单 1234 复现",
        },
    ],
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
        description="通过 SCP 从所填多个目录批量收集 cpdt_* 日志。",
        defaults=SSHDefaults(),
    )
    remote_dirs = _parse_remote_dirs(ctx.argument("remote_dirs", required=True))
    alias = str(ctx.argument("alias", default="") or "").strip()

    # Phase 1: each remote directory is downloaded into its own source-NNN
    # namespace.  The batch is all-or-nothing, so a missing directory cannot
    # produce a deceptively complete analysis result.
    collection = ctx.create_log_collection(alias=alias)
    result = collection.download_many(
        log_server,
        remote_dirs=remote_dirs,
        glob="cpdt_*",
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
    # Only extracted cpdt*.log files enter business analysis.  The framework
    # orders them across all remote directories by inherited remote mtime and
    # then stable relative path.
    group = collection.group(glob="cpdt*.log", timestamp=timestamp)

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
