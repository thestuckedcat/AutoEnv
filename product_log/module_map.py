"""Per-batch business module mapping, applied to published records at query time."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .parser import normalize_module_name


DEFAULT_MODULE_MAPPINGS = (
    {"module": "udk", "user_module_name": "udk", "kernel_keyword": "udk"},
    {"module": "UBE", "user_module_name": "UBE", "kernel_keyword": "mami"},
)


def _validate_rows(rows: list[dict]) -> list[dict]:
    if not isinstance(rows, list):
        raise ValueError("Module mappings must be a list")
    result, names = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Every module mapping must be an object")
        values = {}
        for field in ("module", "user_module_name", "kernel_keyword"):
            value = row.get(field, "")
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError(f"Module mapping {field} must be text without NUL")
            values[field] = value.strip()
        if not values["module"] or values["module"] in names:
            raise ValueError("Module names must be nonempty and unique")
        values["user_module_name"] = normalize_module_name(values["user_module_name"])
        names.add(values["module"])
        result.append(values)
    return result


def load_module_mappings(batch_dir: str | Path, database: sqlite3.Connection) -> list[dict]:
    """Load the table and include newly discovered, otherwise unmapped names."""
    path = Path(batch_dir).resolve() / "module-map.json"
    rows = _validate_rows(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else [
        dict(row) for row in DEFAULT_MODULE_MAPPINGS]
    mapped = {row["user_module_name"] for row in rows}
    names = {row["module"] for row in rows}
    for row in database.execute(
        "SELECT DISTINCT module_name FROM records WHERE kind='driver' AND driver_domain='user' ORDER BY module_name"
    ):
        name = row[0]
        if not name or name in mapped:
            continue
        label = name
        suffix = 2
        while label in names:
            label = f"{name} ({suffix})"
            suffix += 1
        rows.append({"module": label, "user_module_name": name, "kernel_keyword": ""})
        mapped.add(name)
        names.add(label)
    return rows


def _counts(database: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    user_counts = dict(database.execute(
        "SELECT module_name,COUNT(*) FROM records WHERE kind='driver' AND driver_domain='user' GROUP BY module_name"
    ))
    result = []
    for row in rows:
        item = dict(row)
        item["user_count"] = user_counts.get(item["user_module_name"], 0)
        item["user_exists"] = bool(item["user_count"])
        item["kernel_configured"] = bool(item["kernel_keyword"])
        item["kernel_count"] = database.execute(
            "SELECT COUNT(*) FROM records WHERE kind='driver' AND driver_domain='kernel' "
            "AND instr(casefold(raw_text),?)>0", (item["kernel_keyword"].casefold(),)
        ).fetchone()[0] if item["kernel_configured"] else 0
        result.append(item)
    return result


def list_module_mappings(batch_dir: str | Path) -> list[dict]:
    from .index import _open_ready

    database = _open_ready(batch_dir)
    try:
        return _counts(database, load_module_mappings(batch_dir, database))
    finally:
        database.close()


def save_module_mappings(batch_dir: str | Path, rows: list[dict]) -> list[dict]:
    from .index import _open_ready

    validated = _validate_rows(rows)
    database = _open_ready(batch_dir)
    try:
        path = Path(batch_dir).resolve() / "module-map.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(validated, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return _counts(database, load_module_mappings(batch_dir, database))
    finally:
        database.close()


def kernel_group_id(module: str) -> str:
    return "kernel-" + hashlib.sha256(module.encode("utf-8")).hexdigest()[:24]


def kernel_groups(database: sqlite3.Connection, rows: list[dict]) -> list[dict]:
    groups = []
    for row in rows:
        keyword = row["kernel_keyword"]
        if not keyword:
            continue
        counts = database.execute(
            "SELECT COUNT(*) AS count,COUNT(DISTINCT source_id) AS source_count FROM records "
            "WHERE kind='driver' AND driver_domain='kernel' AND instr(casefold(raw_text),?)>0",
            (keyword.casefold(),),
        ).fetchone()
        if not counts["count"]:
            continue
        groups.append({"group_id": kernel_group_id(row["module"]), "kind": "driver", "driver_domain": "kernel",
                       "module": row["module"], "module_name": row["module"], "slot_id": "", "cpu_id": "",
                       "device_id": "", "log_type": "", "count": counts["count"], "dump_count": 0,
                       "source_count": counts["source_count"], "module_names": [row["module"]]})
    return groups
