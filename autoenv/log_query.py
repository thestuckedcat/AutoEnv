"""Read-only queries over finalized log-collection SQLite indexes.

Collection and query are intentionally decoupled: the workflow process owns
remote access and file creation, while the local Web server only reads batches
whose atomic manifest says ``ready``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date as date_type, datetime, time as time_type
from pathlib import Path


def list_log_batches(root_dir: Path | str) -> list[dict[str, object]]:
    """List complete batches, ignoring building, failed, or damaged manifests."""

    root = Path(root_dir).resolve()
    batches: list[dict[str, object]] = []
    logs = root / "logs"
    if not logs.is_dir():
        return batches
    for manifest in logs.glob("*/log_collection/manifest.json"):
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("status") == "ready":
            batches.append(
                {
                    "batch_id": str(value.get("batch_id", "")),
                    "alias": str(value.get("alias", "")),
                    "collected_at": str(value.get("collected_at", value.get("updated_at", ""))),
                    "updated_at": str(value.get("updated_at", "")),
                    "targets": list(value.get("targets", [])),
                    "record_count": int(value.get("record_count", 0)),
                }
            )
    return sorted(batches, key=lambda item: str(item["collected_at"]), reverse=True)


def list_log_targets(root_dir: Path | str, batch_id: str) -> list[str]:
    batch = _batch_dir(root_dir, batch_id)
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("status") != "ready":
        raise ValueError("log batch is not ready")
    targets = manifest.get("targets", [])
    if not isinstance(targets, list):
        raise ValueError("log batch targets are invalid")
    return [str(item) for item in targets]


def query_log_records(
    root_dir: Path | str,
    batch_id: str,
    target: str,
    *,
    page: int = 1,
    limit: int = 200,
    query_date: str = "",
    query_time: str = "",
    window_minutes: int = 60,
    keyword: str = "",
    context_lines: int = 0,
) -> dict[str, object]:
    """Filter one derived target and optionally include lines around Find hits.

    Filtering before pagination makes Find operate over the whole target rather
    than only the current page.  When ``context_lines`` is non-zero, pagination
    is applied to matching rows first and each page is then expanded in target
    sequence order.  This guarantees that a hit is returned together with all
    available preceding/following context, including across an ordinary page
    boundary.  Context is intentionally not constrained by the time filter: it
    explains a selected hit rather than becoming another hit itself.

    Rows without a timestamp remain visible during ordinary browsing but are
    excluded from time-window hits because their temporal distance cannot be
    established safely.
    """

    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if not 1 <= window_minutes <= 24 * 60:
        raise ValueError("window_minutes must be between 1 and 1440")
    if isinstance(context_lines, bool) or not isinstance(context_lines, int):
        raise TypeError("context_lines must be an integer")
    if not 0 <= context_lines <= 50:
        raise ValueError("context_lines must be between 0 and 50")
    if not isinstance(keyword, str):
        raise TypeError("keyword must be a string")
    normalized_keyword = keyword.strip()
    if len(normalized_keyword) > 200:
        raise ValueError("keyword must not exceed 200 characters")
    folded_keyword = normalized_keyword.casefold()
    targets = list_log_targets(root_dir, batch_id)
    if target not in targets:
        raise ValueError("unknown target file")
    if query_date and not query_time:
        raise ValueError("time is required when date is specified")
    wanted_date_value: date_type | None = None
    if query_date:
        wanted_date_value = date_type.fromisoformat(query_date)
    center = _parse_clock(query_time) if query_time else None
    half_window = window_minutes * 60 / 2
    batch = _batch_dir(root_dir, batch_id)
    # Fetch in target sequence order.  The index is bounded to a local batch and
    # target names come from its ready manifest, preventing arbitrary file/table
    # selection through HTTP query parameters.
    with sqlite3.connect(batch / "index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM records WHERE target = ? ORDER BY sequence", (target,)
        ).fetchall()
    hit_indexes: list[int] = []
    for index, row in enumerate(rows):
        if folded_keyword and folded_keyword not in str(row["text"]).casefold():
            continue
        clock = row["clock_seconds"]
        row_date = row["date_key"]
        if center is not None:
            if clock is None:
                continue
            if wanted_date_value is not None and row_date:
                # Full dates use an absolute distance, so a window centered just
                # after midnight can include records from the preceding date.
                row_date_value = date_type.fromisoformat(str(row_date))
                row_value = datetime.combine(row_date_value, time_type())
                center_value = datetime.combine(wanted_date_value, time_type())
                distance = abs(
                    (row_value - center_value).total_seconds() + int(clock) - center
                )
            else:
                # Logs that only contain a clock use circular 24-hour distance;
                # e.g. 23:59 and 00:01 are two minutes apart, not 23h58m.
                distance = _clock_distance(int(clock), center)
            if distance > half_window:
                continue
        hit_indexes.append(index)

    start = (page - 1) * limit
    page_hits = hit_indexes[start : start + limit]
    effective_context = context_lines if folded_keyword else 0
    visible_indexes = set(page_hits)
    if effective_context:
        for index in page_hits:
            first = max(0, index - effective_context)
            last = min(len(rows), index + effective_context + 1)
            visible_indexes.update(range(first, last))

    # A row may be context for two nearby hits.  The set removes duplicates and
    # sorting restores the exact target-file sequence.  Only hits selected by
    # the keyword/time filter receive the match role; nearby text that happens
    # to contain the same word but is outside the time window stays context.
    hit_set = set(hit_indexes)
    selected = [
        _record_dict(
            rows[index],
            find_role=(
                "match"
                if folded_keyword and index in hit_set
                else "context" if effective_context else ""
            ),
        )
        for index in sorted(visible_indexes)
    ]
    return {
        "records": selected,
        "page": page,
        "limit": limit,
        # For Find queries, total and pagination describe actual hits rather
        # than the variable number of expanded context rows returned per page.
        "total": len(hit_indexes),
        "has_more": start + limit < len(hit_indexes),
        "keyword": normalized_keyword,
        "context_lines": effective_context,
        "returned_count": len(selected),
    }


def _record_dict(row: sqlite3.Row, *, find_role: str) -> dict[str, object]:
    """Serialize one indexed row with its Web Find presentation role."""

    return {
        "id": row["id"],
        "sequence": row["sequence"],
        "text": row["text"],
        "source_file": row["source_file"],
        "source_line": row["source_line"],
        "timestamp": _display_timestamp(row),
        "clock_seconds": row["clock_seconds"],
        "date_key": row["date_key"],
        "timestamp_source": row["timestamp_source"],
        "incomplete_block": bool(row["incomplete_block"]),
        "find_role": find_role,
    }


def _batch_dir(root_dir: Path | str, batch_id: str) -> Path:
    if not isinstance(batch_id, str) or Path(batch_id).name != batch_id or batch_id in {"", ".", ".."}:
        raise ValueError("invalid batch id")
    root = Path(root_dir).resolve()
    batch = (root / "logs" / batch_id / "log_collection").resolve()
    try:
        batch.relative_to((root / "logs").resolve())
    except ValueError as exc:
        raise ValueError("invalid batch id") from exc
    if not batch.is_dir():
        raise FileNotFoundError("log batch was not found")
    return batch


def _parse_clock(value: str) -> int:
    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise ValueError("time must be HH:MM or HH:MM:SS")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise ValueError("time is outside the valid clock range")
    return hour * 3600 + minute * 60 + second


def _clock_distance(left: int, right: int) -> int:
    direct = abs(left - right)
    return min(direct, 24 * 3600 - direct)


def _display_timestamp(row: sqlite3.Row) -> str:
    # Invalid calendar text is preserved as a different diagnostic state from
    # a line that had no timestamp at all.  Both are excluded from time-window
    # correlation because clock_seconds is NULL, but the Web UI renders them as
    # ``?`` and ``-`` respectively.
    if row["timestamp_source"] == "invalid":
        return "?"
    if row["clock_seconds"] is None:
        return "-"
    clock = f"{int(row['hour']):02d}:{int(row['minute']):02d}"
    if row["second"] is not None:
        clock += f":{int(row['second']):02d}"
    return f"{row['date_key']} {clock}" if row["date_key"] else clock
