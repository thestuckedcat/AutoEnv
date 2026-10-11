"""Deterministic segment assembly and bounded, parameterized SQLite queries."""

from __future__ import annotations

import hashlib
import itertools
import json
import sqlite3
import time
from pathlib import Path
from typing import Callable, Iterator

from ._storage import INDEX_SCHEMA_VERSION, RECORD_COLUMNS, add_diagnostic, connect, create_schema, register_functions
from .parser import check_cancelled, normalize_module_name, time_value
from .module_map import kernel_group_id, kernel_groups, load_module_mappings


GROUP_FIELDS = {
    "driver": ("slot_id", "cpu_id", "module_name"),
    "firmware": ("slot_id", "cpu_id", "device_id", "log_type"),
    "unclassified": (),
}
SELECT_RECORDS = (
    "SELECT r.*,s.path AS source_path,s.relative_path,s.origin_path,s.archive_path,s.member_path,"
    "COALESCE(d.anchor_value,r.time_value) AS dump_anchor,"
    "COALESCE(d.source_rank,r.source_rank) AS dump_rank,"
    "COALESCE(d.line_number,r.line_number) AS dump_line "
    "FROM records r JOIN sources s ON s.source_id=r.source_id "
    "LEFT JOIN dumps d ON d.dump_id=r.dump_id"
)
MAX_CACHED_QUERIES = 12
MAX_CACHED_HITS = 500_000
CACHE_TTL_SECONDS = 3600


def _cached_matches(batch_dir: str | Path, where: str, parameters: list, order: str,
                    offset: int, limit: int) -> tuple[int, list[int]] | None:
    """Cache ordered matching IDs on disk, bounded by queries and hit count.

    A large query exceeding the capacity falls back to a bounded-memory SQL
    scan. The cache does not mutate the published index and contains no log text.
    """
    root = Path(batch_dir).resolve()
    fingerprint = hashlib.sha256(json.dumps([where, parameters, order], ensure_ascii=False).encode()).hexdigest()
    try:
        cache = sqlite3.connect(root / "query-cache.sqlite3", timeout=30, uri=True)
    except sqlite3.OperationalError:
        return None
    cache.row_factory = sqlite3.Row
    register_functions(cache)
    try:
        cache.executescript(
            "CREATE TABLE IF NOT EXISTS queries(key TEXT PRIMARY KEY,total INTEGER NOT NULL,used REAL NOT NULL);"
            "CREATE TABLE IF NOT EXISTS hits(key TEXT NOT NULL,position INTEGER NOT NULL,id INTEGER NOT NULL,PRIMARY KEY(key,position));"
        )
        cache.execute("ATTACH DATABASE ? AS indexed", ((root / "index.sqlite3").as_uri() + "?mode=ro",))
        cache.execute("BEGIN IMMEDIATE")
        now = time.time()
        cache.execute("DELETE FROM hits WHERE key IN(SELECT key FROM queries WHERE used<?)", (now - CACHE_TTL_SECONDS,))
        cache.execute("DELETE FROM queries WHERE used<?", (now - CACHE_TTL_SECONDS,))
        previous = cache.execute("SELECT total FROM queries WHERE key=?", (fingerprint,)).fetchone()
        if previous is None:
            # Capacity is enforced before insertion. One over-capacity marker can
            # be cached, but its matching IDs are discarded.
            existing = cache.execute("SELECT key FROM queries ORDER BY used DESC").fetchall()
            for old in existing[MAX_CACHED_QUERIES - 1:]:
                cache.execute("DELETE FROM hits WHERE key=?", (old[0],))
                cache.execute("DELETE FROM queries WHERE key=?", (old[0],))
            source_select = SELECT_RECORDS.replace("FROM records r", "FROM indexed.records r").replace(
                "JOIN sources s", "JOIN indexed.sources s").replace("JOIN dumps d", "JOIN indexed.dumps d")
            safe_where = where.replace("FROM segments sg", "FROM indexed.segments sg")
            row_order = order.replace("r.", "")
            cache.execute(
                "INSERT INTO hits(key,position,id) SELECT ?,ROW_NUMBER() OVER(" + row_order + "),id FROM (" +
                source_select + " WHERE " + safe_where + ") LIMIT ?",
                [fingerprint] + parameters + [MAX_CACHED_HITS + 1],
            )
            total = cache.execute("SELECT COUNT(*) FROM hits WHERE key=?", (fingerprint,)).fetchone()[0]
            if total > MAX_CACHED_HITS:
                cache.execute("DELETE FROM hits WHERE key=?", (fingerprint,))
                total = -1
            cache.execute("INSERT INTO queries(key,total,used) VALUES(?,?,?)", (fingerprint, total, now))
        else:
            total = previous[0]
            cache.execute("UPDATE queries SET used=? WHERE key=?", (now, fingerprint))
        ids = [row[0] for row in cache.execute("SELECT id FROM hits WHERE key=? ORDER BY position LIMIT ? OFFSET ?",
                                              (fingerprint, limit, offset))] if total >= 0 else []
        cache.commit()
        return (total, ids) if total >= 0 else None
    except sqlite3.OperationalError:
        # Read-only workspaces, cache contention or unsupported window functions
        # retain the original query semantics; caching is an optimization.
        cache.rollback()
        return None
    finally:
        cache.close()


