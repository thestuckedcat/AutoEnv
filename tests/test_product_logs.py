"""Generated, entirely local product logs exercise the independent workflow."""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
import tarfile
import zipfile
from pathlib import Path

import pytest

from product_log import AnalysisCancelled, run_analysis
from product_log.index import get_source, iter_export, list_diagnostics, list_groups, query_records


def driver(message: str, *, time: str = "2000-01-06 03:51:36.383", slot: int = 62,
           cpu: int = 0, module: str = "FWD_SDK", host: str = "C01-A06-sfu2") -> str:
    return (f"{time} {host} %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;UB Forward SDK module. "
            f"(SlotId={slot}, CpuId={cpu}, ModuleName={module}, Info=<28028:28041> {message})")


def firmware(seg: int, *lines: str, time: str = "2000-01-06 03:51:36.383", slot: int = 62,
             cpu: int = 0, module: str = "FWD_SDK", device: int = 4, log_type: int = 1,
             host: str = "C01-A06-sfu2", inline: str = "") -> str:
    header = (f"{time} {host} %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;UB Forward SDK module. "
              f"(SlotId={slot}, CpuId={cpu}, ModuleName={module}, info=[INFO][HAL_UB]"
              f"Unit[{device}] firmware logType[{log_type}] seg[{seg}]:{inline}")
    return "\n".join([header, *lines])


def write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding=encoding)
    return path


def analyze(tmp_path: Path, files: dict[str, str], **options) -> tuple[dict, Path]:
    root = tmp_path / "input"
    for name, text in files.items():
        write(root / name, text)
    batch = tmp_path / "batch"
    return run_analysis(root, batch, **options), batch


def test_cross_file_segments_keep_own_times_and_both_ordering_views(tmp_path):
    manifest, batch = analyze(tmp_path, {
        "a.log": firmware(0, "[87527.625][0-0][IMP-EVENT] first", time="2000-01-06 03:51:38.383"),
        "b.log": firmware(1, "[87528.435][0-1][IMP-EVENT] second", time="2000-01-06 03:51:36.383"),
    })
    assert manifest["status"] == "ready"
    assert manifest["counts"]["dumps"] == 1
    assert manifest["counts"]["firmware_records"] == 2
    strict = query_records(batch, kind="firmware")["records"]
    stitched = query_records(batch, kind="firmware", view="stitched")["records"]
    assert [row["seg"] for row in strict] == [1, 0]
    assert [row["seg"] for row in stitched] == [0, 1]
    assert strict[0]["text"] == "[2000-01-06 03:51:36.383][87528.435][0-1][IMP-EVENT] second"
    assert strict[0]["dump_id"] == strict[1]["dump_id"]
    assert any(row["code"] == "segment_time_reversed" for row in list_diagnostics(batch)["records"])


def test_microsecond_time_order_and_filters_override_file_order(tmp_path):
    _, batch = analyze(tmp_path, {
        "a.log": driver("later", time="2000-01-06 03:51:36.383900") + "\n" + firmware(0, "later-firmware", time="2000-01-06 03:51:36.383900"),
        "b.log": driver("earlier", time="2000-01-06 03:51:36.383100") + "\n" + firmware(1, "earlier-firmware", time="2000-01-06 03:51:36.383100"),
    })
    for kind in ("driver", "firmware"):
        records = query_records(batch, kind=kind)["records"]
        assert [row["time"] for row in records] == ["2000-01-06 03:51:36.383100", "2000-01-06 03:51:36.383900"]
        assert records[1]["time_value"] - records[0]["time_value"] == 800
        filtered = query_records(batch, kind=kind, keyword="earlier", start_time="2000-01-06 03:51:36.383050", end_time="2000-01-06 03:51:36.383500")
        assert filtered["total"] == 1
        assert "earlier" in filtered["records"][0]["text"]
        assert len(list(iter_export(batch, kind=kind, end_time="2000-01-06 03:51:36.383500"))) == 1
    assert [row["seg"] for row in query_records(batch, kind="firmware", view="stitched")["records"]] == [0, 1]


