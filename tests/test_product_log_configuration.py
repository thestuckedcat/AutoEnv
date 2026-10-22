"""Explicit source configuration is shared with the shipped browser validator."""
from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def api():
    return importlib.import_module("product_log.configuration")


def empty_config():
    return {"version": 1, "sources": [], "parsers": [], "rules": [],
            "module_aliases": [], "firmware_types": {}, "modules": []}


def javascript(action, config=None, **values):
    if NODE is None:
        pytest.skip("Node.js is needed for configuration parity")
    runner = r"""
const fs=require('node:fs'),vm=require('node:vm');
globalThis.ProductLogDefaultConfig=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
vm.runInThisContext(fs.readFileSync(process.argv[2],'utf8'));
const req=JSON.parse(fs.readFileSync(0,'utf8')),api=ProductLogConfig;
try {
  const config=api.normalize(req.config);
  let value=config;
  if(req.action==='active')value=api.activeSources(config).map(source=>source.id);
  if(req.action==='record')value=api.recordEnabled(config,req.source_ids,req.category,req.metadata||{},req.raw||'',req.body||'');
  if(req.action==='module')value=api.normalizeModule(req.value,config);
  if(req.action==='firmware')value=api.firmwareTypeName(req.value,config);
  if(req.action==='regex')value=api.compileRegex(req.pattern,!!req.ignore_case).test(req.value);
  process.stdout.write(JSON.stringify({value}));
}catch(error){process.stdout.write(JSON.stringify({error:String(error.message)}));}
"""
    result = subprocess.run([NODE, "-e", runner, str(ROOT / "product_log/default_config.json"),
                             str(ROOT / "product_log/standalone/configuration.js")],
                            input=json.dumps({"action": action, "config": config, **values}),
                            capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)
    return json.loads(result.stdout)


def test_defaults_are_explicit_extracts_and_independent():
    config = api().default_config()
    assert api().normalize_config() == config
    assert {source["id"] for source in config["sources"]} == {"diag", "dmesg"}
    dmesg = next(source for source in config["sources"] if source["id"] == "dmesg")
    assert dmesg["directory_patterns"] == ["varlog", "var"]
    assert dmesg["file_regex"] == r"^cpdt_dmesg_current\.log(?:\.(\d+))?$"
    assert {rule["category"] for rule in config["rules"]} == {"user_driver", "kernel_driver", "firmware"}
    assert all(rule["match"]["operator"] == "any" for rule in config["rules"])
    assert config["firmware_types"] == {"0": "bios", "1": "IMP", "2": "IMU", "3": "HSM"}
    config["sources"].clear()
    assert len(api().default_config()["sources"]) == 2


def test_empty_configuration_and_removed_rules_do_not_restore_defaults():
    config = api().normalize_config(empty_config())
    assert api().active_sources(config) == []
    assert not api().record_enabled(config, ["dmesg"], "kernel_driver", {}, "udk", "udk")
    config = api().default_config()
    config["rules"] = []
    assert api().active_sources(api().normalize_config(config)) == []
    assert javascript("active", empty_config()) == {"value": []}


def test_source_choices_enable_only_explicitly_selected_sources():
    config = api().default_config()
    extra = copy.deepcopy(config["sources"][1])
    extra.update(id="kbox", name="Kbox", file_patterns=["cpdt_kbox*"], file_regex="")
    config["sources"].append(extra)
    config["rules"] = [dict(config["rules"][1], source_ids=["kbox"])]
    # Select the kernel rule independently of the default array order.
    config["rules"][0]["category"] = "kernel_driver"
    normalized = api().normalize_config(config)
    assert [source["id"] for source in api().active_sources(normalized)] == ["kbox"]
    assert javascript("active", normalized) == {"value": ["kbox"]}


@pytest.mark.parametrize("operator,value,values,expected", [
    ("any", "", [], True), ("equals", "UDK ready", [], True),
    ("contains", "UDK", [], True), ("in", "", ["udk READY", "other"], True),
    ("regex", r"^udk\s+ready$", [], True), ("contains", "mami", [], False),
])
def test_completed_record_matching_has_python_javascript_parity(operator, value, values, expected):
    config = api().default_config()
    rule = next(rule for rule in config["rules"] if rule["category"] == "kernel_driver")
    config["rules"] = [rule]
    rule["match"] = {"field": "raw_text", "operator": operator, "value": value,
                     "values": values, "ignore_case": True}
    normalized = api().normalize_config(config)
    assert api().record_enabled(normalized, ["dmesg"], "kernel_driver", {}, "udk ready", "body") is expected
    assert javascript("record", normalized, source_ids=["dmesg"], category="kernel_driver",
                      raw="udk ready", body="body") == {"value": expected}
    assert not api().record_enabled(normalized, ["diag"], "kernel_driver", {}, "udk ready", "body")


