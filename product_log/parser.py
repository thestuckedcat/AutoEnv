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


DATE = r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
HEADER = re.compile(rf"^(?P<time>{DATE})\s+(?P<host>\S+)\s+%%[^\r\n]+")
OUTER_SHAPE = re.compile(r"^\S+\s+\S+\s+\S+\s+%%")
SLOT = re.compile(r"\bSlotId\s*=\s*(\d+)", re.I)
CPU = re.compile(r"\bCpuId\s*=\s*(\d+)", re.I)
MODULE = re.compile(r"\bModuleName\s*=\s*([^,\r\n)]+)", re.I)
SERVICE = re.compile(r"\bService\s*=\s*([^;\s,]+)", re.I)
INFO = re.compile(r"\bInfo\s*=\s*(.*)", re.I)
FIRMWARE = re.compile(
    r"Unit\s*\[\s*(\d+)\s*\]\s+firmware\s+logType\s*\[\s*(\d+)\s*\]"
    r"\s+seg\s*\[\s*(\d+)\s*\]\s*:\s*(.*)", re.I,
)
UPTIME = re.compile(r"^\[(\d+(?:\.\d+)?)\]")
EPOCH = datetime(1970, 1, 1)
KERNEL_DATE = re.compile(
    r"^\[(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?P<month>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+"
    r"(?P<clock>\d{2}:\d{2}:\d{2})(?:\.(?P<fraction>\d{1,6}))?\s+"
    r"(?P<year>\d{4})\]\s*"
)
MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}


def normalize_module_name(value: str) -> str:
    """Only the udk family has aliases; preserve all other component names."""
    value = value.strip()
    return "udk" if re.match(r"^udk(?:\s|$)", value, re.I) else value


def kernel_timestamp(line: str) -> tuple[str, int | None, str]:
    match = KERNEL_DATE.match(line)
    if match is None:
        return "", None, line
    try:
        month = MONTHS[match.group("month").title()]
        stamp = f"{match.group('year')}-{month:02d}-{int(match.group('day')):02d} {match.group('clock')}"
        if match.group("fraction"):
            stamp += "." + match.group("fraction")
        value = time_value(stamp)
    except (KeyError, ValueError):
        return "", None, line
    return stamp, value, line[match.end():]


class AnalysisCancelled(RuntimeError):
    """A user cancelled a local analysis; its database is not published."""


def check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled and cancelled():
        raise AnalysisCancelled("product-log analysis cancelled")


def time_value(value: str) -> int:
    """Naive device-local dates, represented as exact integer microseconds."""
    normalized = " ".join(value.split())
    parsed = datetime.fromisoformat(normalized)
    delta = parsed - EPOCH
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _field(pattern: re.Pattern, line: str) -> str:
    match = pattern.search(line)
    return match.group(1) if match else ""


def _unpack_info(line: str) -> str:
    match = INFO.search(line)
    if not match:
        return ""
    body = match.group(1)
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

    def record(kind: str, raw: str, number: int, *, metadata: dict | None = None,
               body: str | None = None) -> None:
        values = dict(metadata or {})
        content = raw if body is None else body
        stamp = values.get("time", "")
        if kind == "firmware":
            shown = f"[{stamp}]{content}"
        elif kind == "driver" and values.get("driver_domain") == "kernel":
            shown = f"{stamp or '[时间未知]'} {content}"
        elif kind == "driver":
            shown = f"{stamp} Info={content}"
        else:
            shown = raw
        uptime = UPTIME.match(content) if kind == "firmware" else None
        values.update(kind=kind, source_id=source_id, source_rank=source_rank,
                      line_number=number, text=shown, raw_text=raw, search_text=content,
                      firmware_time=uptime.group(1) if uptime else "", dump_id=None)
        defaults = {name: "" for name in RECORD_COLUMNS}
        defaults.update(time_value=None, seg=None, segment_id=None, dump_id=None)
        defaults.update(values)
        database.execute(insert_record, tuple(defaults[name] for name in RECORD_COLUMNS))
        counts[kind] += 1

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
                            stamp, timestamp, body = kernel_timestamp(line)
                            record("driver", line, number,
                                   metadata=dict(time=stamp, time_value=timestamp, driver_domain="kernel"), body=body)
                        if number % 2000 == 0:
                            database.commit()
                        continue
                    header = HEADER.match(line)
                    if header or OUTER_SHAPE.match(line):
                        current = None
                        if header is None:
                            add_diagnostic(database, source_id, number, "invalid_time", "Invalid outer record date/time")
                            record("unclassified", line, number)
                            continue
                        stamp = " ".join(header.group("time").split())
                        try:
                            timestamp = time_value(stamp)
                        except ValueError:
                            add_diagnostic(database, source_id, number, "invalid_time", "Invalid outer record date/time")
                            record("unclassified", line, number)
                            continue
                        raw_module = _field(MODULE, line).strip()
                        metadata = dict(time=stamp, time_value=timestamp, slot_id=_field(SLOT, line),
                                        cpu_id=_field(CPU, line), module_name=raw_module,
                                        raw_module_name=raw_module)
                        if not all(metadata[name] for name in ("slot_id", "cpu_id", "module_name")):
                            add_diagnostic(database, source_id, number, "missing_fields", "Outer record missing SlotId/CpuId/ModuleName")
                            record("unclassified", line, number, metadata=metadata)
                            continue
                        firmware = FIRMWARE.search(line)
                        if firmware:
                            number_text = firmware.group(3)
                            if len(number_text) > 10 or int(number_text) > 2_147_483_647:
                                add_diagnostic(database, source_id, number, "invalid_segment", "Firmware seg number is outside supported integer range")
                                record("unclassified", line, number, metadata=metadata)
                                continue
                            current = dict(metadata, device_id=firmware.group(1), log_type=firmware.group(2),
                                           seg=int(number_text), segment_id=f"{source_id}:{number}")
                            database.execute(
                                "INSERT INTO segments(segment_id,source_id,line_number,time,time_value,slot_id,cpu_id,"
                                "module_name,device_id,log_type,seg,host,service,device_source,source_rank,raw_header) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (current["segment_id"], source_id, number, stamp, timestamp, metadata["slot_id"],
                                 metadata["cpu_id"], metadata["module_name"], current["device_id"], current["log_type"],
                                 current["seg"], header.group("host"), _field(SERVICE, line), source["device_source"], source_rank, line),
                            )
                            inline = firmware.group(4)
                            if inline:
                                if "(" in line[:firmware.start()] and inline.endswith(")"):
                                    inline = inline[:-1]
                            if inline:
                                record("firmware", line, number, metadata=current, body=inline)
                        elif re.search(r"\bfirmware\s+logType", line, re.I):
                            add_diagnostic(database, source_id, number, "invalid_segment", "Malformed firmware Unit/logType/seg metadata")
                            record("unclassified", line, number, metadata=metadata)
                        else:
                            info = _unpack_info(line)
                            if INFO.search(line) is None:
                                add_diagnostic(database, source_id, number, "missing_info", "Driver record missing Info; full source retained")
                                info = line
                            record("driver", line, number, metadata=dict(metadata, driver_domain="user",
                                   module_name=normalize_module_name(raw_module)), body=info)
                    elif current is not None:
                        record("firmware", line, number, metadata=current)
                    elif line.strip():
                        add_diagnostic(database, source_id, number, "orphan_line", "Line outside a recognized driver/firmware record")
                        record("unclassified", line, number)
                    if number % 2000 == 0:
                        database.commit()
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
