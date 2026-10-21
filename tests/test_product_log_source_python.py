"""Configured sources, parser bindings, and ancestor directory selection."""
import io
import json
import zipfile

import pytest

from product_log import run_analysis
from product_log.index import query_records
from product_log.parser import kernel_timestamp


KERNEL = "[Thu Jan 6 04:36:49 2000] udk kernel\n"


def save(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode() if isinstance(text, str) else text)


def packed(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return stream.getvalue()


def config():
    from product_log.configuration import default_config
    return default_config()


def kernel_config():
    value = config()
    source = next(s for s in value["sources"] if s["parser_id"] in {
        p["id"] for p in value["parsers"] if p["engine"] == "kernel"})
    value["sources"] = [source]
    value["rules"] = [r for r in value["rules"] if r["category"] == "kernel_driver"]
    return value, source


def test_outer_varlog_covers_plain_and_archive_descendants(tmp_path):
    root = tmp_path / "input"
    prefix = "diagnostic_information/varlog"
    save(root, prefix + "/cpdt_dmesg_current.log", KERNEL)
    save(root, prefix + "/slot_62/varlog/cpdt_dmesg_current.log.1", KERNEL)
    save(root, prefix + "/slot_62/varlog.zip", packed({"varlog/cpdt_dmesg_current.log.2": KERNEL}))
    result = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert result["target_paths"]["kernel"] == str(root / prefix)
    assert result["matched_files"] == 3


def test_unrelated_shallower_directory_stays_ambiguous(tmp_path):
    root = tmp_path / "input"
    save(root, "a/varlog/cpdt_dmesg_current.log", KERNEL)
    save(root, "b/deep/varlog/cpdt_dmesg_current.log", KERNEL)
    with pytest.raises(ValueError, match="ambiguous directory"):
        run_analysis(root, tmp_path / "batch", collection_mode="product")


def test_base_directory_precedes_unrelated_nested_archive_directory(tmp_path):
    root = tmp_path / "input"
    save(root, "varlog/cpdt_dmesg_current.log", KERNEL)
    save(root, "other.zip", packed({"varlog/cpdt_dmesg_current.log": KERNEL}))
    result = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert result["matched_files"] == 1


def test_empty_explicit_rules_never_restore_defaults(tmp_path):
    root = tmp_path / "input"
    save(root, "varlog/cpdt_dmesg_current.log", KERNEL)
    value = config()
    value["rules"] = []
    result = run_analysis(root, tmp_path / "batch", collection_mode="product", analysis_config=value)
    assert result["matched_files"] == result["counts"]["driver_records"] == 0
    assert result["analysis_config"]["rules"] == []


def test_rule_selects_only_kbox_with_its_own_iso_parser(tmp_path):
    root = tmp_path / "input"
    save(root, "varlog/cpdt_dmesg_current.log", KERNEL)
    save(root, "varlog/cpdt_kbox.log", "2000-01-06 04:36:50.123456 udk selected\n2000-01-06 04:36:51 other skip\n")
    value, original = kernel_config()
    profile = json.loads(json.dumps(next(p for p in value["parsers"] if p["id"] == original["parser_id"])))
    profile.update(id="kbox_iso", time_format="iso")
    profile["patterns"]["date"] = r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\s*"
    value["parsers"].append(profile)
    value["sources"].append(dict(original, id="kbox", name="kbox", parser_id="kbox_iso",
                                  file_patterns=["cpdt_kbox.log"], file_regex="", order={"type": "name", "current": "", "pattern": ""}))
    value["rules"] = [{"id": "selected_udk", "category": "kernel_driver", "component": "udk",
                         "source_ids": ["kbox"], "match": {"field": "raw_text", "operator": "contains", "value": "udk", "ignore_case": True}}]
    batch = tmp_path / "batch"
    result = run_analysis(root, batch, collection_mode="product", analysis_config=value)
    assert result["matched_files"] == 1
    records = query_records(batch, kind="all")["records"]
    assert len(records) == 1
    assert records[0]["time"] == "2000-01-06 04:36:50.123456"
    assert records[0]["line_number"] == 1


def test_same_file_with_different_bound_parsers_is_explicit_conflict(tmp_path):
    root = tmp_path / "input"
    save(root, "varlog/cpdt_dmesg_current.log", KERNEL)
    value, source = kernel_config()
    profile = json.loads(json.dumps(next(p for p in value["parsers"] if p["id"] == source["parser_id"])))
    profile.update(id="other_parser", time_format="none")
    value["parsers"].append(profile)
    value["sources"].append(dict(source, id="second", parser_id="other_parser"))
    value["rules"][0]["source_ids"].append("second")
    with pytest.raises(ValueError, match="[Pp]arser.*conflict|[Cc]onflicting.*parser"):
        run_analysis(root, tmp_path / "batch", collection_mode="product", analysis_config=value)


def test_firmware_matching_segment_header_keeps_its_continuations(tmp_path):
    root = tmp_path / "input"
    header = ("2000-01-06 04:36:49 host %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;"
              "(SlotId=62, CpuId=0, ModuleName=FWD, Unit[4] firmware logType[1] seg[0]: ")
    save(root, "diag/diag.log", header + "\n[1.000] first body\n[2.000] second body\n")
    value = config()
    source = next(s for s in value["sources"] if s["parser_id"] in {
        p["id"] for p in value["parsers"] if p["engine"] == "diag"})
    value["rules"] = [{"id": "firmware_only", "category": "firmware", "component": "IMP",
                         "source_ids": [source["id"]], "match": {"field": "raw_text", "operator": "contains", "value": "logType[1]"}}]
    batch = tmp_path / "batch"
    run_analysis(root, batch, collection_mode="product", analysis_config=value)
    records = query_records(batch, kind="firmware")["records"]
    assert [r["line_number"] for r in records] == [2, 3]


def test_empty_source_selection_does_not_read_unconfigured_files(tmp_path):
    root = tmp_path / "input"
    save(root, "diag/diag.log", "unconfigured")
    value = config()
    value["sources"] = []
    value["rules"] = []
    result = run_analysis(root, tmp_path / "batch", collection_mode="product", analysis_config=value)
    assert result["matched_files"] == 0


def test_copy_patterns_cannot_enable_files_excluded_by_explicit_sources(tmp_path):
    root = tmp_path / "input"
    user = "2000-01-06 04:36:49 host %%01X: (SlotId=62, CpuId=0, ModuleName=udk, Info=sample)\n"
    save(root, "diag/diag.log", user)
    save(root, "diag/extra.txt", user)
    result = run_analysis(root, tmp_path / "batch", collection_mode="product", analysis_config=config(),
                          copy_patterns=["extra.txt"])
    assert result["matched_files"] == 1


def test_firmware_parser_without_absolute_time_still_stitches_segments(tmp_path):
    root = tmp_path / "input"
    prefix = "2000-01-06 04:36:49 host %%01X: (SlotId=62, CpuId=0, ModuleName=FWD, Unit[4] firmware logType[1] seg["
    save(root, "diag/diag.log", prefix + "0]:\n[1] first\n" + prefix + "1]:\n[2] second\n")
    value = config()
    for profile in value["parsers"]:
        if profile["engine"] == "diag":
            profile["time_format"] = "none"
    batch = tmp_path / "batch"
    result = run_analysis(root, batch, collection_mode="product", analysis_config=value)
    assert result["counts"]["firmware_records"] == 2
    assert result["counts"]["dumps"] == 1
    assert all(r["time_value"] is None for r in query_records(batch, kind="firmware")["records"])


def test_optional_configured_date_capture_is_unknown_instead_of_crashing():
    value = config()
    profile = next(p for p in value["parsers"] if p["engine"] == "kernel")
    profile.update(time_format="iso", patterns={"date": r"^optional-(\d{4}-\d{2}-\d{2})?$"})
    assert kernel_timestamp("optional-", profile) == ("", None, "optional-")


def test_explicit_nested_sources_with_incompatible_engines_report_conflict(tmp_path):
    root = tmp_path / "input"
    save(root, "diag/varlog/shared.log", KERNEL)
    value = config()
    for source in value["sources"]:
        source.update(file_patterns=["*.log"], file_regex="", order={"type": "name"})
    with pytest.raises(ValueError, match="[Pp]arser.*conflict"):
        run_analysis(root, tmp_path / "batch", collection_mode="product", analysis_config=value)


def test_configured_digit_regex_does_not_select_unicode_rotation_digits(tmp_path):
    root = tmp_path / "input"
    save(root, "varlog/cpdt_dmesg_current.log.١", KERNEL)
    result = run_analysis(root, tmp_path / "batch", collection_mode="product", analysis_config=config())
    assert result["matched_files"] == 0


def test_numeric_rotation_rejects_nonnumeric_configured_capture(tmp_path):
    root = tmp_path / "input"
    save(root, "varlog/cpdt_dmesg_current.log.bad", KERNEL)
    value, source = kernel_config()
    source["file_regex"] = ""
    source["order"]["pattern"] = r"^cpdt_dmesg_current\.log\.([a-z]+)$"
    with pytest.raises(ValueError, match="numeric_rotation"):
        run_analysis(root, tmp_path / "batch", collection_mode="product", analysis_config=value)