def test_component_fields_and_module_aliases_are_explicit():
    config = api().default_config()
    assert api().normalize_module(" UDK log ", config) == "udk"
    assert api().normalize_module("udk2", config) == "udk2"
    config["module_aliases"] = []
    assert api().normalize_module(" UDK log ", config) == "UDK log"
    assert javascript("module", config, value=" UDK log ") == {"value": "UDK log"}
    config["firmware_types"] = {"0": "custom"}
    assert api().firmware_type_name("0", config) == "custom"
    assert javascript("firmware", config, value="0") == {"value": "custom"}


def test_empty_source_selection_disables_rule_without_removing_definition():
    config = api().default_config()
    for rule in config["rules"]:
        rule["source_ids"] = []
    normalized = api().normalize_config(config)
    assert api().active_sources(normalized) == []
    assert javascript("active", normalized) == {"value": []}


def test_unicode_literal_matching_and_numeric_version_have_browser_parity():
    config = api().default_config()
    config["version"] = 1.0
    rule = next(rule for rule in config["rules"] if rule["category"] == "kernel_driver")
    config["rules"] = [rule]
    rule["match"] = {"field": "raw_text", "operator": "contains", "value": "STRASSE", "ignore_case": True}
    normalized = api().normalize_config(config)
    assert api().record_enabled(normalized, ["dmesg"], "kernel_driver", {}, "Straße ready", "")
    assert javascript("record", normalized, source_ids=["dmesg"], category="kernel_driver", raw="Straße ready") == {"value": True}


@pytest.mark.parametrize("space", ["\u00a0", "\u3000", "\ufeff"])
def test_regex_whitespace_and_aliases_share_unicode_whitespace(space):
    config = api().default_config()
    module = "UDK" + space + "log"
    assert api().normalize_module(module, config) == "udk"
    assert javascript("module", config, value=module) == {"value": "udk"}
    rule = next(rule for rule in config["rules"] if rule["category"] == "kernel_driver")
    config["rules"] = [rule]
    for expression, expected in [(r"^A\sB$", True), (r"^A[\s]+B$", True), (r"^\S+$", False), (r"^[\s\S]+$", True)]:
        rule["match"] = {"field": "raw_text", "operator": "regex", "value": expression}
        normalized = api().normalize_config(config)
        assert api().record_enabled(normalized, ["dmesg"], "kernel_driver", {}, "A" + space + "B", "") is expected
        assert javascript("record", normalized, source_ids=["dmesg"], category="kernel_driver", raw="A" + space + "B") == {"value": expected}


def test_none_metadata_matches_empty_string_in_both_engines():
    config = api().default_config()
    rule = next(rule for rule in config["rules"] if rule["category"] == "kernel_driver")
    config["rules"] = [rule]
    rule["match"] = {"field": "module_name", "operator": "equals", "value": "None"}
    normalized = api().normalize_config(config)
    assert not api().record_enabled(normalized, ["dmesg"], "kernel_driver", {"module_name": None}, "", "")
    assert javascript("record", normalized, source_ids=["dmesg"], category="kernel_driver", metadata={"module_name": None}) == {"value": False}


@pytest.mark.parametrize("aliases", [
    [{"pattern": "^A$", "replacement": "B", "ignore_case": False},
     {"pattern": "^B$", "replacement": "C", "ignore_case": False}],
    [{"pattern": "^A$", "replacement": "B", "ignore_case": False},
     {"pattern": "^B$", "replacement": "A", "ignore_case": False}],
    [{"pattern": "^A$", "replacement": " B ", "ignore_case": False}],
])
def test_noncanonical_alias_targets_are_rejected(aliases):
    config = api().default_config()
    config["module_aliases"] = aliases
    with pytest.raises(ValueError, match="canonical"):
        api().normalize_config(config)
    result = javascript("normalize", config)
    assert "canonical" in result["error"]


def test_alias_targets_may_match_an_explicit_idempotent_rule():
    config = api().default_config()
    config["module_aliases"] = [{"pattern": "^A$", "replacement": "B", "ignore_case": False},
                                {"pattern": "^B$", "replacement": "B", "ignore_case": False}]
    normalized = api().normalize_config(config)
    assert api().normalize_module("A", normalized) == "B"
    assert api().normalize_module("B", normalized) == "B"
    assert javascript("module", normalized, value="A") == {"value": "B"}


