"""Explicit source selection and source-bound browser parser regressions."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import subprocess

import pytest

from test_product_log_standalone_archives import NODE, ROOT, _prepare, _zip
from product_log.configuration import default_config


pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for offline source tests")


def test_offline_outer_directory_contains_nested_and_archived_candidates():
    line = b"[Thu Jan 6 04:36:49 2000] udk sample\n"
    output = _prepare({
        "diagnostic_information/varlog/slot_62/varlog/cpdt_dmesg_current.log": line,
        "diagnostic_information/varlog/slot_62/varlog.zip": _zip({"varlog/cpdt_dmesg_current.log.1": line}),
    }, {"upload_kind": "directory"})
    assert "error" not in output, output
    assert output["result"]["targets"]["kernel"] == "diagnostic_information/varlog"
    assert len(output["result"]["sources"]) == 2


def test_offline_different_branches_remain_ambiguous_even_at_different_depths():
    output = _prepare({"one/varlog/cpdt_dmesg_current.log": b"one",
                       "other/deep/varlog/cpdt_dmesg_current.log": b"two"},
                      {"upload_kind": "directory"})
    assert "不唯一" in output["error"]


def test_offline_empty_explicit_rules_do_not_restore_hardcoded_collection():
    config = {"version": 1, "sources": [], "parsers": [], "rules": [],
              "module_aliases": [], "firmware_types": {}, "modules": []}
    output = _prepare({"varlog/cpdt_dmesg_current.log": b"must skip"},
                      {"analysis_config": config, "upload_kind": "directory"})
    assert "error" not in output, output
    assert output["result"]["sources"] == []


def _configured_kernel_sources():
    config = default_config()
    kernel = next(source for source in config["sources"] if source["id"] == "dmesg")
    second = deepcopy(kernel)
    second.update(id="kbox", name="可选第二来源", file_patterns=["cpdt_kbox*"], file_regex="", parser_id="kbox_iso",
                  order={"type": "name"})
    config["sources"].append(second)
    config["parsers"].append({"id": "kbox_iso", "engine": "kernel", "time_format": "iso",
                              "patterns": {"date": r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)\s+"},
                              "months": []})
    config["rules"] = [{"id": "kernel", "category": "kernel_driver", "component": "udk",
                         "source_ids": ["kbox"], "match": {"field": "raw_text", "operator": "contains", "value": "udk", "ignore_case": True}}]
    return config


def _parse_prepared(result):
    runner = r"""