def test_index_time_format_mismatch_requires_reimport(tmp_path):
    _, batch = analyze(tmp_path, {"a.log": driver("local")})
    manifest_path = batch / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["time_unit"] = "milliseconds"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="import the source again"):
        query_records(batch, kind="driver")


def test_archive_provenance_cannot_collide_with_literal_bang_directory(tmp_path):
    root = tmp_path / "input"
    write(root / "payload.zip!" / "file.log", driver("bare"))
    with zipfile.ZipFile(root / "payload.zip", "w") as archive:
        archive.writestr("file.log", driver("member") + "\n")
    batch = tmp_path / "batch"
    manifest = run_analysis(root, batch, file_patterns=["*.log"], copy_patterns=["file.log"])
    records = query_records(batch, kind="driver")["records"]
    assert manifest["counts"]["files"] == 2
    assert len({row["source_id"] for row in records}) == 2
    assert len({row["source_path"] for row in records}) == 2
    assert {row["text"].split()[-1] for row in records} == {"bare", "member"}
    assert {tuple(source["provenance_parts"]) for source in manifest["sources"]} == {
        ("payload.zip!/file.log",), ("payload.zip", "file.log"),
    }
    with pytest.raises(ValueError, match="Ambiguous relative rename"):
        run_analysis(root, tmp_path / "ambiguous", rename_map={"payload.zip!/file.log": "renamed.log"})


def test_interleaved_types_modules_hosts_and_multiple_dumps(tmp_path):
    manifest, batch = analyze(tmp_path, {
        "a.log": "\n".join([
            firmware(0, "A0"), firmware(0, "B0", module="OTHER"),
            firmware(0, "C0", log_type=2), firmware(0, "D0", host="other-device"),
        ]),
        "b.log": "\n".join([
            firmware(1, "A1"), firmware(1, "B1", module="OTHER"), firmware(1, "C1", log_type=2),
            firmware(1, "D1", host="other-device"), firmware(0, "Anew0"), firmware(1, "Anew1"),
        ]),
    })
    assert manifest["counts"]["dumps"] == 5
    rows = query_records(batch, kind="firmware", view="stitched")["records"]
    by_dump = {}
    for row in rows:
        by_dump.setdefault(row["dump_id"], []).append(row["raw_text"])
    assert sorted(by_dump.values()) == sorted([["A0", "A1"], ["B0", "B1"], ["C0", "C1"], ["D0", "D1"], ["Anew0", "Anew1"]])
    groups = list_groups(batch, "firmware")
    assert len(groups) == 2
    type1 = next(group for group in groups if group["log_type"] == "1")
    assert type1["module_names"] == ["FWD_SDK", "OTHER"]
    assert query_records(batch, kind="firmware", group_id=type1["group_id"], module_name="OTHER")["total"] == 2


def test_module_names_keep_inner_spaces_and_trim_field_padding(tmp_path):
    names = ("udk log", "UBE", "MEME", "HBOOT")
    padded_driver = driver("spaced module", module=" \tudk log\t ").replace("ModuleName=", "ModuleName \t = ")
    manifest, batch = analyze(tmp_path, {
        "a.log": "\n".join([
            padded_driver,
            *(driver(name, module=name) for name in names[1:]),
            firmware(0, "[1.0] first", module=" \tudk log\t "),
        ]),
        "b.log": firmware(1, "[2.0] second", module="udk log"),
    })
    groups = list_groups(batch, "driver")
    assert {group["module_name"] for group in groups} == {"udk", *names[1:]}
    for group in groups:
        result = query_records(batch, kind="driver", group_id=group["group_id"])
        assert result["total"] == 1
        assert result["records"][0]["module_name"] == group["module_name"]
    assert query_records(batch, kind="driver", module_name="udk log")["total"] == 1
    assert query_records(batch, kind="driver", module_name="udk")["total"] == 1
    assert query_records(batch, kind="driver", module_name="udk")["records"][0]["raw_module_name"] == "udk log"
    assert query_records(batch, kind="driver", module_name="udk log", mode="original")["records"][0]["text"] == padded_driver
    assert manifest["counts"]["dumps"] == 1
    assert list_groups(batch, "firmware")[0]["module_names"] == ["udk log"]
    firmware_rows = query_records(batch, kind="firmware", module_name="udk log", view="stitched")["records"]
    assert [row["seg"] for row in firmware_rows] == [0, 1]
    assert len({row["dump_id"] for row in firmware_rows}) == 1