@pytest.mark.parametrize("expression,value,expected", [
    (r"^\d+$", "123", True), (r"^\d+$", "١٢٣", False),
    (r"^\w+$", "abc_123", True), (r"^\w+$", "组件", False),
    (r"^[\S]+$", "ABC", True), (r"^[\S]+$", "A\u3000B", False),
    (r"^[^\S]+$", "\u00a0\u3000", True),
    (r"^\s+$", "\u0085", False), (r"^\S+$", "\u0085", True),
    (r"^\s+$", "\u001c", False), (r"^\s+$", "\ufeff", True),
])
def test_exported_regex_compiler_keeps_shared_whitespace_and_ascii_word_sets(expression, value, expected):
    assert bool(api().compile_regex(expression).search(value)) is expected
    assert javascript("regex", api().default_config(), pattern=expression, value=value) == {"value": expected}


@pytest.mark.parametrize("change", [
    "missing_version", "unknown_field", "bad_version", "duplicate_source", "duplicate_parser", "duplicate_rule",
    "missing_parser", "missing_source", "invalid_engine", "invalid_category", "incompatible_category",
    "unsafe_directory", "unsafe_glob", "invalid_regex", "named_regex", "missing_capture",
    "invalid_match", "missing_match_value", "invalid_order", "invalid_recursive", "bad_modules", "unknown_escape", "backreference",
    "invalid_capture_count_kernel_iso", "invalid_capture_count_kernel_dmesg", "invalid_diag_time_format",
])
def test_invalid_configurations_fail_consistently(change):
    config = api().default_config()
    kernel = next(rule for rule in config["rules"] if rule["category"] == "kernel_driver")
    if change == "missing_version": config.pop("version")
    elif change == "unknown_field": config["unknown"] = True
    elif change == "bad_version": config["version"] = True
    elif change == "duplicate_source": config["sources"].append(copy.deepcopy(config["sources"][0]))
    elif change == "duplicate_parser": config["parsers"].append(copy.deepcopy(config["parsers"][0]))
    elif change == "duplicate_rule": config["rules"].append(copy.deepcopy(config["rules"][0]))
    elif change == "missing_parser": config["sources"][0]["parser_id"] = "missing"
    elif change == "missing_source": kernel["source_ids"] = ["missing"]
    elif change == "invalid_engine": config["parsers"][0]["engine"] = "missing"
    elif change == "invalid_category": kernel["category"] = "other"
    elif change == "incompatible_category": kernel["source_ids"] = ["diag"]
    elif change == "unsafe_directory": config["sources"][0]["directory_patterns"] = ["../diag"]
    elif change == "unsafe_glob": config["sources"][0]["file_patterns"] = ["../*.log"]
    elif change == "invalid_regex": config["sources"][0]["file_regex"] = "["
    elif change == "named_regex": config["sources"][0]["file_regex"] = "(?P<name>log)"
    elif change == "missing_capture": config["parsers"][0]["patterns"]["header"] = "^log$"
    elif change == "invalid_match": kernel["match"]["operator"] = "script"
    elif change == "missing_match_value": kernel["match"] = {"field": "raw_text", "operator": "contains"}
    elif change == "invalid_order": config["sources"][0]["order"]["type"] = "random"
    elif change == "invalid_recursive": config["sources"][0]["recursive"] = "yes"
    elif change == "bad_modules": config["modules"][0]["module"] = ""
    elif change == "unknown_escape": kernel["match"] = {"field": "raw_text", "operator": "regex", "value": r"\q"}
    elif change == "backreference": kernel["match"] = {"field": "raw_text", "operator": "regex", "value": r"(udk)\1"}
    elif change == "invalid_capture_count_kernel_iso":
        config["parsers"][1].update(time_format="iso", patterns={"date": "^log$"})
    elif change == "invalid_capture_count_kernel_dmesg": config["parsers"][1]["patterns"]["date"] = "^log$"
    elif change == "invalid_diag_time_format": config["parsers"][0]["time_format"] = "dmesg"
    with pytest.raises((TypeError, ValueError)):
        api().normalize_config(config)
    assert "error" in javascript("normalize", config)


def test_defaults_match_browser_and_normalization_never_mutates_input():
    config = api().default_config()
    before = copy.deepcopy(config)
    assert api().normalize_config(config) == before
    assert config == before
    assert javascript("normalize", config) == {"value": config}
