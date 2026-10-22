"""Real local diag/dmesg files exercise two driver domains and module mappings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_log._storage import INDEX_SCHEMA_VERSION, write_manifest
from product_log.index import build_index, get_source, iter_export, list_groups, query_records
from product_log.module_map import list_module_mappings, save_module_mappings
from product_log.parser import parse_file, time_value


def user_line(message: str, module: str = "udk", time: str = "2000-01-06 04:36:49.123456",
              slot: int = 62, cpu: int = 0) -> str:
    return (f"{time} device %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0; "
            f"(SlotId={slot}, CpuId={cpu}, ModuleName={module}, Info={message})")


def firmware_line(log_type: int, message: str = "[1.000][0-0][IMP-EVENT] firmware", *,
                  seg: int = 0, time: str = "2000-01-06 04:36:49.100000") -> str:
    return (f"{time} device %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0; "
            f"(SlotId=62, CpuId=0, ModuleName=FWD_SDK, info=[INFO][HAL_UB]"
            f"Unit[4] firmware logType[{log_type}] seg[{seg}]:\n{message}")


def batch_from_files(tmp_path: Path, files: list[tuple[str, str, str]]) -> Path:
    batch = tmp_path / "batch"
    batch.mkdir()
    shards = []
    for rank, (name, domain, text) in enumerate(files):
        path = tmp_path / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        source = {"source_id": f"source-{rank}", "source_rank": rank, "path": str(path),
                  "relative_path": name, "origin_path": str(path), "archive_path": "", "member_path": "",
                  "device_source": "fixture-device", "log_domain": domain}
        shard = tmp_path / f"shard-{rank}.sqlite3"
        parse_file(path, shard, source)
        shards.append(shard)
    counts = build_index(batch, shards)
    write_manifest(batch / "manifest.json", {"status": "ready", "index_schema_version": INDEX_SCHEMA_VERSION,
                                             "time_unit": "microseconds", "counts": counts})
    return batch


def test_user_aliases_preserve_original_names_and_do_not_merge_udk2(tmp_path):
    names = ["udk", "udk log", "udk upgrade", "udk2", "OTHER log"]
    batch = batch_from_files(tmp_path, [("diag.log", "user", "\n".join(
        user_line(name, name) for name in names))])
    groups = list_groups(batch, "driver", "user")
    assert {row["module_name"]: row["count"] for row in groups} == {"udk": 3, "udk2": 1, "OTHER log": 1}
    rows = query_records(batch, kind="driver", module_name="udk")
    assert rows["total"] == 3
    assert [row["raw_module_name"] for row in rows["records"]] == names[:3]
    assert all(row["driver_domain"] == "user" for row in rows["records"])
    assert [row["raw_text"] for row in rows["records"]] == [user_line(name, name) for name in names[:3]]
    assert query_records(batch, kind="driver", module_name="udk log")["total"] == 3


def test_kernel_dates_unknown_lines_never_inherit_and_source_is_retained(tmp_path):
    text = "\n".join([
        "udk beginning without date", "[Thu Jan 6 04:36:49 2000] <thread:1:2> UDK ready",
        "udk continuation without date", "[Thu Feb 30 04:36:49 2000] udk invalid date",
        "[Thu Jan 6 04:36:49.987654 2000] udk fractional",
    ])
    batch = batch_from_files(tmp_path, [("var/cpdt_dmesg_current.log", "kernel", text)])
    result = query_records(batch, kind="driver", driver_domain="kernel")
    assert result["total"] == 5
    timed, fractional, *unknown = result["records"]
    assert timed["time"] == "2000-01-06 04:36:49"
    assert timed["time_value"] == time_value(timed["time"])
    assert fractional["time_value"] - timed["time_value"] == 987654
    assert all(row["time"] == "" and row["time_value"] is None for row in unknown)
    assert all(row["text"].startswith("[时间未知]") for row in unknown)
    assert all(row["slot_id"] == row["cpu_id"] == row["module_name"] == "" for row in result["records"])
    original = query_records(batch, kind="driver", driver_domain="kernel", mode="original")
    assert "[Thu Jan 6 04:36:49 2000]" in original["records"][0]["text"]
    source = get_source(batch, timed["source_id"])
    assert source["source"]["log_domain"] == "kernel"
    assert [row["text"] for row in source["lines"]] == text.splitlines()


@pytest.mark.parametrize("month,number", [("Jan", 1), ("Feb", 2), ("Mar", 3), ("Apr", 4), ("May", 5),
                                       ("Jun", 6), ("Jul", 7), ("Aug", 8), ("Sep", 9), ("Oct", 10),
                                       ("Nov", 11), ("Dec", 12)])
def test_kernel_english_months_do_not_depend_on_operating_system_locale(tmp_path, month, number):
    batch = batch_from_files(tmp_path, [("cpdt_dmesg_current.log", "kernel",
                                         f"[Thu {month} 6 04:36:49 2000] udk message")])
    assert query_records(batch, kind="driver")["records"][0]["time"] == f"2000-01-06 04:36:49".replace("01-06", f"{number:02d}-06")


def test_unified_timeline_sorts_domains_and_excludes_unknown_from_date_ranges(tmp_path):
    batch = batch_from_files(tmp_path, [
        ("diaglog_62_20000106043000.log", "user", firmware_line(1) + "\n" + user_line("user", time="2000-01-06 04:36:49.200000")),
        ("cpdt_dmesg_current.log.1", "kernel", "[Thu Jan 6 04:36:49 2000] udk kernel\nudk unknown"),
        ("diag.log", "user", firmware_line(1, "[2.000] next seg", seg=1, time="2000-01-06 04:36:50")),
    ])
    result = query_records(batch, kind="all")
    assert query_records(batch, kind="all", view="stitched")["records"] == result["records"]
    assert [(row["kind"], row["driver_domain"]) for row in result["records"]] == [
        ("driver", "kernel"), ("firmware", ""), ("driver", "user"), ("firmware", ""), ("driver", "kernel")]
    assert result["records"][-1]["time_value"] is None
    dates = query_records(batch, kind="all", start_time="2000-01-06 04:36:49", end_time="2000-01-06 04:36:49.199999")
    assert dates["total"] == 2
    assert len(list(iter_export(batch, kind="all", start_time="2000-01-06 04:36:49", end_time="2000-01-06 04:36:49.199999"))) == 2
    assert query_records(batch, kind="firmware", view="stitched")["records"][0]["dump_id"] == result["records"][3]["dump_id"]
    raw = query_records(batch, kind="all", mode="raw")
    assert raw["records"][1]["text"].startswith("[2000-01-06 04:36:49.100000][1.000]")
    assert any(text.startswith("[2000-01-06 04:36:49.100000][1.000]") for text in iter_export(batch, kind="all", mode="raw"))


def test_explicit_module_mappings_counts_and_kernel_matching_without_discovery(tmp_path):
    batch = batch_from_files(tmp_path, [
        ("diag.log", "user", "\n".join([user_line("one", "udk log"), user_line("two", "udk upgrade"),
                                            user_line("three", "UBE"), user_line("four", "HBOOT")])),
        ("var/cpdt_dmesg_current.log", "kernel", "[Thu Jan 6 04:36:49 2000] UDK mami one\nudk two\nHBOOT three"),
    ])
    mappings = {row["module"]: row for row in list_module_mappings(batch)}
    assert (mappings["udk"]["user_count"], mappings["udk"]["kernel_count"]) == (2, 2)
    assert mappings["UBE"]["kernel_keyword"] == "mami"
    assert "HBOOT" not in mappings
    assert query_records(batch, kind="driver", module="udk")["total"] == 4
    assert query_records(batch, kind="driver", module="UBE", driver_domain="kernel")["total"] == 1
    with pytest.raises(ValueError, match="Module does not belong"):
        query_records(batch, kind="driver", module="HBOOT", driver_domain="kernel")


def test_unknown_kernel_context_is_marked_and_stably_sorted_after_dated_hits(tmp_path):
    batch = batch_from_files(tmp_path, [("cpdt_dmesg_current.log", "kernel",
        "udk before without date\n[Thu Jan 6 04:36:49 2000] UDK hit\nudk after without date")])
    for kind, view in (("driver", "time"), ("driver", "stitched"), ("all", "stitched")):
        result = query_records(batch, kind=kind, driver_domain="kernel", module="udk", keyword="hit",
                               context_lines=1, start_time="2000-01-06 04:36:49", view=view)
        assert result["total"] == 1
        assert [row["line_number"] for row in result["records"]] == [2, 1, 3]
        assert [row["is_context"] for row in result["records"]] == [False, True, True]
        assert all(row["time_value"] is None for row in result["records"][1:])
    assert len(list(iter_export(batch, kind="all", module="udk", keyword="hit", context_lines=1,
                                start_time="2000-01-06 04:36:49"))) == 1


def test_mapping_changes_apply_without_reparse_and_overlap_is_not_duplicated(tmp_path):
    batch = batch_from_files(tmp_path, [
        ("diag.log", "user", user_line("user")),
        ("cpdt_dmesg_current.log", "kernel", "[Thu Jan 6 04:36:49 2000] UDK MAMI mixed\nudk other\nA_B% literal"),
    ])
    index_bytes = (batch / "index.sqlite3").read_bytes()
    initial = query_records(batch, kind="driver", module="udk", keyword="other")
    assert initial["total"] == 1
    rows = save_module_mappings(batch, [
        {"module": "udk", "user_module_name": "udk", "kernel_keyword": "mami"},
        {"module": "missing", "user_module_name": "DOES_NOT_EXIST", "kernel_keyword": "UDK"},
        {"module": "literal", "user_module_name": "", "kernel_keyword": "a_b%"},
    ])
    missing = next(row for row in rows if row["module"] == "missing")
    assert missing["user_exists"] is False and missing["user_count"] == 0 and missing["kernel_count"] == 2
    assert query_records(batch, kind="driver", module="udk", keyword="other")["total"] == 0
    assert query_records(batch, kind="driver", module="missing")["total"] == 2
    assert query_records(batch, kind="driver", module="literal")["total"] == 1
    assert query_records(batch, kind="driver")["total"] == 4
    assert (batch / "index.sqlite3").read_bytes() == index_bytes
    assert json.loads((batch / "module-map.json").read_text(encoding="utf-8"))[0]["kernel_keyword"] == "mami"
    assert {row["module"] for row in list_module_mappings(batch)} == {"udk", "missing", "literal"}


def test_device_filters_ignore_kernel_and_module_mapping_does_not_hide_firmware(tmp_path):
    batch = batch_from_files(tmp_path, [
        ("diag.log", "user", user_line("selected") + "\n" + user_line("other cpu", cpu=1) + "\n" + firmware_line(2)),
        ("cpdt_dmesg_current.log", "kernel", "[Thu Jan 6 04:36:49 2000] udK kernel"),
    ])
    result = query_records(batch, kind="all", slot_id="62", cpu_id="0", device_id="4", log_type="2", module="udk")
    assert result["total"] == 3
    assert {row["kind"] + ":" + row["driver_domain"] for row in result["records"]} == {"driver:user", "driver:kernel", "firmware:"}
    assert query_records(batch, kind="all", driver_domain="kernel", module="udk")["total"] == 2
    assert query_records(batch, kind="all", module_name="FWD_SDK")["total"] == 2
    assert query_records(batch, kind="driver", driver_domain="kernel", slot_id="999", cpu_id="999", module_name="missing")["total"] == 1


def test_kernel_groups_are_mapping_dependent_and_user_groups_keep_exact_devices(tmp_path):
    batch = batch_from_files(tmp_path, [
        ("diag.log", "user", user_line("one", "udk log") + "\n" + user_line("two", "udk upgrade", cpu=1)),
        ("cpdt_dmesg_current.log", "kernel", "[Thu Jan 6 04:36:49 2000] udk kernel\nmami other"),
    ])
    users = list_groups(batch, "driver", "user")
    kernels = list_groups(batch, "driver", "kernel")
    assert len(users) == len(kernels) == 2
    assert all(row["driver_domain"] == "kernel" and row["slot_id"] == row["cpu_id"] == "" for row in kernels)
    assert query_records(batch, kind="driver", group_id=users[0]["group_id"])["total"] == 1
    group = next(row for row in kernels if row["module"] == "udk")
    assert query_records(batch, kind="driver", group_id=group["group_id"])["records"][0]["raw_text"].endswith("udk kernel")
    save_module_mappings(batch, [{"module": "udk", "user_module_name": "udk", "kernel_keyword": "mami"}])
    assert query_records(batch, kind="driver", group_id=group["group_id"])["records"][0]["raw_text"] == "mami other"
    with pytest.raises(ValueError, match="Group does not belong"):
        query_records(batch, kind="firmware", group_id=group["group_id"])


@pytest.mark.parametrize("log_type,label", [(0, "bios"), (1, "IMP"), (2, "IMU"), (3, "HSM"), (9, "9")])
def test_firmware_type_names_are_labels_and_numeric_filter_values_stay_stable(tmp_path, log_type, label):
    batch = batch_from_files(tmp_path, [("diag.log", "user", firmware_line(log_type))])
    group = list_groups(batch, "firmware")[0]
    assert group["log_type"] == str(log_type) and group["log_type_name"] == label
    record = query_records(batch, kind="firmware", log_type=str(log_type))["records"][0]
    assert record["log_type_name"] == label
    assert list_groups(batch, "all")[0]["group_id"] == group["group_id"]


@pytest.mark.parametrize("rows", [None, [{}], [{"module": "x"}, {"module": "x"}], [{"module": "x", "kernel_keyword": 1}]])
def test_invalid_module_table_cannot_replace_valid_mapping(tmp_path, rows):
    batch = batch_from_files(tmp_path, [("diag.log", "user", user_line("one"))])
    save_module_mappings(batch, [{"module": "x", "user_module_name": "udk", "kernel_keyword": "hello"}])
    before = (batch / "module-map.json").read_bytes()
    with pytest.raises(ValueError):
        save_module_mappings(batch, rows)
    assert (batch / "module-map.json").read_bytes() == before


def test_old_schema_cannot_be_queried_as_new_driver_domains(tmp_path):
    batch = batch_from_files(tmp_path, [("diag.log", "user", user_line("one"))])
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["index_schema_version"] == INDEX_SCHEMA_VERSION
    manifest["index_schema_version"] = 1
    write_manifest(batch / "manifest.json", manifest)
    with pytest.raises(ValueError, match="import the source again"):
        query_records(batch, kind="all")