def test_missing_duplicate_segments_and_duplicate_text_are_preserved(tmp_path):
    manifest, batch = analyze(tmp_path, {
        "a.log": firmware(3, "same") + "\n" + firmware(5, "same") + "\n" + firmware(5, "same"),
        "b.log": firmware(0, "same") + "\n" + firmware(2, "same"),
    })
    assert manifest["counts"]["dumps"] == 2
    assert query_records(batch, kind="firmware")["total"] == 5
    codes = [row["code"] for row in list_diagnostics(batch)["records"]]
    assert "missing_start" in codes
    assert "duplicate_segment" in codes
    assert codes.count("missing_segments") == 2


def test_mixed_driver_boundary_missing_fields_and_continuations(tmp_path):
    bad = "2000-01-06 03:51:37.383 dev %%01X:Info=missing slot"
    manifest, batch = analyze(tmp_path, {"logs.txt": "\n".join([
        "orphan", firmware(0, "[1.0] first", "continued (keep)", "2000-01-06 standalone", ""),
        bad, "orphan-after-bad", driver("正文(inner)", module="DIAG"),
    ])})
    assert manifest["counts"]["firmware_records"] == 4
    fw = query_records(batch, kind="firmware")["records"]
    assert fw[1]["raw_text"] == "continued (keep)"
    assert fw[2]["raw_text"] == "2000-01-06 standalone"
    assert fw[3]["raw_text"] == ""
    assert query_records(batch, kind="unclassified")["total"] == 0
    row = query_records(batch, kind="driver")["records"][0]
    assert row["text"] == "2000-01-06 03:51:36.383 Info=<28028:28041> 正文(inner)"
    assert query_records(batch, kind="driver", mode="original")["records"][0]["text"] == driver("正文(inner)", module="DIAG")


@pytest.mark.parametrize("archive_type", ["zip", "tar", "tgz", "bz2", "xz", "gz"])
def test_top_level_supported_archives_and_wrong_extension(tmp_path, archive_type):
    payload = driver("archive content").encode() + b"\n"
    archive = tmp_path / "input.unusual"
    if archive_type == "zip":
        with zipfile.ZipFile(archive, "w") as packed:
            packed.writestr("diag/slot62/log.txt", payload)
    elif archive_type == "gz":
        archive.write_bytes(gzip.compress(payload))
    else:
        modes = {"tar": "w", "tgz": "w:gz", "bz2": "w:bz2", "xz": "w:xz"}
        with tarfile.open(archive, modes[archive_type]) as packed:
            member = tarfile.TarInfo("diag/slot62/log.txt")
            member.size = len(payload)
            packed.addfile(member, io.BytesIO(payload))
    batch = tmp_path / "batch"
    manifest = run_analysis(archive, batch, directory_pattern="diag/slot62" if archive_type != "gz" else "")
    assert manifest["counts"]["driver_records"] == 1
    assert Path(manifest["input_root"]).is_absolute()
    assert query_records(batch, kind="driver")["records"][0]["origin_path"].startswith(str(archive) + "!/")


def test_nested_archives_plus_bare_files_copy_rename_and_source_provenance(tmp_path):
    root = tmp_path / "input"
    target = root / "node" / "diag" / "slot62"
    write(target / "a.log", firmware(0, "[1.0] zero"))
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target / "archive.zip", "w") as packed:
        packed.writestr("b.log", firmware(1, "[2.0] one") + "\n")
        packed.writestr("c.txt", driver("copied other type") + "\n")
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as nested:
            nested.writestr("nested.log", driver("nested source"))
        packed.writestr("inner.zip", inner.getvalue())
    batch = tmp_path / "batch"
    manifest = run_analysis(root, batch, directory_pattern="diag/slot62", file_patterns=["*.log"],
                            copy_patterns=["*.txt", "a.log"], rename_map={"a.log": "a-renamed.log"})
    assert manifest["counts"]["files"] == 4
    assert manifest["counts"]["firmware_records"] == 2
    assert manifest["counts"]["driver_records"] == 2
    assert manifest["counts"]["dumps"] == 1
    assert (target / "a.log").exists()
    zero = next(row for row in query_records(batch, kind="firmware")["records"] if row["seg"] == 0)
    assert Path(zero["source_path"]).name == "a-renamed.log"
    assert zero["origin_path"] == str(target / "a.log")
    one = next(row for row in query_records(batch, kind="firmware")["records"] if row["seg"] == 1)
    assert one["member_path"] == "b.log"
    assert "archive.zip!/b.log" in one["origin_path"]