const fs = require('fs'), vm = require('vm');
globalThis.ProductLogDefaultConfig = JSON.parse(fs.readFileSync('product_log/default_config.json','utf8'));
vm.runInThisContext(fs.readFileSync('product_log/standalone/configuration.js','utf8'));
vm.runInThisContext(fs.readFileSync('product_log/standalone/parser.js','utf8'));
const input = JSON.parse(fs.readFileSync(0,'utf8'));
const batch = ProductLogOfflineParser.parseSources(input.sources,input.analysis_config);
process.stdout.write(JSON.stringify({counts:batch.counts,records:batch.records,segments:batch.segments,mappings:ProductLogOfflineParser.listMappings(batch)}));
"""
    output = subprocess.run([NODE, "-e", runner], input=json.dumps(result), cwd=ROOT,
                            text=True, encoding="utf-8", capture_output=True, check=True, timeout=30)
    return json.loads(output.stdout)


def test_offline_source_option_selects_only_referenced_source_and_its_time_parser():
    config = _configured_kernel_sources()
    output = _prepare({"varlog/cpdt_dmesg_current.log": b"[Thu Jan 6 04:36:49 2000] udk not selected\n",
                       "varlog/cpdt_kbox.log": b"2001-02-03 04:05:06.123456 UDK selected\n2001-02-03 04:05:07 other component\n"},
                      {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in output, output
    result = output["result"]
    assert [source["original_name"] for source in result["sources"]] == ["cpdt_kbox.log"]
    assert result["sources"][0]["configured_source_ids"] == ["kbox"]
    batch = _parse_prepared(result)
    assert len(batch["records"]) == 1
    assert batch["records"][0]["time"] == "2001-02-03 04:05:06.123456"
    assert batch["records"][0]["time_value"] == 981173106123456


def test_offline_explicit_multiselect_preserves_different_sources_and_timestamps():
    config = _configured_kernel_sources()
    config["rules"][0]["source_ids"] = ["dmesg", "kbox"]
    output = _prepare({"varlog/cpdt_dmesg_current.log": b"[Thu Jan 6 04:36:49 2000] udk selected\n",
                       "varlog/cpdt_kbox.log": b"2001-02-03 04:05:06 udk selected\n"},
                      {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in output, output
    records = _parse_prepared(output["result"])["records"]
    assert [record["time"] for record in records] == ["2000-01-06 04:36:49", "2001-02-03 04:05:06"]


def test_offline_shared_file_same_profile_reads_once_and_conflicting_profile_fails():
    config = _configured_kernel_sources()
    second = config["sources"][-1]
    second.update(file_patterns=["cpdt_dmesg_current.log*"], parser_id="dmesg_v1")
    config["rules"][0]["source_ids"] = ["dmesg", "kbox"]
    files = {"varlog/cpdt_dmesg_current.log": b"[Thu Jan 6 04:36:49 2000] udk shared\n"}
    output = _prepare(files, {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in output, output
    assert len(output["result"]["sources"]) == 1
    assert output["result"]["sources"][0]["configured_source_ids"] == ["dmesg", "kbox"]
    assert len(_parse_prepared(output["result"])["records"]) == 1
    second["parser_id"] = "kbox_iso"
    conflicting = _prepare(files, {"upload_kind": "directory", "analysis_config": config})
    assert "不兼容" in conflicting["error"]


def test_offline_firmware_matches_complete_segment_and_preserves_all_continuations():
    config = default_config()
    config["rules"] = [{"id": "firmware-only", "category": "firmware", "component": "IMP",
                         "source_ids": ["diag"], "match": {"field": "raw_text", "operator": "contains", "value": "keep-segment"}}]
    header = "2000-01-06 04:36:49 host %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0; (SlotId=62, CpuId=0, ModuleName=FWD_SDK, Info=Unit[4] firmware logType[1] seg[0]:)"
    ignored_user = "2000-01-06 04:36:50 host %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0; (SlotId=62, CpuId=0, ModuleName=udk, Info=keep-segment user)"
    text = header + "\n[1] first continuation\n[2] keep-segment\n[3] last continuation\n" + ignored_user
    output = _prepare({"diag/diag.log": text.encode()}, {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in output, output
    batch = _parse_prepared(output["result"])
    assert batch["counts"]["driver_records"] == 0
    assert batch["counts"]["firmware_records"] == 3
    assert [record["line_number"] for record in batch["records"]] == [2, 3, 4]
    assert len(batch["segments"]) == 1
    config["rules"] = []
    empty = _prepare({"diag/diag.log": text.encode()}, {"upload_kind": "directory", "analysis_config": config})
    assert _parse_prepared(empty["result"])["counts"]["dumps"] == 0


def test_offline_optional_copy_does_not_bypass_explicit_source_file_selection():
    config = default_config()
    output = _prepare({"diag/diag.log": b"selected", "diag/extra.txt": b"not configured"},
                      {"upload_kind": "directory", "analysis_config": config, "copy_patterns": ["*.txt"]})
    assert "error" not in output, output
    assert [source["original_name"] for source in output["result"]["sources"]] == ["diag.log"]


def test_offline_explicit_directory_literal_is_exact_but_legacy_custom_name_is_fuzzy():
    config = default_config()
    config["sources"][0]["directory_patterns"] = ["diagnostic"]
    config["rules"] = [rule for rule in config["rules"] if rule["category"] == "user_driver"]
    files = {"diagnostics/diag.log": b"user"}
    explicit = _prepare(files, {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in explicit, explicit
    assert explicit["result"]["sources"] == []
    legacy = _prepare(files, {"upload_kind": "directory", "diag_directory_pattern": "diagnostic"})
    assert "error" not in legacy, legacy
    assert [source["original_name"] for source in legacy["result"]["sources"]] == ["diag.log"]


def test_offline_explicit_file_literal_does_not_select_extra_suffix():
    output = _prepare({"diag/diag.log": b"selected", "diag/diag.log.old": b"unconfigured"},
                      {"upload_kind": "directory", "analysis_config": default_config()})
    assert "error" not in output, output
    assert [source["original_name"] for source in output["result"]["sources"]] == ["diag.log"]


def test_offline_generic_without_file_patterns_still_has_valid_explicit_parser_snapshot():
    output = _prepare({"input.txt": b"2000-01-06 04:36:49 host %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0; (SlotId=62, CpuId=0, ModuleName=udk, Info=generic)"},
                      {"upload_kind": "directory", "collection_mode": "generic"})
    assert "error" not in output, output
    assert _parse_prepared(output["result"])["counts"]["driver_records"] == 1


def test_offline_explicit_null_configuration_is_not_an_implicit_default_request():
    output = _prepare({"varlog/cpdt_dmesg_current.log": b"kernel"},
                      {"upload_kind": "directory", "analysis_config": None})
    assert "analysis_config" in output["error"]


def test_offline_source_file_regex_must_match_the_whole_basename():
    config = default_config()
    config["sources"][1]["file_regex"] = "cpdt"
    output = _prepare({"varlog/cpdt_dmesg_current.log": b"kernel"},
                      {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in output, output
    assert output["result"]["sources"] == []


def test_offline_numeric_rotation_rejects_nonnumeric_capture_with_clear_error():
    config = default_config()
    config["sources"][1]["order"]["pattern"] = "^(.+)$"
    output = _prepare({"varlog/cpdt_dmesg_current.log": b"one", "varlog/cpdt_dmesg_current.log.1": b"two"},
                      {"upload_kind": "directory", "analysis_config": config})
    assert "numeric_rotation" in output["error"]


@pytest.mark.parametrize(("date_pattern", "text"), [
    (r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*", "udk prefix 2001-02-03 04:05:06"),
    (r"(2001-02-03 04:05:06)?udk ", "udk missing optional date"),
])
def test_offline_configured_date_matches_only_prefix_and_missing_capture_is_unknown(date_pattern, text):
    config = _configured_kernel_sources()
    config["parsers"][-1]["patterns"]["date"] = date_pattern
    output = _prepare({"varlog/cpdt_kbox.log": text.encode()}, {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in output, output
    record = _parse_prepared(output["result"])["records"][0]
    assert record["time_value"] is None
    assert record["raw_text"] == text


def test_offline_source_identifiers_are_data_even_for_object_property_names():
    config = default_config()
    config["sources"][0]["id"] = "__proto__"
    config["rules"] = [{**rule, "source_ids": ["__proto__"]} for rule in config["rules"] if "diag" in rule["source_ids"]]
    output = _prepare({"diag/diag.log": b"source"}, {"upload_kind": "directory", "analysis_config": config})
    assert "error" not in output, output
    assert output["result"]["source_targets"] == {"__proto__": "diag"}


def test_offline_explicit_nested_sources_do_not_hide_conflicting_parser_bindings():
    config = default_config()
    for source in config["sources"]:
        source.update(file_patterns=["*.log"], file_regex="", order={"type": "name"})
    output = _prepare({"diag/varlog/shared.log": b"shared"},
                      {"upload_kind": "directory", "analysis_config": config})
    assert "不兼容" in output["error"]


@pytest.mark.parametrize(("whitespace", "accepted"), [("\u3000", True), ("\ufeff", True), ("\u0085", False)])
def test_offline_parser_uses_shared_whitespace_semantics_for_fields_and_time(whitespace, accepted):
    text = (f"2000-01-06{whitespace}04:36:49{whitespace}host{whitespace}%%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0; "
            f"(SlotId={whitespace}62, CpuId={whitespace}0, ModuleName=udk, Info=shared whitespace)")
    output = _prepare({"diag/diag.log": text.encode()},
                      {"upload_kind": "directory", "analysis_config": default_config()})
    assert "error" not in output, output
    batch = _parse_prepared(output["result"])
    assert batch["counts"]["driver_records"] == int(accepted)
    if accepted:
        assert batch["records"][0]["time"] == "2000-01-06 04:36:49"
        assert batch["records"][0]["slot_id"] == "62"
