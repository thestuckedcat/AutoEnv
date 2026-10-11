"""The on-disk contract shared by parser workers and the query layer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

INDEX_SCHEMA_VERSION = 2


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 source_id TEXT PRIMARY KEY, path TEXT NOT NULL, relative_path TEXT NOT NULL,
 origin_path TEXT NOT NULL, archive_path TEXT NOT NULL DEFAULT '',
 member_path TEXT NOT NULL DEFAULT '', source_rank INTEGER NOT NULL,
 log_domain TEXT NOT NULL DEFAULT 'user',
 encoding TEXT NOT NULL DEFAULT '', device_source TEXT NOT NULL DEFAULT '',
 line_count INTEGER NOT NULL DEFAULT 0, size_bytes INTEGER NOT NULL DEFAULT 0,
 mtime_ns INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS segments (
 segment_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, line_number INTEGER NOT NULL,
 time TEXT NOT NULL, time_value INTEGER NOT NULL,
 slot_id TEXT NOT NULL, cpu_id TEXT NOT NULL, module_name TEXT NOT NULL,
 device_id TEXT NOT NULL, log_type TEXT NOT NULL, seg INTEGER NOT NULL,
 host TEXT NOT NULL, service TEXT NOT NULL, device_source TEXT NOT NULL,
 dump_id TEXT, source_rank INTEGER NOT NULL, raw_header TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dumps (
 dump_id TEXT PRIMARY KEY, stream_key TEXT NOT NULL, anchor_time TEXT NOT NULL,
 anchor_value INTEGER NOT NULL, first_value INTEGER NOT NULL,
 source_rank INTEGER NOT NULL, line_number INTEGER NOT NULL,
 missing_start INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT NOT NULL,
 source_rank INTEGER NOT NULL, line_number INTEGER NOT NULL,
 time TEXT NOT NULL, time_value INTEGER, slot_id TEXT NOT NULL DEFAULT '',
 cpu_id TEXT NOT NULL DEFAULT '', module_name TEXT NOT NULL DEFAULT '',
 raw_module_name TEXT NOT NULL DEFAULT '', driver_domain TEXT NOT NULL DEFAULT '',
 device_id TEXT NOT NULL DEFAULT '', log_type TEXT NOT NULL DEFAULT '',
 dump_id TEXT, segment_id TEXT, seg INTEGER, firmware_time TEXT NOT NULL DEFAULT '',
 text TEXT NOT NULL, raw_text TEXT NOT NULL, search_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostics (
 id INTEGER PRIMARY KEY, source_id TEXT NOT NULL DEFAULT '',
 line_number INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL, message TEXT NOT NULL,
 dump_id TEXT
);
"""

RECORD_COLUMNS = (
    "kind", "source_id", "source_rank", "line_number", "time", "time_value",
    "slot_id", "cpu_id", "module_name", "raw_module_name", "driver_domain", "device_id", "log_type", "dump_id",
    "segment_id", "seg", "firmware_time", "text", "raw_text", "search_text",
)


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    register_functions(connection)
    return connection


def register_functions(connection: sqlite3.Connection) -> None:
    """SQLite's lower() only folds ASCII; mapping keywords support Unicode too."""
    connection.create_function("casefold", 1, lambda text: str(text or "").casefold(), deterministic=True)


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def add_diagnostic(connection: sqlite3.Connection, source_id: str, line: int,
                   code: str, message: str, dump_id: str | None = None) -> None:
    connection.execute(
        "INSERT INTO diagnostics(source_id,line_number,code,message,dump_id) VALUES(?,?,?,?,?)",
        (source_id, line, code, message, dump_id),
    )


def write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