def build_index(batch_dir: Path, shards: list[Path], *,
                cancelled: Callable[[], bool] | None = None,
                progress: Callable[[dict], None] | None = None) -> dict:
    database_path = batch_dir / "index.sqlite3"
    database = connect(database_path)
    create_schema(database)
    try:
        # The list is in immutable source order, independent of worker completion.
        for rank, shard in enumerate(shards):
            check_cancelled(cancelled)
            database.execute("ATTACH DATABASE ? AS worker", (str(shard),))
            for table in ("sources", "segments"):
                database.execute(f"INSERT INTO {table} SELECT * FROM worker.{table}")
            columns = ",".join(RECORD_COLUMNS)
            database.execute(f"INSERT INTO records({columns}) SELECT {columns} FROM worker.records ORDER BY id")
            database.execute(
                "INSERT INTO diagnostics(source_id,line_number,code,message,dump_id) "
                "SELECT source_id,line_number,code,message,dump_id FROM worker.diagnostics ORDER BY id"
            )
            database.commit()
            database.execute("DETACH DATABASE worker")
            if progress:
                progress({"stage": "indexing", "count": rank + 1, "message": "Merged source shard"})
        database.executescript(
            "CREATE INDEX segment_source ON segments(source_rank,line_number);"
            "CREATE INDEX segment_dump ON segments(dump_id,seg);"
            "CREATE INDEX record_segment ON records(segment_id);"
        )
        active: dict[str, tuple[str, int]] = {}
        segments = database.execute("SELECT * FROM segments ORDER BY source_rank,line_number")
        for segment in segments:
            check_cancelled(cancelled)
            stream = json.dumps([segment[key] for key in (
                "device_source", "host", "service", "slot_id", "cpu_id", "module_name", "device_id", "log_type",
            )], ensure_ascii=False, separators=(",", ":"))
            if segment["seg"] == 0 or stream not in active:
                dump_id = "dump-" + hashlib.sha256((stream + ":" + segment["segment_id"]).encode()).hexdigest()[:20]
                database.execute(
                    "INSERT INTO dumps(dump_id,stream_key,anchor_time,anchor_value,first_value,source_rank,line_number,missing_start) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (dump_id, stream, segment["time"], segment["time_value"], segment["time_value"],
                     segment["source_rank"], segment["line_number"], int(segment["seg"] != 0)),
                )
                if segment["seg"] != 0:
                    add_diagnostic(database, segment["source_id"], segment["line_number"], "missing_start",
                                   f"Partial firmware dump starts at seg[{segment['seg']}]; seg[0] was not found", dump_id)
            else:
                dump_id, previous_time = active[stream]
                if database.execute("SELECT 1 FROM segments WHERE dump_id=? AND seg=? LIMIT 1",
                                    (dump_id, segment["seg"])).fetchone():
                    add_diagnostic(database, segment["source_id"], segment["line_number"], "duplicate_segment",
                                   f"Repeated seg[{segment['seg']}] retained", dump_id)
                if segment["time_value"] < previous_time:
                    add_diagnostic(database, segment["source_id"], segment["line_number"], "segment_time_reversed",
                                   "Segment read time goes backwards in source order; real times retained", dump_id)
            active[stream] = (dump_id, segment["time_value"])
            database.execute("UPDATE segments SET dump_id=? WHERE segment_id=?", (dump_id, segment["segment_id"]))
            database.execute("UPDATE records SET dump_id=? WHERE segment_id=?", (dump_id, segment["segment_id"]))
            database.execute("UPDATE dumps SET first_value=MIN(first_value,?) WHERE dump_id=?", (segment["time_value"], dump_id))
        # Missing ranges are represented as ranges, never expanded into huge lists.
        previous_dump = None
        previous_seg = -1
        for item in database.execute("SELECT dump_id,seg,MIN(source_id) AS source_id,MIN(line_number) AS line_number "
                                     "FROM segments GROUP BY dump_id,seg ORDER BY dump_id,seg"):
            check_cancelled(cancelled)
            if item["dump_id"] != previous_dump:
                previous_dump, previous_seg = item["dump_id"], -1
            if previous_seg >= 0 and item["seg"] > previous_seg + 1:
                add_diagnostic(database, item["source_id"], item["line_number"], "missing_segments",
                               f"Missing seg[{previous_seg + 1}..{item['seg'] - 1}]", item["dump_id"])
            previous_seg = item["seg"]
        database.executescript(
            "CREATE INDEX record_time ON records(kind,time_value,source_rank,line_number,id);"
            "CREATE INDEX record_driver ON records(kind,driver_domain,slot_id,cpu_id,module_name,time_value);"
            "CREATE INDEX record_firmware ON records(kind,slot_id,cpu_id,device_id,log_type,time_value);"
            "CREATE INDEX record_dump ON records(dump_id,seg,source_rank,line_number,id);"
            "CREATE INDEX record_source ON records(source_id,line_number,id);"
            "CREATE TABLE groups(group_id TEXT PRIMARY KEY,kind TEXT NOT NULL,slot_id TEXT NOT NULL,"
            "cpu_id TEXT NOT NULL,module_name TEXT NOT NULL,device_id TEXT NOT NULL,log_type TEXT NOT NULL,"
            "count INTEGER NOT NULL,dump_count INTEGER NOT NULL,source_count INTEGER NOT NULL,module_names TEXT NOT NULL,"
            "driver_domain TEXT NOT NULL DEFAULT '');"
        )
        for kind, fields in GROUP_FIELDS.items():
            names = ",".join(fields)
            grouping = f" GROUP BY {names}" if fields else ""
            selected = f"{names}," if fields else ""
            if kind == "firmware":
                # Headers identify a group even when a dump has no body lines.
                selected = ",".join(f"g.{field} AS {field}" for field in fields)
                grouping = ",".join(f"g.{field}" for field in fields)
                rows = database.execute(
                    f"SELECT {selected},COUNT(r.id) AS count,COUNT(DISTINCT g.dump_id) AS dump_count,"
                    "COUNT(DISTINCT g.source_id) AS source_count FROM segments g LEFT JOIN records r "
                    f"ON r.segment_id=g.segment_id GROUP BY {grouping}"
                )
            else:
                domain_where = " AND driver_domain='user'" if kind == "driver" else ""
                rows = database.execute(
                    f"SELECT {selected}COUNT(*) AS count,COUNT(DISTINCT dump_id) AS dump_count,"
                    f"COUNT(DISTINCT source_id) AS source_count FROM records WHERE kind=?{domain_where}{grouping}", (kind,),
                )
            for group in rows:
                if not group["count"] and kind != "firmware":
                    continue
                identity = [kind] + [group[field] for field in fields]
                group_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
                values = {field: "" for field in ("slot_id", "cpu_id", "module_name", "device_id", "log_type")}
                values.update({field: group[field] for field in fields})
                where = " AND ".join(f"{field}=?" for field in fields)
                if kind == "firmware":
                    modules = [item[0] for item in database.execute(
                        "SELECT DISTINCT module_name FROM segments WHERE " + where + " ORDER BY module_name",
                        [group[field] for field in fields],
                    )]
                else:
                    modules = [item[0] for item in database.execute(
                        "SELECT DISTINCT module_name FROM records WHERE kind=?" + (" AND " + where if where else "") +
                        " ORDER BY module_name", [kind] + [group[field] for field in fields],
                    )]
                if len(modules) == 1:
                    values["module_name"] = modules[0]
                database.execute("INSERT INTO groups VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                    group_id, kind, *(values[field] for field in ("slot_id", "cpu_id", "module_name", "device_id", "log_type")),
                    group["count"], group["dump_count"], group["source_count"], json.dumps(modules, ensure_ascii=False),
                    "user" if kind == "driver" else "",
                ))
        database.commit()
        counts = {"files": database.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                  "driver_records": 0, "firmware_records": 0, "unclassified_records": 0,
                  "dumps": database.execute("SELECT COUNT(*) FROM dumps").fetchone()[0],
                  "diagnostics": database.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]}
        for row in database.execute("SELECT kind,COUNT(*) FROM records GROUP BY kind"):
            counts[row[0] + "_records"] = row[1]
        counts["user_records"] = database.execute("SELECT COUNT(*) FROM records WHERE driver_domain='user'").fetchone()[0]
        counts["kernel_records"] = database.execute("SELECT COUNT(*) FROM records WHERE driver_domain='kernel'").fetchone()[0]
        return counts
    finally:
        database.close()


