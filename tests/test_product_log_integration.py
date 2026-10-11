"""Real standalone CLI acceptance using nested archives and mixed local logs."""
from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

from product_log.index import iter_export, list_groups, query_records
from product_log_sample import build_sample_archive
from product_log_domain_sample import build_domain_archive


ROOT = Path(__file__).resolve().parents[1]


def _header(stamp: str, *, cpu: int = 0, module: str = "FWD_SDK", info: str) -> str:
    return (f"2000-01-06 {stamp} C01-A06-sfu2 %%01UBFWD/7/UBSDK_DIAG(D):"
            "Service=fwmd0;UB Forward SDK module. "
            f"(SlotId=62, CpuId={cpu}, ModuleName={module}, Info={info})\n")


def _segment(stamp: str, seg: int, body: str) -> str:
    return _header(stamp, info=f"[INFO][HAL_UB]Unit[4] firmware logType[1] seg[{seg}]:") + body + "\n"


def _tar(files: dict[str, bytes], mode: str = "w:gz") -> bytes:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode=mode) as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return data.getvalue()


def _run(input_path: Path, output: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(ROOT / ".test-deps"), str(ROOT), environment.get("PYTHONPATH", ""),
    )))
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "product_log", str(input_path),
         "--output", str(output), *arguments],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def test_real_cli_all_archive_types_and_cross_file_firmware(tmp_path):
    first = (_segment("03:51:36.383", 0, "[87527.625][0-0][IMP-EVENT] A0")
             + _header("03:51:32.120", info="<28028:28041> 驱动内容 (括号)"))
    second = (_segment("03:51:37.383", 1, "[87528.435][0-1][IMP-EVENT] A1")
              + _header("03:51:33.000", module="OTHER", info="<1:2> other-module"))
    third = (_segment("03:51:35.383", 2, "[87529.100][0-0][IMP-EVENT] A2")
             + _header("03:51:31.000", cpu=1, info="<1:2> other-cpu"))
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("02-second.log", second)
    files = {
        "diag/slot62/01-first.log": first.encode(),
        "diag/slot62/nested.zip": zipped.getvalue(),
        "diag/slot62/03-third.log.gz": gzip.compress(third.encode()),
    }
    for index, (suffix, mode) in enumerate((
        ("tar", "w"), ("tar.gz", "w:gz"), ("tar.bz2", "w:bz2"), ("tar.xz", "w:xz"),
    ), 4):
        name = f"{index:02d}-driver.log"
        files[f"diag/slot62/package-{index}.{suffix}"] = _tar({
            name: _header(f"03:51:{20 + index:02d}.000", info=f"<1:2> {suffix}-driver").encode(),
        }, mode)
    # A different similarly named directory must be excluded by the partial path.
    files["other/slot62/ignored.log"] = _header("00:00:00.000", info="ignored").encode()
    input_path = tmp_path / "product.tar.gz"
    input_bytes = _tar(files)
    input_path.write_bytes(input_bytes)
    output = tmp_path / "batch"
    result = _run(input_path, output, "--directory", "diag/slot62", "--file-pattern", "*.log", "--workers", "3")
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["status"] == "ready"
    assert manifest["counts"]["firmware_records"] == 3
    assert manifest["counts"]["driver_records"] == 7
    assert manifest["counts"]["files"] == 7
    assert Path(manifest["product_log_path"]).is_absolute()
    assert input_path.read_bytes() == input_bytes

    firmware_groups = list_groups(output, "firmware")
    assert len(firmware_groups) == 1
    assert firmware_groups[0]["dump_count"] == 1
    assert (firmware_groups[0]["slot_id"], firmware_groups[0]["cpu_id"],
            firmware_groups[0]["device_id"], firmware_groups[0]["log_type"]) == ("62", "0", "4", "1")
    stitched = query_records(output, kind="firmware", view="stitched")
    assert [row["seg"] for row in stitched["records"]] == [0, 1, 2]
    assert len({row["dump_id"] for row in stitched["records"]}) == 1
    chronological = query_records(output, kind="firmware")
    assert [row["seg"] for row in chronological["records"]] == [2, 0, 1]
    assert [row["time"] for row in chronological["records"]] == sorted(row["time"] for row in chronological["records"])
    assert "[2000-01-06 03:51:35.383][87529.100]" in chronological["records"][0]["text"]

    drivers = query_records(output, kind="driver", keyword="驱动内容")
    assert drivers["total"] == 1
    assert drivers["records"][0]["text"] == "2000-01-06 03:51:32.120 Info=<28028:28041> 驱动内容 (括号)"
    raw = query_records(output, kind="driver", keyword="驱动内容", mode="raw")
    assert raw["records"][0]["text"] == first.splitlines()[-1]
    exported = list(iter_export(output, kind="driver", keyword="-driver"))
    assert len(exported) == 4
    assert all("ignored" not in text for text in exported)