def test_unique_directory_ambiguity_is_a_failed_batch(tmp_path):
    root = tmp_path / "input"
    write(root / "a/diag/slot62/log", driver("one"))
    write(root / "b/diag/slot62/log", driver("two"))
    batch = tmp_path / "batch"
    with pytest.raises(ValueError):
        run_analysis(root, batch, directory_pattern="diag/slot62")
    assert json.loads((batch / "manifest.json").read_text())["status"] == "failed"
    with pytest.raises(ValueError, match="not ready"):
        query_records(batch, kind="driver")


def test_millisecond_order_substring_search_raw_scope_paging_context_export(tmp_path):
    manifest, batch = analyze(tmp_path, {"b.log": "\n".join([
        driver("before", time="2000-01-06 03:51:36.380"),
        driver("hit % _ [literal] 中文", time="2000-01-06 03:51:36.382"),
        driver("after", time="2000-01-06 03:51:36.383"),
    ]), "a.log": driver("earliest", time="2000-01-06 03:51:36.381")})
    result = query_records(batch, kind="driver", offset=1, limit=1)
    assert result["total"] == 4 and result["has_more"]
    assert result["records"][0]["text"].endswith("earliest")
    context = query_records(batch, kind="driver", keyword="% _ [literal] 中文", context_lines=1)
    assert context["total"] == 1
    assert [row["is_context"] for row in context["records"]] == [True, False, True]
    assert query_records(batch, kind="driver", keyword="ModuleName")["total"] == 0
    assert query_records(batch, kind="driver", keyword="ModuleName", raw_search=True)["total"] == 4
    filtered = query_records(batch, kind="driver", start_time="2000-01-06T03:51:36.381", end_time="2000-01-06 03:51:36.382")
    assert filtered["total"] == 2
    exported = "".join(iter_export(batch, kind="driver", keyword="hit", mode="original"))
    assert exported == driver("hit % _ [literal] 中文", time="2000-01-06 03:51:36.382") + "\n"
    assert query_records(batch, kind="driver", keyword="' OR 1=1 --")["total"] == 0
    with pytest.raises(ValueError):
        query_records(batch, kind="driver", limit=501)
    with pytest.raises(ValueError):
        query_records(batch, kind="driver", start_time="2000-01-07 00:00:00", end_time="2000-01-06 00:00:00")
    with pytest.raises(ValueError):
        query_records(batch, kind="firmware", group_id=list_groups(batch, "driver")[0]["group_id"])


def test_firmware_inline_raw_header_search_and_source_window(tmp_path):
    _, batch = analyze(tmp_path, {"logs.txt": firmware(0, "next", inline="[1.0] inline)")})
    rows = query_records(batch, kind="firmware")["records"]
    assert rows[0]["text"].endswith("[1.0] inline")
    assert query_records(batch, kind="firmware", keyword="HAL_UB", raw_search=True)["total"] == 2
    assert query_records(batch, kind="firmware", keyword="HAL_UB")["total"] == 0
    source = get_source(batch, rows[0]["source_id"], offset=1, limit=1)
    assert source["lines"] == [{"line_number": 2, "text": "next"}]
    assert source["total"] == 2
    with pytest.raises(FileNotFoundError):
        get_source(batch, "../../secrets")