def _open_ready(batch_dir: str | Path) -> sqlite3.Connection:
    root = Path(batch_dir).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Product-log manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "ready":
        raise ValueError("Product-log batch is not ready")
    if manifest.get("index_schema_version") != INDEX_SCHEMA_VERSION or manifest.get("time_unit") != "microseconds":
        raise ValueError("Product-log index format changed; import the source again")
    database = sqlite3.connect((root / "index.sqlite3").as_uri() + "?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    register_functions(database)
    database.execute("PRAGMA query_only=ON")
    return database


def _page(offset: int, limit: int) -> tuple[int, int]:
    if isinstance(offset, bool) or isinstance(limit, bool):
        raise ValueError("Invalid pagination")
    offset, limit = int(offset), int(limit)
    if offset < 0 or not 1 <= limit <= 500:
        raise ValueError("offset must be nonnegative; limit must be 1..500")
    return offset, limit


def firmware_type_name(value: str) -> str:
    return {"0": "bios", "1": "IMP", "2": "IMU", "3": "HSM"}.get(str(value), str(value))


def list_groups(batch_dir: str | Path, kind: str, driver_domain: str = "") -> list[dict]:
    if kind not in (*GROUP_FIELDS, "all"):
        raise ValueError("kind must be driver, firmware, unclassified or all")
    if driver_domain not in ("", "user", "kernel"):
        raise ValueError("driver_domain must be empty, user or kernel")
    database = _open_ready(batch_dir)
    try:
        result = []
        where, values = ("kind IN ('driver','firmware')", []) if kind == "all" else ("kind=?", [kind])
        if driver_domain:
            where += " AND (kind<>'driver' OR driver_domain=?)"
            values.append(driver_domain)
        for row in database.execute("SELECT * FROM groups WHERE " + where + " ORDER BY slot_id,cpu_id,device_id,log_type,module_name", values):
            item = dict(row)
            item["module_names"] = json.loads(item["module_names"])
            if item["kind"] == "firmware":
                item["log_type_name"] = firmware_type_name(item["log_type"])
            result.append(item)
        if kind in ("driver", "all") and driver_domain != "user":
            result.extend(kernel_groups(database, load_module_mappings(batch_dir, database)))
        return result
    finally:
        database.close()


def _filters(database: sqlite3.Connection, *, batch_dir: str | Path, kind: str, group_id: str | None = None,
             slot_id: str = "", cpu_id: str = "", module_name: str = "", device_id: str = "", log_type: str = "",
             driver_domain: str = "", module: str = "", keyword: str = "", raw_search: bool = False,
             start_time: str = "", end_time: str = "") -> tuple[str, list]:
    if kind not in (*GROUP_FIELDS, "all"):
        raise ValueError("kind must be driver, firmware, unclassified or all")
    if driver_domain not in ("", "user", "kernel"):
        raise ValueError("driver_domain must be empty, user or kernel")
    clauses, parameters = (["r.kind IN ('driver','firmware')"], []) if kind == "all" else (["r.kind=?"], [kind])
    mappings = load_module_mappings(batch_dir, database) if module or (group_id and group_id.startswith("kernel-")) else []
    if group_id:
        if group_id.startswith("kernel-"):
            mapped = next((row for row in mappings if kernel_group_id(row["module"]) == group_id and row["kernel_keyword"]), None)
            if kind not in ("driver", "all") or mapped is None:
                raise ValueError("Group does not belong to this batch and kind")
            clauses.append("r.kind='driver' AND r.driver_domain='kernel' AND instr(casefold(r.raw_text),?)>0")
            parameters.append(mapped["kernel_keyword"].casefold())
        else:
            group = database.execute("SELECT * FROM groups WHERE group_id=?", (group_id,)).fetchone()
            if group is None or (kind != "all" and group["kind"] != kind):
                raise ValueError("Group does not belong to this batch and kind")
            clauses.append("r.kind=?")
            parameters.append(group["kind"])
            if group["kind"] == "driver":
                clauses.append("r.driver_domain='user'")
            for field in GROUP_FIELDS[group["kind"]]:
                clauses.append(f"r.{field}=?")
                parameters.append(group[field])
    if driver_domain:
        clauses.append("(r.kind<>'driver' OR r.driver_domain=?)")
        parameters.append(driver_domain)
    for field, value in (("slot_id", slot_id), ("cpu_id", cpu_id), ("module_name", module_name),
                         ("device_id", device_id), ("log_type", log_type)):
        if value != "" and value is not None:
            if field in ("device_id", "log_type"):
                clauses.append(f"(r.kind<>'firmware' OR r.{field}=?)")
            elif field == "module_name":
                clauses.append("(r.driver_domain='kernel' OR (r.kind='firmware' AND r.module_name=?) "
                               "OR (r.kind<>'firmware' AND r.module_name=?))")
                parameters.extend([str(value), normalize_module_name(str(value))])
                continue
            else:
                clauses.append(f"(r.driver_domain='kernel' OR r.{field}=?)")
            parameters.append(str(value))
    if module and kind in ("driver", "all"):
        mapped = next((row for row in mappings if row["module"] == module), None)
        if mapped is None:
            raise ValueError("Module does not belong to this batch")
        clauses.append("(r.kind='firmware' OR (r.kind='driver' AND "
                       "((r.driver_domain='user' AND r.module_name=?) OR "
                       "(r.driver_domain='kernel' AND ?<>'' AND instr(casefold(r.raw_text),?)>0))))")
        parameters.extend([mapped["user_module_name"], mapped["kernel_keyword"], mapped["kernel_keyword"].casefold()])
    if keyword:
        if raw_search:
            clauses.append("(instr(r.raw_text,?)>0 OR EXISTS(SELECT 1 FROM segments sg "
                           "WHERE sg.segment_id=r.segment_id AND instr(sg.raw_header,?)>0))")
            parameters.extend([str(keyword), str(keyword)])
        else:
            clauses.append("instr(r.search_text,?)>0")
            parameters.append(str(keyword))
    for value, operator in ((start_time, ">="), (end_time, "<=")):
        if value:
            clauses.append(f"r.time_value{operator}?")
            parameters.append(time_value(str(value).replace("T", " ")))
    if start_time and end_time and time_value(str(start_time).replace("T", " ")) > time_value(str(end_time).replace("T", " ")):
        raise ValueError("start_time must not exceed end_time")
    return " AND ".join(clauses), parameters


def _order(view: str) -> str:
    if view == "time":
        return " ORDER BY r.time_value IS NULL,r.time_value,r.source_rank,r.line_number,r.id"
    if view == "stitched":
        return " ORDER BY dump_anchor IS NULL,dump_anchor,dump_rank,dump_line,r.seg,r.source_rank,r.line_number,r.id"
    raise ValueError("view must be time or stitched")


def _record(row: sqlite3.Row | dict, mode: str, is_context: bool = False, *, preserve_firmware_time: bool = False) -> dict:
    if mode not in ("concise", "original", "raw"):
        raise ValueError("mode must be concise or original")
    result = dict(row)
    if result["kind"] == "firmware":
        result["log_type_name"] = firmware_type_name(result["log_type"])
    if (mode == "raw" and not (preserve_firmware_time and result["kind"] == "firmware")) or (mode == "original" and result["kind"] != "firmware"):
        result["text"] = result["raw_text"]
    result["is_context"] = is_context
    for field in ("dump_anchor", "dump_rank", "dump_line", "search_text"):
        result.pop(field, None)
    return result


def query_records(batch_dir: str | Path, *, kind: str, group_id: str | None = None,
                  slot_id: str = "", cpu_id: str = "", module_name: str = "", device_id: str = "", log_type: str = "",
                  driver_domain: str = "", module: str = "",
                  keyword: str = "", raw_search: bool = False, start_time: str = "", end_time: str = "",
                  view: str = "time", offset: int = 0, limit: int = 200, mode: str = "concise",
                  context_lines: int = 0) -> dict:
    offset, limit = _page(offset, limit)
    context_lines = int(context_lines)
    if not 0 <= context_lines <= 10:
        raise ValueError("context_lines must be 0..10")
    if mode not in ("concise", "original", "raw"):
        raise ValueError("mode must be concise or original")
    if kind == "all" and view == "stitched":
        view = "time"
    database = _open_ready(batch_dir)
    try:
        filter_args = dict(batch_dir=batch_dir, kind=kind, group_id=group_id, slot_id=slot_id, cpu_id=cpu_id, module_name=module_name,
                           driver_domain=driver_domain, module=module,
                           device_id=device_id, log_type=log_type, keyword=keyword, raw_search=raw_search,
                           start_time=start_time, end_time=end_time)
        where, parameters = _filters(database, **filter_args)
        order = _order(view)
        cached = _cached_matches(batch_dir, where, parameters, order, offset, limit) if keyword else None
        if cached is None:
            total = database.execute("SELECT COUNT(*) FROM records r WHERE " + where, parameters).fetchone()[0]
            matches = list(database.execute(SELECT_RECORDS + " WHERE " + where + order + " LIMIT ? OFFSET ?", parameters + [limit, offset]))
        else:
            total, hit_ids = cached
            matches = list(database.execute(SELECT_RECORDS + " WHERE r.id IN (" + ",".join("?" for _ in hit_ids) + ")" + order,
                                            hit_ids)) if hit_ids else []
        records = matches
        hit_ids = {row["id"] for row in matches}
        if context_lines and keyword and matches:
            filter_args.update(keyword="", start_time="", end_time="")
            structural, values = _filters(database, **filter_args)
            contextual = {row["id"]: row for row in matches}
            for match in matches:
                for row in database.execute(SELECT_RECORDS + " WHERE " + structural +
                                            " AND r.source_id=? AND r.line_number BETWEEN ? AND ?",
                                            values + [match["source_id"], match["line_number"] - context_lines,
                                                      match["line_number"] + context_lines]):
                    contextual[row["id"]] = row
            if view == "time":
                key = lambda row: (row["time_value"] is None, row["time_value"] or 0, row["source_rank"], row["line_number"], row["id"])
            else:
                key = lambda row: (row["dump_anchor"] is None, row["dump_anchor"] or 0, row["dump_rank"], row["dump_line"],
                                   row["seg"] or 0, row["source_rank"], row["line_number"], row["id"])
            records = sorted(contextual.values(), key=key)
        return {"records": [_record(row, mode, row["id"] not in hit_ids, preserve_firmware_time=kind == "all") for row in records],
                "total": total, "has_more": offset + len(matches) < total, "offset": offset, "limit": limit}
    finally:
        database.close()


def get_source(batch_dir: str | Path, source_id: str, *, offset: int = 0, limit: int = 200) -> dict:
    offset, limit = _page(offset, limit)
    database = _open_ready(batch_dir)
    try:
        row = database.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if row is None:
            raise FileNotFoundError("Source is not registered in this batch")
        source = dict(row)
    finally:
        database.close()
    path = Path(source["path"])
    from autoenv.file_handles import _assert_no_links

    _assert_no_links(path)
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Registered source is no longer available")
    current_stat = path.stat()
    if (current_stat.st_size, current_stat.st_mtime_ns) != (source["size_bytes"], source["mtime_ns"]):
        raise ValueError("Registered source changed after analysis; re-import it before viewing original lines")
    lines = []
    with path.open("rb") as stream:
        for number, raw in itertools.islice(enumerate(stream, 1), offset, offset + limit):
            if number == 1:
                raw = raw.removeprefix(b"\xef\xbb\xbf")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gb18030", errors="replace")
            lines.append({"line_number": number, "text": text.rstrip("\r\n")})
    return {"source": source, "source_id": source_id, "source_path": str(path), "lines": lines, "records": lines,
            "total": source["line_count"], "has_more": offset + len(lines) < source["line_count"], "offset": offset, "limit": limit}


def list_diagnostics(batch_dir: str | Path, *, offset: int = 0, limit: int = 200) -> dict:
    offset, limit = _page(offset, limit)
    database = _open_ready(batch_dir)
    try:
        total = database.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0]
        records = [dict(row) for row in database.execute(
            "SELECT d.*,s.path AS source_path FROM diagnostics d LEFT JOIN sources s ON s.source_id=d.source_id "
            "ORDER BY d.id LIMIT ? OFFSET ?", (limit, offset),
        )]
        return {"records": records, "total": total, "has_more": offset + len(records) < total, "offset": offset, "limit": limit}
    finally:
        database.close()


def iter_export(batch_dir: str | Path, **query) -> Iterator[str]:
    """Hold one read cursor and fetch bounded chunks; never materialize a group."""
    mode = query.pop("mode", "concise")
    view = query.pop("view", "time")
    if query.get("kind") == "all" and view == "stitched":
        view = "time"
    for ignored in ("offset", "limit", "context_lines"):
        query.pop(ignored, None)
    if mode not in ("concise", "original", "raw"):
        raise ValueError("mode must be concise or original")
    database = _open_ready(batch_dir)
    try:
        where, parameters = _filters(database, batch_dir=batch_dir, **query)
        cursor = database.execute(SELECT_RECORDS + " WHERE " + where + _order(view), parameters)
        while rows := cursor.fetchmany(256):
            for row in rows:
                yield _record(row, mode, preserve_firmware_time=query.get("kind") == "all")["text"] + "\n"
    finally:
        database.close()
