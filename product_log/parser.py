"""Stream each source once into an isolated SQLite shard.

File workers never decide which dump a segment belongs to. That decision must
be made later in the deterministic file-name / source-line order.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from ._storage import RECORD_COLUMNS, add_diagnostic, connect, create_schema


from .configuration import normalize_config, normalize_module, record_enabled, compile_regex

EPOCH = datetime(1970, 1, 1)


def normalize_module_name(value: str, config: dict | None = None) -> str:
    return normalize_module(value, config)


def kernel_timestamp(line: str, profile: dict | None = None) -> tuple[str, int | None, str]:
    if profile is None:
        profile = next(p for p in normalize_config()["parsers"] if p["engine"] == "kernel")
    if profile["time_format"] == "none":
        return "", None, line
    match = compile_regex(profile["patterns"]["date"]).match(line)
    if match is None:
        return "", None, line
    try:
        if profile["time_format"] == "iso":
            stamp = compile_regex(r"\s+").sub(" ", match.group(1))
        else:
            months = [month.casefold() for month in profile["months"]]
            month = months.index(match.group(1).casefold()) + 1
            stamp = f"{match.group(5)}-{month:02d}-{int(match.group(2)):02d} {match.group(3)}"
            if match.group(4):
                stamp += "." + match.group(4)
        value = time_value(stamp)
    except (ValueError, IndexError, AttributeError, TypeError):
        return "", None, line
    return stamp, value, line[match.end():]


class AnalysisCancelled(RuntimeError):
    """A user cancelled a local analysis; its database is not published."""


def check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise AnalysisCancelled("product-log analysis cancelled")


def time_value(value: str) -> int:
    """Naive device-local dates, represented as exact integer microseconds."""
    normalized = compile_regex(r"\s+").sub(" ", value.strip())
    parsed = datetime.fromisoformat(normalized)
    delta = parsed - EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _field(pattern: re.Pattern, line: str) -> str:
    match = pattern.search(line)
    return (match.group(1) or "") if match else ""


def _unpack_info(line: str, pattern: re.Pattern) -> str:
    match = pattern.search(line)
    if not match:
        return ""
    body = match.group(1) or ""
    # Remove exactly the outer metadata wrapper, leaving Info's parentheses intact.
    if "(" in line[:match.start()] and body.endswith(")"):
        body = body[:-1]
    return body


def parse_file(path: Path, shard_path: Path, source: dict,
               cancelled: Callable[[], bool] | None = None) -> dict:
    check_cancelled(cancelled)
    log_domain = source.get("log_domain", "user")
    if log_domain not in ("user", "kernel"):
        raise ValueError("source log_domain must be user or kernel")
    config = source.get("analysis_config")
    config = normalize_config(config)
    engine = "kernel" if log_domain == "kernel" else "diag"
    profile = source.get("parser_profile") or next((p for p in config["parsers"] if p["engine"] == engine), None)
    if profile is None:
        raise ValueError("Source has no configured parser profile")
    log_domain = "kernel" if profile["engine"] == "kernel" else "user"
    configured_ids = source.get("configured_source_ids")
    if configured_ids is None:
        parser_ids = {p["id"] for p in config["parsers"] if p["engine"] == profile["engine"]}
        configured_ids = [item["id"] for item in config["sources"] if item["parser_id"] in parser_ids]
    patterns = {name: compile_regex(pattern, name not in {"header", "outer_shape", "date", "uptime"})
                for name, pattern in profile["patterns"].items()}
    database = connect(shard_path)
    create_schema(database)
    initial_stat = path.stat()
    source_id = source["source_id"]
    source_rank = source["source_rank"]
    database.execute(
        "INSERT INTO sources(source_id,path,relative_path,origin_path,archive_path,member_path,"
        "source_rank,device_source,log_domain) VALUES(?,?,?,?,?,?,?,?,?)",
        tuple(source[name] for name in ("source_id", "path", "relative_path", "origin_path",
                                       "archive_path", "member_path", "source_rank", "device_source")) + (log_domain,),
    )
    placeholders = ",".join("?" for _ in RECORD_COLUMNS)
    insert_record = f"INSERT INTO records({','.join(RECORD_COLUMNS)}) VALUES({placeholders})"
    current: dict | None = None
    encoding = "utf-8"
    counts = {"driver": 0, "firmware": 0, "unclassified": 0}
    number = 0
    pending_rows = []
    pending_raw = []

    def record(kind: str, raw: str, number: int, *, metadata: dict | None = None,
               body: str | None = None, accepted: bool = False) -> None:
        values = dict(metadata or {})
        content = raw if body is None else body
        category = "firmware" if kind == "firmware" else ("kernel_driver" if values.get("driver_domain") == "kernel" else "user_driver")
        if kind == "unclassified" and "analysis_config" in source:
            # Unsupported content stays available in its original source and diagnostics.
            return
        if kind != "unclassified" and not accepted and not record_enabled(config, configured_ids, category, values, raw, content):
            return
        stamp = values.get("time", "")
        if kind == "firmware":
            shown = f"[{stamp}]{content}"
        elif kind == "driver" and values.get("driver_domain") == "kernel":
            shown = f"{stamp or '[时间未知]'} {content}"
        elif kind == "driver":
            shown = f"{stamp} Info={content}"
        else:
            shown = raw
        uptime = patterns["uptime"].match(content) if kind == "firmware" else None
        values.update(kind=kind, source_id=source_id, source_rank=source_rank,
                      line_number=number, text=shown, raw_text=raw, search_text=content,
                      firmware_time=uptime.group(1) if uptime else "", dump_id=None)
        defaults = {name: "" for name in RECORD_COLUMNS}
        defaults.update(time_value=None, seg=None, segment_id=None, dump_id=None)
        defaults.update(values)
        database.execute(insert_record, tuple(defaults[name] for name in RECORD_COLUMNS))
        counts[kind] += 1

    def flush_segment() -> None:
        if current is None:
            return
        if not record_enabled(config, configured_ids, "firmware", current,
                              "\n".join(pending_raw), "\n".join(row[2] for row in pending_rows)):
            return
        database.execute(
            "INSERT INTO segments(segment_id,source_id,line_number,time,time_value,slot_id,cpu_id,"
            "module_name,device_id,log_type,seg,host,service,device_source,source_rank,raw_header) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (current["segment_id"], source_id, current["header_number"], current["time"], current["time_value"],
             current["slot_id"], current["cpu_id"], current["module_name"], current["device_id"], current["log_type"],
             current["seg"], current["host"], current["service"], source["device_source"], source_rank, current["raw_header"]),
        )
        for raw, line_number, body in pending_rows:
            record("firmware", raw, line_number, metadata=current, body=body, accepted=True)

    try:
        with path.open("rb") as stream:
            sample = stream.read(8192)
            stream.seek(0)
            if b"\x00" in sample:
                encoding = "binary"
                add_diagnostic(database, source_id, 0, "binary_file", "NUL bytes: source is not a supported text log")
            else:
                for number, raw_bytes in enumerate(stream, 1):
                    if number % 256 == 1:
                        check_cancelled(cancelled)
                    if number == 1 and raw_bytes.startswith(b"\xef\xbb\xbf"):
                        raw_bytes = raw_bytes[3:]
                        encoding = "utf-8-sig"
                    try:
                        line = raw_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        try:
                            line = raw_bytes.decode("gb18030")
                            encoding = "gb18030"
                        except UnicodeDecodeError:
                            line = raw_bytes.decode("utf-8", errors="replace")
                            encoding = "mixed/replacement"
                            add_diagnostic(database, source_id, number, "decode_error",
                                           "UTF-8 and GB18030 decoding failed; replacement text kept, original bytes retained in source")
                    line = line.rstrip("\r\n")
                    if log_domain == "kernel":
                        if line.strip():
                            stamp, timestamp, body = kernel_timestamp(line, profile)
                            record("driver", line, number,
                                   metadata=dict(time=stamp, time_value=timestamp, driver_domain="kernel"), body=body)
                        if number % 2000 == 0:
                            database.commit()
                        continue
                    header = patterns["header"].match(line)
                    if header or patterns["outer_shape"].match(line):
                        flush_segment()
                        current = None
                        pending_rows = []
                        pending_raw = []
                        if header is None:
                            add_diagnostic(database, source_id, number, "invalid_time", "Invalid outer record date/time")
                            record("unclassified", line, number)
                            continue
                        stamp = compile_regex(r"\s+").sub(" ", header.group(1) or "") if profile["time_format"] != "none" else ""
                        try:
                            timestamp = time_value(stamp) if profile["time_format"] != "none" else None
                        except ValueError:
                            add_diagnostic(database, source_id, number, "invalid_time", "Invalid outer record date/time")
                            record("unclassified", line, number)
                            continue
                        raw_module = compile_regex(r"^\s+|\s+$").sub("", _field(patterns["module"], line))
                        metadata = dict(time=stamp, time_value=timestamp, slot_id=_field(patterns["slot"], line),
                                        cpu_id=_field(patterns["cpu"], line), module_name=raw_module,
                                        raw_module_name=raw_module)
                        if not all(metadata[name] for name in ("slot_id", "cpu_id", "module_name")):
                            add_diagnostic(database, source_id, number, "missing_fields", "Outer record missing SlotId/CpuId/ModuleName")
                            record("unclassified", line, number, metadata=metadata)
                            continue
                        firmware = patterns["firmware"].search(line)
                        if firmware:
                            number_text = firmware.group(3) or ""
                            if (not firmware.group(1) or not firmware.group(2) or not re.fullmatch(r"[0-9]+", number_text)
                                    or len(number_text) > 10 or int(number_text) > 2_147_483_647):
                                add_diagnostic(database, source_id, number, "invalid_segment", "Firmware seg number is outside supported integer range")
                                record("unclassified", line, number, metadata=metadata)
                                continue
                            current = dict(metadata, device_id=firmware.group(1), log_type=firmware.group(2),
                                           seg=int(number_text), segment_id=f"{source_id}:{number}")
                            current.update(host=header.group(2) or "", service=_field(patterns["service"], line),
                                           header_number=number, raw_header=line)
                            pending_raw = [line]
                            inline = firmware.group(4) or ""
                            if inline:
                                if "(" in line[:firmware.start()] and inline.endswith(")"):
                                    inline = inline[:-1]
                            if inline:
                                pending_rows.append((line, number, inline))
                        elif patterns["firmware_marker"].search(line):
                            add_diagnostic(database, source_id, number, "invalid_segment", "Malformed firmware Unit/logType/seg metadata")
                            record("unclassified", line, number, metadata=metadata)
                        else:
                            info = _unpack_info(line, patterns["info"])
                            if patterns["info"].search(line) is None:
                                add_diagnostic(database, source_id, number, "missing_info", "Driver record missing Info; full source retained")
                                info = line
                            record("driver", line, number, metadata=dict(metadata, driver_domain="user",
                                   module_name=normalize_module_name(raw_module, config)), body=info)
                    elif current is not None:
                        pending_raw.append(line)
                        pending_rows.append((line, number, line))
                    elif line.strip():
                        add_diagnostic(database, source_id, number, "orphan_line", "Line outside a recognized driver/firmware record")
                        record("unclassified", line, number)
                    if number % 2000 == 0:
                        database.commit()
        flush_segment()
        check_cancelled(cancelled)
        final_stat = path.stat()
        if (initial_stat.st_size, initial_stat.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns):
            raise ValueError(f"Source file changed during parsing: {path}")
        database.execute("UPDATE sources SET encoding=?,line_count=?,size_bytes=?,mtime_ns=? WHERE source_id=?",
                         (encoding, number, initial_stat.st_size, initial_stat.st_mtime_ns, source_id))
        database.commit()
        return {"source_id": source_id, "encoding": encoding, "counts": counts}
    finally:
        database.close()