def test_utf8_bom_gb18030_undecodable_and_binary_inputs(tmp_path):
    root = tmp_path / "input"
    write(root / "a.log", driver("UTF 中文"), "utf-8-sig")
    write(root / "b.log", driver("GB 中文"), "gb18030")
    (root / "c.bin").write_bytes(b"\x00binary\n")
    (root / "d.txt").write_bytes(b"\xff\n")
    batch = tmp_path / "batch"
    manifest = run_analysis(root, batch)
    assert manifest["counts"]["driver_records"] == 2
    assert query_records(batch, kind="driver", keyword="中文")["total"] == 2
    codes = {row["code"] for row in list_diagnostics(batch)["records"]}
    assert {"binary_file", "decode_error", "orphan_line"} <= codes
    gb = next(row for row in query_records(batch, kind="driver")["records"] if "GB 中文" in row["text"])
    assert get_source(batch, gb["source_id"])["source"]["encoding"] == "gb18030"


def test_parallel_completion_does_not_change_dump_membership(tmp_path, monkeypatch):
    import time
    import product_log.workflow as workflow
    root = tmp_path / "input"
    write(root / "log10.txt", firmware(0, "first"))
    write(root / "log2.txt", firmware(1, "second"))
    write(root / "log3.txt", firmware(0, "next-first"))
    original = workflow.parse_file

    def slow_first(path, *args, **kwargs):
        if path.name == "log10.txt":
            time.sleep(0.04)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(workflow, "parse_file", slow_first)
    left, right = tmp_path / "serial", tmp_path / "parallel"
    run_analysis(root, left, workers=1)
    run_analysis(root, right, workers=4)
    serial = query_records(left, kind="firmware", view="stitched")["records"]
    parallel = query_records(right, kind="firmware", view="stitched")["records"]
    assert [(row["id"], row["dump_id"], row["seg"], row["text"]) for row in serial] == [
        (row["id"], row["dump_id"], row["seg"], row["text"]) for row in parallel]
    assert [row["raw_text"] for row in serial] == ["first", "second", "next-first"]


def test_cancellation_failure_and_output_isolation(tmp_path):
    root = tmp_path / "input"
    write(root / "a.log", driver("first"))
    write(root / "b.log", driver("second"))
    cancel = False

    def progress(event):
        nonlocal cancel
        if event["stage"] == "parsing" and event["count"] == 1:
            cancel = True

    batch = tmp_path / "cancelled"
    with pytest.raises(AnalysisCancelled):
        run_analysis(root, batch, workers=1, progress=progress, cancelled=lambda: cancel)
    assert json.loads((batch / "manifest.json").read_text())["status"] == "cancelled"
    with pytest.raises(ValueError, match="not ready"):
        query_records(batch, kind="driver")
    with pytest.raises(FileExistsError):
        run_analysis(root, batch)
    ready = run_analysis(root, root / "nested-output")
    assert ready["counts"]["files"] == 2
    assert ready["counts"]["driver_records"] == 2
    corrupted = tmp_path / "broken.zip"
    corrupted.write_bytes(b"PK\x03\x04damaged")
    failed = tmp_path / "failed"
    with pytest.raises((ValueError, zipfile.BadZipFile)):
        run_analysis(corrupted, failed)
    assert json.loads((failed / "manifest.json").read_text())["status"] == "failed"


def test_no_recursion_patterns_and_unmatched_rename(tmp_path):
    root = tmp_path / "input"
    write(root / "a.log", driver("a"))
    write(root / "nested/b.log", driver("b"))
    manifest = run_analysis(root, tmp_path / "batch", recursive=False, file_patterns=["*.log", "a*"])
    assert manifest["counts"]["files"] == 1
    with pytest.raises(ValueError, match="Rename rules"):
        run_analysis(root, tmp_path / "bad-rename", rename_map={"missing.log": "new.log"})


def test_task_expansion_budget_is_shared_across_archives(tmp_path, monkeypatch):
    import product_log.workflow as workflow
    root = tmp_path / "input"
    root.mkdir()
    for name in ("a.zip", "b.zip"):
        with zipfile.ZipFile(root / name, "w") as packed:
            packed.writestr("log.txt", driver(name))
    monkeypatch.setattr(workflow, "MAX_EXPANDED_FILES", 1)
    with pytest.raises(ValueError, match="budget"):
        run_analysis(root, tmp_path / "batch")


