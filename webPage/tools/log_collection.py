from __future__ import annotations

from autoenv import SSHDefaults, TimestampPattern
from autoenv.web_tools import register_web_tool

@register_web_tool(
    name="log-collection",
    title="日志收集与关联分析",
    description="通过已注册 SSH 环境批量收集、解压、分类并关联查看日志。",
    kind="workflow",
    renderer="log_collection",
    fields=[
        {
            "name": "remote_dir",
            "label": "远端日志目录",
            "type": "text",
            "required": True,
            "placeholder": "/var/log/product",
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
    log_server = ctx.register_ssh_host(
        "log_server",
        resource_label="1260网口",
        alias="日志服务器网口",
        description="通过 SCP 从当前目录批量收集 cpdt_* 日志。",
        defaults=SSHDefaults(),
    )
    remote_dir = str(ctx.argument("remote_dir", required=True)).strip()
    alias = str(ctx.argument("alias", default="") or "").strip()

    collection = ctx.create_log_collection(alias=alias)
    result = collection.download(
        log_server,
        remote_dir=remote_dir,
        glob="cpdt_*",
        protocol="scp",
    )
    if not result.success:
        return result

    result = collection.extract_all()
    if not result.success:
        return result

    timestamp = TimestampPattern(
        r"(?:(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\s+)?"
        r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    )
    group = collection.group(glob="cpdt*.log", timestamp=timestamp)
    result = group.match_line(r"\[AUTH\]", target_file="auth.log")
    if not result.success:
        return result
    result = group.match_block(
        r"\[DB\] BEGIN\b",
        r"\[DB\] END\b",
        target_file="database.log",
    )
    if not result.success:
        return result
    return collection.finalize()