def test_real_cli_directory_ambiguity_reports_candidates(tmp_path):
    for branch in ("diag", "other"):
        directory = tmp_path / "input" / branch / "slot62"
        directory.mkdir(parents=True)
        (directory / "01.log").write_text(_header("03:00:00.000", info="<1:2> local"), encoding="utf-8")
    output = tmp_path / "failed"
    result = _run(tmp_path / "input", output, "--directory", "slot62")
    assert result.returncode != 0
    assert "diag" in result.stderr and "other" in result.stderr
    assert not (output / "index.sqlite3").exists()


def test_large_mixed_sample_archive_all_groups_and_stitched_segments(tmp_path):
    archive_path = tmp_path / "product.tar.gz"
    expected = build_sample_archive(archive_path)
    output = tmp_path / "batch"
    result = _run(archive_path, output, "--directory", "diag/slot62",
                  "--file-pattern", "*.log", "--workers", "4")
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["counts"] == {
        "files": 7, "driver_records": 2000, "firmware_records": 3600,
        "dumps": 30, "diagnostics": 0, "unclassified_records": 0,
        "user_records": 2000, "kernel_records": 0,
    }
    groups = list_groups(output, "firmware")
    assert {(group["log_type"], group["device_id"]) for group in groups} == {
        (str(log_type), str(device)) for log_type in range(5) for device in range(6)
    }
    assert all(group["count"] == expected["firmware_per_group"] and
               group["source_count"] == 7 and group["dump_count"] == 1 for group in groups)
    for group in groups:
        rows = query_records(output, kind="firmware", group_id=group["group_id"],
                             view="stitched", limit=500)["records"]
        assert [row["seg"] for row in rows] == sorted(row["seg"] for row in rows)
        assert {row["seg"] for row in rows} == set(range(14))
        assert [int(row["text"].split("seq=")[1][:3]) for row in rows] == list(range(1, 121))
        chronological = query_records(output, kind="firmware", group_id=group["group_id"], limit=500)["records"]
        assert [row["time"] for row in chronological] == sorted(row["time"] for row in chronological)
    drivers = list_groups(output, "driver")
    assert {group["module_name"]: group["count"] for group in drivers} == {
        ("udk" if name == "udk log" else name): count
        for name, count in expected["driver_per_module"].items()
    }
    driver_rows = query_records(output, kind="driver", limit=500)["records"]
    assert len({row["module_name"] for row in driver_rows}) == 4
    assert [row["time"] for row in driver_rows] == sorted(row["time"] for row in driver_rows)


def test_real_cli_product_domains_nested_archives_and_unified_time(tmp_path):
    archive = tmp_path / "product.tar.gz"
    expected = build_domain_archive(archive)
    original = archive.read_bytes()
    output = tmp_path / "product-batch"
    result = _run(archive, output, "--collection-mode", "product",
                  "--diag-directory", "diag/slot62", "--var-directory", "var", "--workers", "3")
    assert result.returncode == 0, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["collection_mode"] == "product"
    assert manifest["counts"]["files"] == expected["files"]
    assert manifest["counts"]["user_records"] == expected["user_records"]
    assert manifest["counts"]["kernel_records"] == expected["kernel_records"]
    assert manifest["counts"]["firmware_records"] == expected["firmware_records"]
    assert archive.read_bytes() == original
    user_groups = list_groups(output, "driver", driver_domain="user")
    assert sum(group["count"] for group in user_groups if group["module_name"] == "udk") == 500
    firmware = list_groups(output, "firmware")
    assert len(firmware) == 30
    assert all(group["dump_count"] == 1 and group["source_count"] == 7 for group in firmware)
    kernel = query_records(output, kind="driver", driver_domain="kernel", module="udk", limit=500)
    assert kernel["total"] == 104
    assert sum(row["time_value"] is None for row in kernel["records"]) == 4
    assert all(row["slot_id"] == "" and row["cpu_id"] == "" for row in kernel["records"])
    rows = query_records(output, kind="all", limit=500)
    assert rows["total"] == 6011
    assert [row["time_value"] for row in rows["records"]] == sorted(row["time_value"] for row in rows["records"])