def test_keyword_id_cache_pagination_capacity_and_stitched_order(tmp_path, monkeypatch):
    import product_log.index as index
    _, batch = analyze(tmp_path, {"a.log": firmware(0, "shared first") + "\n" +
                                firmware(1, "shared second", time="2000-01-06 03:51:35.000")})
    first = query_records(batch, kind="firmware", keyword="shared", view="stitched", limit=1)
    second = query_records(batch, kind="firmware", keyword="shared", view="stitched", offset=1, limit=1)
    assert first["records"][0]["seg"] == 0
    assert second["records"][0]["seg"] == 1
    with sqlite3.connect(batch / "query-cache.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == 2
    monkeypatch.setattr(index, "MAX_CACHED_QUERIES", 2)
    for keyword in ("first", "second", "missing", "Unit"):
        query_records(batch, kind="firmware", keyword=keyword, raw_search=True)
    with sqlite3.connect(batch / "query-cache.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM queries").fetchone()[0] <= 2
    monkeypatch.setattr(index, "MAX_CACHED_HITS", 1)
    uncached = query_records(batch, kind="firmware", keyword="shared", view="time", limit=1)
    assert uncached["total"] == 2
    assert uncached["records"][0]["seg"] == 1
    with sqlite3.connect(batch / "query-cache.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM queries WHERE total=-1").fetchone()[0] == 1


def test_output_directory_is_excluded_from_unique_directory_matching(tmp_path):
    root = tmp_path / "input"
    write(root / "actual/diag/log.txt", driver("only input"))
    output = root / "diag"  # A generated output with the same basename.
    manifest = run_analysis(root, output, directory_pattern="diag")
    assert manifest["target_path"] == str((root / "actual/diag").resolve())
    assert manifest["counts"]["driver_records"] == 1


def test_original_source_changed_after_import_is_detected(tmp_path):
    _, batch = analyze(tmp_path, {"a.log": driver("original")})
    row = query_records(batch, kind="driver")["records"][0]
    write(Path(row["source_path"]), driver("modified source with different length"))
    with pytest.raises(ValueError, match="changed after analysis"):
        get_source(batch, row["source_id"])
    # The immutable parsed snapshot can still be read and exported.
    assert query_records(batch, kind="driver")["records"][0]["text"].endswith("original")


def test_corrupt_log_metadata_is_preserved_without_merging_unknown_streams(tmp_path):
    invalid_date = driver("bad date", time="2000-99-06 03:51:36.383")
    malformed = firmware(0, "orphan after bad seg").replace("seg[0]", "seg[99999999999999999999999]")
    _, batch = analyze(tmp_path, {"log.txt": invalid_date + "\n" + malformed + "\n" + driver("good")})
    assert query_records(batch, kind="driver")["total"] == 1
    assert query_records(batch, kind="firmware")["total"] == 0
    assert query_records(batch, kind="unclassified")["total"] == 0
    codes = {row["code"] for row in list_diagnostics(batch)["records"]}
    assert {"invalid_time", "invalid_segment", "orphan_line"} <= codes


def test_raw_firmware_export_and_cli_archive_error_exit_code(tmp_path, capsys):
    from product_log.__main__ import main
    _, batch = analyze(tmp_path, {"a.log": firmware(0, "[1.0] original body")})
    assert "".join(iter_export(batch, kind="firmware", mode="raw")) == "[1.0] original body\n"
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"PK\x03\x04broken")
    assert main([str(broken), "--output", str(tmp_path / "failed-cli")]) == 2
    assert "BadZipFile" in capsys.readouterr().err


def test_empty_firmware_dump_still_exposes_its_parsed_group(tmp_path):
    manifest, batch = analyze(tmp_path, {"a.log": firmware(0, inline=")")})
    assert manifest["counts"]["firmware_records"] == 0
    assert manifest["counts"]["dumps"] == 1
    groups = list_groups(batch, "firmware")
    assert len(groups) == 1
    assert groups[0]["count"] == 0
    assert groups[0]["dump_count"] == 1
    assert query_records(batch, kind="firmware", group_id=groups[0]["group_id"])["total"] == 0
