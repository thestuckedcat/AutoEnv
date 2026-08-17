from __future__ import annotations

import json
import sqlite3
from datetime import date as date_type, datetime, time as time_type
from pathlib import Path


def list_log_batches(root_dir: Path | str) -> list[dict[str, object]]:
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
) -> dict[str, object]:
    if page < 1:
        raise ValueError("page must be at least 1")
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    if not 1 <= window_minutes <= 24 * 60:
        raise ValueError("window_minutes must be between 1 and 1440")
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
    with sqlite3.connect(batch / "index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM records WHERE target = ? ORDER BY sequence", (target,)
        ).fetchall()
    selected: list[dict[str, object]] = []
    for row in rows:
        if folded_keyword and folded_keyword not in str(row["text"]).casefold():
            continue
        clock = row["clock_seconds"]
        row_date = row["date_key"]
        if center is not None:
            if clock is None:
                continue
            if wanted_date_value is not None and row_date:
                row_date_value = date_type.fromisoformat(str(row_date))
                row_value = datetime.combine(row_date_value, time_type())
                center_value = datetime.combine(wanted_date_value, time_type())
                distance = abs(
                    (row_value - center_value).total_seconds() + int(clock) - center
                )
            else:
                distance = _clock_distance(int(clock), center)
            if distance > half_window:
                continue
        selected.append(
            {
                "id": row["id"],
                "sequence": row["sequence"],
                "text": row["text"],
                "source_file": row["source_file"],
                "source_line": row["source_line"],
                "timestamp": _display_timestamp(row),
                "clock_seconds": clock,
                "date_key": row_date,
                "timestamp_source": row["timestamp_source"],
                "incomplete_block": bool(row["incomplete_block"]),
            }
        )
    start = (page - 1) * limit
    return {
        "records": selected[start : start + limit],
        "page": page,
        "limit": limit,
        "total": len(selected),
        "has_more": start + limit < len(selected),
        "keyword": normalized_keyword,
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
    if row["clock_seconds"] is None:
        return "-"
    clock = f"{int(row['hour']):02d}:{int(row['minute']):02d}"
    if row["second"] is not None:
        clock += f":{int(row['second']):02d}"
    return f"{row['date_key']} {clock}" if row["date_key"] else clock
