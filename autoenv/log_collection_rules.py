"""Concrete product log rules shared by collection and bounded Web preview."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .logs import LogCollection, MetadataPattern, TimestampPattern


TIMESTAMP_REGEX = (
    r"(?:(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\s+)?"
    r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
)
METADATA_REGEXES = (
    r"\bslotid=(?P<slot_id>[^\s,\]]+)",
    r"\bsocketid=(?P<socket_id>[^\s,\]]+)",
)
AUTH_REGEX = r"\[AUTH\]"
BLOCK_BEGIN_REGEX = r"\[DB\] BEGIN\b"
BLOCK_END_REGEX = r"\[DB\] END\b"
BLOCK_CONSUME_REGEX = r"LOG_TYPE\[\d+\]\s+seg\[\d+\]"


def timestamp_pattern() -> TimestampPattern:
    return TimestampPattern(TIMESTAMP_REGEX)


def metadata_patterns() -> tuple[MetadataPattern, ...]:
    return tuple(MetadataPattern(regex) for regex in METADATA_REGEXES)


def apply_configured_rules(group):
    result = group.match_line(AUTH_REGEX, target_file="auth.log")
    if not result.success:
        return result
    return group.match_block(
        BLOCK_BEGIN_REGEX,
        BLOCK_END_REGEX,
        target_file="database.log",
        boundary_mode="strict",
        consume_regex=BLOCK_CONSUME_REGEX,
    )


class _PreviewRecorder:
    def __init__(self) -> None:
        self.index = 0

    def next_operation_id(self) -> str:
        self.index += 1
        return f"preview-{self.index:04d}"

    def record_result(self, _name: str, _result: object) -> None:
        return None

    def log(self, _message: str, **_options: object) -> None:
        return None


def preview_log_sample(payload: bytes, name: str = "sample.log") -> dict[str, object]:
    """Run the exact configured rules against one bounded, non-persistent sample."""

    if not isinstance(payload, bytes):
        raise TypeError("preview payload must be bytes")
    if len(payload) > 8 * 1024 * 1024:
        raise ValueError("preview sample exceeds 8 MiB")
    safe_name = Path(name).name or "sample.log"
    with TemporaryDirectory(prefix="autoenv-log-preview-") as temporary:
        root = Path(temporary)
        run_dir = root / "logs" / "preview"
        run_dir.mkdir(parents=True)
        collection = LogCollection(
            run_id="preview", run_dir=run_dir, recorder=_PreviewRecorder()
        )
        sample = collection.expanded_dir / safe_name
        sample.write_bytes(payload)
        collection._extracted = True
        group = collection.group(
            glob=safe_name,
            timestamp=timestamp_pattern(),
            metadata_patterns=metadata_patterns(),
        )
        result = apply_configured_rules(group)
        if not result.success:
            raise RuntimeError(result.error_message or "preview rule failed")
        ordered = sorted(
            collection._records,
            key=lambda item: (
                item.target,
                item.file_order,
                item.line_number,
                item.rule_order,
            ),
        )
        targets: dict[str, dict[str, object]] = {}
        for record in ordered:
            target = targets.setdefault(record.target, {"count": 0, "examples": []})
            target["count"] = int(target["count"]) + 1
            examples = target["examples"]
            if isinstance(examples, list) and len(examples) < 20:
                examples.append(
                    {
                        "line": record.line_number,
                        "text": record.text,
                        "timestamp": (
                            record.timestamp.display() if record.timestamp else "-"
                        ),
                        "timestamp_source": record.timestamp_source,
                        "slot_id": record.slot_id,
                        "slot_id_source": record.slot_id_source,
                        "socket_id": record.socket_id,
                        "socket_id_source": record.socket_id_source,
                    }
                )
        return {
            "sample": safe_name,
            "input_lines": len(group._parsed_lines(sample)),
            "retained_lines": len(ordered),
            "targets": targets,
            "rules_hash": collection._rules_hash(),
        }
