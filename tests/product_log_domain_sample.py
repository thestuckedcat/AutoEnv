"""Reproducible diag/var product archive, built entirely from synthetic logs."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import gzip
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import zipfile

from product_log_sample import build_sample_archive, _tar


def _zip(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            member = zipfile.ZipInfo(name, (2000, 1, 6, 3, 51, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, payload)
    return stream.getvalue()


def _driver(module: str, message: str, slot: int = 62, cpu: int = 0) -> str:
    return ("2000-01-06 03:51:50.123456 C01-A06-sfu2 %%01UBFWD/7/UBSDK_DIAG(D):"
            "Service=fwmd0;UB Forward SDK module. "
            f"(SlotId={slot}, CpuId={cpu}, ModuleName={module}, Info=<1:2> {message})\n")


def build_domain_archive(output: Path, *, source_dir: Path | None = None) -> dict:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="domain-sample-", dir=output.parent) as temporary:
        base = Path(temporary)
        baseline = build_sample_archive(base / "baseline.tar.gz", source_dir=base / "source")
        originals = sorted((base / "source" / "diag" / "slot62").glob("*.log"))
        diag: dict[str, bytes] = {}
        aliases = 0
        for index, original in enumerate(originals):
            name = (f"diaglog_62_20000106035{index}00.log" if index < len(originals) - 1 else "diag.log")
            lines = []
            for line in original.read_text(encoding="utf-8").splitlines(keepends=True):
                if "ModuleName=udk log," in line:
                    aliases += 1
                    if aliases % 2 == 0:
                        line = line.replace("ModuleName=udk log,", "ModuleName=udk upgrade,")
                lines.append(line)
            diag[name] = "".join(lines).encode("utf-8")
        diag["diag.log"] += (_driver("udk2", "must remain a separate module")
                             + _driver("EXTRA", "second slot", slot=63, cpu=1)
                             + _driver("EXTRA", "second cpu", cpu=1)).encode("utf-8")

    origin = datetime(2000, 1, 6, 3, 51)
    kernel: dict[str, bytes] = {}
    keywords = ("uDk", "mAmI", "MEME", "HBooT")
    for rank, suffix in enumerate((".1", ".2", ".10", "")):
        lines = []
        for number in range(100):
            stamp = origin + timedelta(seconds=rank * 100 + number)
            lines.append(f"[Thu Jan 6 {stamp:%H:%M:%S} 2000] <LpoThread_u10_p:25170:28546> "
                         f"{keywords[number % 4]} kernel seq={rank * 100 + number:03d}\n")
        # These follow dated records deliberately: neither may inherit a date.
        lines.extend(["udk continuation WITHOUT DATE\n", "plain continuation WITHOUT DATE\n"])
        kernel["cpdt_dmesg_current.log" + suffix] = "".join(lines).encode("utf-8")

    names = list(diag)
    members = {"diag/slot62/" + names[0]: diag[names[0]]}
    for index, mode in enumerate(("w", "w:gz", "w:bz2", "w:xz"), 1):
        extension = ("tar", "tar.gz", "tar.bz2", "tar.xz")[index - 1]
        members[f"diag/slot62/package-{index:02d}.{extension}"] = _tar({names[index]: diag[names[index]]}, mode)
    members["diag/slot62/package-05.zip"] = _zip({names[5]: diag[names[5]]})
    members["diag/slot62/diag.log.gz"] = gzip.compress(diag["diag.log"], mtime=0)
    # Two archive chains: tar.gz -> zip and tar.xz -> tar -> gz.
    members["var/log/older.tar.gz"] = _tar({"layer.zip": _zip({
        "cpdt_dmesg_current.log.1": kernel["cpdt_dmesg_current.log.1"],
        "cpdt_dmesg_current.log.2": kernel["cpdt_dmesg_current.log.2"],
    })}, "w:gz")
    members["var/log/newer.tar.xz"] = _tar({"deep.tar": _tar({
        "cpdt_dmesg_current.log.10.gz": gzip.compress(kernel["cpdt_dmesg_current.log.10"], mtime=0),
        "cpdt_dmesg_current.log": kernel["cpdt_dmesg_current.log"],
    }, "w")}, "w:xz")
    summary = {
        "collection_mode": "product", "diag_directory": "diag/slot62", "var_directory": "var",
        "files": 11, "user_records": baseline["driver_records"] + 3,
        "kernel_records": 408, "kernel_unknown_time": 8,
        "firmware_records": baseline["firmware_records"], "firmware_groups": 30,
        "user_per_module": {"udk": 500, "UBE": 500, "MEME": 500, "HBOOT": 500, "udk2": 1, "EXTRA": 2},
        "kernel_matches": {"udk": 104, "mami": 100, "MEME": 100, "HBOOT": 100},
        "kernel_source_order": list(kernel), "diag_source_order": names,
    }
    members["sample-summary.json"] = (json.dumps(summary, ensure_ascii=False, indent=2) + "\n").encode()
    output.write_bytes(_tar(members, "w:gz"))
    if source_dir is not None:
        for prefix, files in (("diag/slot62", diag), ("var/log", kernel)):
            folder = Path(source_dir) / prefix
            folder.mkdir(parents=True, exist_ok=True)
            for name, payload in files.items():
                (folder / name).write_bytes(payload)
        (Path(source_dir) / "sample-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_domain_archive(args.output, source_dir=args.source_dir), ensure_ascii=False, indent=2))
