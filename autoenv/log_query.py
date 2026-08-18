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
                    "index_schema_version": value.get("index_schema_version"),
                    "rule_schema_version": value.get("rule_schema_version"),
                    "rules_hash": str(value.get("rules_hash", "")),
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
    slot_id: str = "",
    socket_id: str = "",
    offset: int | None = None,
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
    if offset is not None and (isinstance(offset, bool) or offset < 0):
        raise ValueError("offset must be at least 0")
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
    if not isinstance(slot_id, str) or not isinstance(socket_id, str):
        raise TypeError("slot_id and socket_id must be strings")
    normalized_slot = slot_id.strip()
    normalized_socket = socket_id.strip()
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
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(records)").fetchall()
        }
        if not folded_keyword and center is None:
            clauses = ["target = ?"]
            parameters: list[object] = [target]
            if normalized_slot:
                if "slot_id" not in columns:
                    return _empty_query_result(page, limit, normalized_keyword, offset)
                clauses.append("slot_id = ?")
                parameters.append(normalized_slot)
            if normalized_socket:
                if "socket_id" not in columns:
                    return _empty_query_result(page, limit, normalized_keyword, offset)
                clauses.append("socket_id = ?")
                parameters.append(normalized_socket)
            where = " AND ".join(clauses)
            total = int(
                connection.execute(
                    f"SELECT count(*) FROM records WHERE {where}", parameters
                ).fetchone()[0]
            )
            start = offset if offset is not None else (page - 1) * limit
            selected_rows = connection.execute(
                f"SELECT * FROM records WHERE {where} ORDER BY sequence LIMIT ? OFFSET ?",
                [*parameters, limit, start],
            ).fetchall()
            return {
                "records": [
                    _record_dict(row, find_role="") for row in selected_rows
                ],
                "page": start // limit + 1,
                "offset": start,
                "limit": limit,
                "total": total,
                "has_more": start + limit < total,
                "keyword": normalized_keyword,
                "context_lines": 0,
                "returned_count": len(selected_rows),
            }
        rows = connection.execute(
            "SELECT * FROM records WHERE target = ? ORDER BY sequence", (target,)
        ).fetchall()
    hit_indexes: list[int] = []
    for index, row in enumerate(rows):
        if normalized_slot and (
            "slot_id" not in columns or row["slot_id"] != normalized_slot
        ):
            continue
        if normalized_socket and (
            "socket_id" not in columns or row["socket_id"] != normalized_socket
        ):
            continue
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

    if offset is not None:
        effective_context = context_lines if folded_keyword else 0
        visible_indexes = set(hit_indexes)
        if effective_context:
            for index in hit_indexes:
                first = max(0, index - effective_context)
                last = min(len(rows), index + effective_context + 1)
                visible_indexes.update(range(first, last))
        ordered_visible = sorted(visible_indexes)
        selected_indexes = ordered_visible[offset : offset + limit]
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
            for index in selected_indexes
        ]
        return {
            "records": selected,
            "page": offset // limit + 1,
            "offset": offset,
            "limit": limit,
            "total": len(hit_indexes),
            "virtual_total": len(ordered_visible),
            "has_more": offset + limit < len(ordered_visible),
            "keyword": normalized_keyword,
            "context_lines": effective_context,
            "returned_count": len(selected),
        }

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
        "offset": start,
        "limit": limit,
        # For Find queries, total and pagination describe actual hits rather
        # than the variable number of expanded context rows returned per page.
        "total": len(hit_indexes),
        "has_more": start + limit < len(hit_indexes),
        "keyword": normalized_keyword,
        "context_lines": effective_context,
        "returned_count": len(selected),
    }


def _empty_query_result(
    page: int, limit: int, keyword: str, offset: int | None
) -> dict[str, object]:
    start = offset if offset is not None else (page - 1) * limit
    return {
        "records": [],
        "page": start // limit + 1,
        "offset": start,
        "limit": limit,
        "total": 0,
        "has_more": False,
        "keyword": keyword,
        "context_lines": 0,
        "returned_count": 0,
    }


