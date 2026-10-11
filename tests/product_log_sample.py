"""Reproducible mixed product-log archive for local/manual acceptance."""
from __future__ import annotations

import argparse
import gzip
import io
import json
from pathlib import Path
import random
import tarfile
import zipfile
from datetime import datetime, timedelta


DRIVER_MODULES = ("udk log", "UBE", "MEME", "HBOOT")
FIRMWARE_PER_GROUP = 120
DRIVER_PER_MODULE = 500
FILE_COUNT = 7
SEGMENTS_PER_GROUP = 14
SEED = 20261011


def _tar(files: dict[str, bytes], mode: str) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode=mode, format=tarfile.PAX_FORMAT) as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mtime = 0
            archive.addfile(member, io.BytesIO(payload))
    return stream.getvalue()


def build_sample_archive(output: Path, *, source_dir: Path | None = None) -> dict:
    """30 firmware groups and four driver groups, mixed across seven files.

    A segment body stays intact. Its group's segments remain ordered across
    files, while different groups and driver lines are randomly interleaved.
    """
    rng = random.Random(SEED)
    origin = datetime(2000, 1, 6, 3, 51, 0)

    def header(module: str, info: str, *, seg: int | None = None) -> str:
        # Read times differ irregularly between groups, while a group's
        # successive segments retain a valid increasing read time.
        delta = rng.randrange(180_000_000) if seg is None else seg * 10_000_000 + rng.randrange(9_000_000)
        stamp = (origin + timedelta(microseconds=delta)).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
        return (f"{stamp} C01-A06-sfu2 %%01UBFWD/7/UBSDK_DIAG(D):"
                "Service=fwmd0;UB Forward SDK module. "
                f"(SlotId=62, CpuId=0, ModuleName={module}, Info={info})\n")

    parts: dict[tuple[int, int], list[int]] = {}
    emitted: dict[tuple[int, int], int] = {}
    for log_type in range(5):
        for device in range(6):
            # Positive, uneven segment sizes with an exact total of 120.
            cuts = sorted(rng.sample(range(1, FIRMWARE_PER_GROUP), SEGMENTS_PER_GROUP - 1))
            edges = [0, *cuts, FIRMWARE_PER_GROUP]
            parts[log_type, device] = [b - a for a, b in zip(edges, edges[1:])]
            emitted[log_type, device] = 0

    driver_entries = [(module, number) for module in DRIVER_MODULES
                      for number in range(1, DRIVER_PER_MODULE + 1)]
    rng.shuffle(driver_entries)
    files: dict[str, bytes] = {}
    for file_number in range(FILE_COUNT):
        blocks: list[str] = []
        for seg in range(file_number * 2, file_number * 2 + 2):
            combinations = list(parts)
            rng.shuffle(combinations)
            for log_type, device in combinations:
                body: list[str] = []
                for _ in range(parts[log_type, device][seg]):
                    emitted[log_type, device] += 1
                    number = emitted[log_type, device]
                    uptime = 87527.000 + number * 0.137 + device * 0.013 + log_type * 0.007
                    body.append(f"[{uptime:.3f}][{device}-{number % 2}][IMP-EVENT] "
                                f"固件日志 type={log_type} dev={device} seq={number:03d} "
                                f"sample={rng.randrange(10000):04d}\n")
                blocks.append(header("FWD_SDK", f"[INFO][HAL_UB]Unit[{device}] "
                                     f"firmware logType[{log_type}] seg[{seg}]:", seg=seg) + "".join(body))
        start = len(driver_entries) * file_number // FILE_COUNT
        end = len(driver_entries) * (file_number + 1) // FILE_COUNT
        # Spread single-line drivers through the firmware blocks without
        # breaking a segment or reordering any group's segment headers.
        driver_slots: list[list[str]] = [[] for _ in range(len(blocks) + 1)]
        for module, number in driver_entries[start:end]:
            slot = rng.randrange(len(driver_slots))
            driver_slots[slot].append(header(module, f"<28028:28041> 驱动日志 "
                                            f"name={module} seq={number:03d} "
                                            f"sample={rng.randrange(10000):04d}"))
        mixed: list[str] = []
        for index, block in enumerate(blocks):
            mixed.extend(driver_slots[index])
            mixed.append(block)
        mixed.extend(driver_slots[-1])
        files[f"{file_number + 1:02d}-mixed.log"] = "".join(mixed).encode("utf-8")

    names = list(files)
    nested_zip = io.BytesIO()
    with zipfile.ZipFile(nested_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        member = zipfile.ZipInfo(names[1], date_time=(2000, 1, 6, 3, 51, 0))
        member.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(member, files[names[1]])
    target = "diag/slot62/"
    members = {
        target + names[0]: files[names[0]],
        target + "package-02.zip": nested_zip.getvalue(),
        target + "package-03.tar": _tar({names[2]: files[names[2]]}, "w"),
        target + "package-04.tar.gz": _tar({names[3]: files[names[3]]}, "w:gz"),
        target + "package-05.tar.bz2": _tar({names[4]: files[names[4]]}, "w:bz2"),
        target + "package-06.tar.xz": _tar({names[5]: files[names[5]]}, "w:xz"),
        target + names[6] + ".gz": gzip.compress(files[names[6]], mtime=0),
    }
    summary = {
        "seed": SEED, "slot_id": 62, "cpu_id": 0,
        "directory_pattern": "diag/slot62", "file_pattern": "*.log",
        "files": FILE_COUNT, "firmware_groups": len(parts),
        "firmware_per_group": FIRMWARE_PER_GROUP,
        "firmware_records": len(parts) * FIRMWARE_PER_GROUP,
        "segments_per_group": SEGMENTS_PER_GROUP,
        "driver_per_module": {name: DRIVER_PER_MODULE for name in DRIVER_MODULES},
        "driver_records": len(driver_entries),
    }
    members["sample-summary.json"] = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".part")
    try:
        temporary.write_bytes(_tar(members, "w:gz"))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    if source_dir is not None:
        raw_root = source_dir / "diag" / "slot62"
        raw_root.mkdir(parents=True, exist_ok=True)
        for name, payload in files.items():
            (raw_root / name).write_bytes(payload)
        (source_dir / "sample-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_sample_archive(args.output, source_dir=args.source_dir),
                     ensure_ascii=False, indent=2))