def correlate_log_records(
    root_dir: Path | str,
    batch_id: str,
    target: str,
    sequence: int,
    window_seconds: int = 300,
) -> dict[str, object]:
    """Find rows in the same batch by time only, across every target."""

    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("sequence must be at least 1")
    if (
        isinstance(window_seconds, bool)
        or not isinstance(window_seconds, int)
        or not 1 <= window_seconds <= 86400
    ):
        raise ValueError("window_seconds must be between 1 and 86400")
    targets = list_log_targets(root_dir, batch_id)
    if target not in targets:
        raise ValueError("unknown target file")
    batch = _batch_dir(root_dir, batch_id)
    with sqlite3.connect(batch / "index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        selected = connection.execute(
            "SELECT * FROM records WHERE target = ? AND sequence = ?",
            (target, sequence),
        ).fetchone()
        if selected is None:
            raise ValueError("unknown log sequence")
        selected_clock = selected["clock_seconds"]
        if selected_clock is None:
            return {
                "target": target,
                "sequence": sequence,
                "window_seconds": window_seconds,
                "matches": [],
            }
        rows = connection.execute(
            "SELECT * FROM records WHERE clock_seconds IS NOT NULL "
            "ORDER BY target, sequence"
        ).fetchall()

    matches: list[dict[str, object]] = []
    for row in rows:
        if row["target"] == target and int(row["sequence"]) == sequence:
            continue
        distance = _row_time_distance(selected, row)
        if distance > window_seconds:
            continue
        matches.append(
            {
                "target": row["target"],
                "sequence": row["sequence"],
                "timestamp": _display_timestamp(row),
                "distance_seconds": distance,
            }
        )
    return {
        "target": target,
        "sequence": sequence,
        "window_seconds": window_seconds,
        "matches": matches,
    }


def export_log_records(
    root_dir: Path | str,
    batch_id: str,
    target: str,
    *,
    mode: str,
    slot_id: str = "",
    socket_id: str = "",
) -> tuple[str, str]:
    """Render a complete target from SQLite without Web display transforms."""

    if mode not in {"raw", "metadata"}:
        raise ValueError("export mode must be raw or metadata")
    targets = list_log_targets(root_dir, batch_id)
    if target not in targets:
        raise ValueError("unknown target file")
    batch = _batch_dir(root_dir, batch_id)
    with sqlite3.connect(batch / "index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(records)").fetchall()
        }
        clauses = ["target = ?"]
        parameters: list[object] = [target]
        for name, value in (("slot_id", slot_id.strip()), ("socket_id", socket_id.strip())):
            if not value:
                continue
            if name not in columns:
                return f"{Path(target).stem}-{mode}.log", ""
            clauses.append(f"{name} = ?")
            parameters.append(value)
        rows = connection.execute(
            f"SELECT * FROM records WHERE {' AND '.join(clauses)} ORDER BY sequence",
            parameters,
        ).fetchall()

    lines: list[str] = []
    for row in rows:
        text = str(row["text"])
        if mode == "raw":
            lines.append(text)
            continue
        slot = row["slot_id"] if "slot_id" in columns and row["slot_id"] is not None else "?"
        socket = (
            row["socket_id"]
            if "socket_id" in columns and row["socket_id"] is not None
            else "?"
        )
        lines.append(
            f"[{_display_timestamp(row)}] [slot_id={slot} socket_id={socket}] {text}"
        )
    content = "\n".join(lines) + ("\n" if lines else "")
    return f"{Path(target).stem}-{mode}.log", content


def _row_time_distance(left: sqlite3.Row, right: sqlite3.Row) -> int:
    left_clock = int(left["clock_seconds"])
    right_clock = int(right["clock_seconds"])
    left_date = left["date_key"]
    right_date = right["date_key"]
    if left_date and right_date:
        left_value = datetime.combine(date_type.fromisoformat(str(left_date)), time_type())
        right_value = datetime.combine(date_type.fromisoformat(str(right_date)), time_type())
        return int(abs((left_value - right_value).total_seconds() + left_clock - right_clock))
    return _clock_distance(left_clock, right_clock)


def _record_dict(row: sqlite3.Row, *, find_role: str) -> dict[str, object]:
    """Serialize one indexed row with its Web Find presentation role."""

    columns = set(row.keys())
    matched_spans: list[list[int]] = []
    if "matched_spans" in columns:
        try:
            value = json.loads(str(row["matched_spans"]))
            if isinstance(value, list):
                matched_spans = value
        except json.JSONDecodeError:
            matched_spans = []
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
        "slot_id": row["slot_id"] if "slot_id" in columns else None,
        "socket_id": row["socket_id"] if "socket_id" in columns else None,
        "slot_id_source": (
            row["slot_id_source"] if "slot_id_source" in columns else "unknown"
        ),
        "socket_id_source": (
            row["socket_id_source"] if "socket_id_source" in columns else "unknown"
        ),
        "matched_spans": matched_spans,
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
