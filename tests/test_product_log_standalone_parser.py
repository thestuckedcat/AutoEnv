"""Execute the shipped browser parser in Node and compare real Python indices."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest

from product_log._storage import INDEX_SCHEMA_VERSION, write_manifest
from product_log.index import build_index, get_source, iter_export, list_diagnostics, list_groups, query_records
from product_log.module_map import list_module_mappings, save_module_mappings
from product_log.parser import parse_file, time_value
from product_log.workflow import run_analysis
from product_log_domain_sample import build_domain_archive


ROOT = Path(__file__).resolve().parents[1]
PARSER = ROOT / "product_log" / "standalone" / "parser.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is needed to verify shipped browser JavaScript")
RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const path = require('path'), root = path.resolve(path.dirname(process.argv[1]), '..');
globalThis.ProductLogDefaultConfig = JSON.parse(fs.readFileSync(path.join(root, 'default_config.json'), 'utf8'));
vm.runInThisContext(fs.readFileSync(path.join(root, 'standalone', 'configuration.js'), 'utf8'));
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const api = ProductLogOfflineParser, batch = api.parseSources(input.sources || [], input.analysis_config);
const output = {counts: batch.counts, results: []};
for (const action of input.actions || []) {
  try {
    const value = action.fn === 'timeValue' ? api.timeValue(action.value) :
      action.fn === 'listGroups' ? api.listGroups(batch, action.kind, action.domain || '') :
      action.fn === 'saveMappings' ? api.saveMappings(batch, action.rows) :
      action.fn === 'sourceContext' ? api.sourceContext(batch, action.source_id, action.options || {}) :
      action.fn === 'listMappings' ? api.listMappings(batch) :
      action.fn === 'queryEachGroup' ? api.listGroups(batch, action.kind).map(group => api.query(batch, {kind: action.kind, group_id: group.group_id})) :
      action.fn === 'dumpPartitions' ? batch.dumps.map(dump => dump.segments.map(row => [row.source_id, row.line_number, row.seg])) :
      api[action.fn](batch, action.options || {});
    output.results.push({value});
  } catch (error) { output.results.push({error: String(error.message)}); }
}
process.stdout.write(JSON.stringify(output));
"""


def browser(sources, actions):
    completed = subprocess.run([NODE, "-e", RUNNER, str(PARSER)], cwd=ROOT,
                               input=json.dumps({"sources": sources, "actions": actions}, ensure_ascii=False),
                               capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def user(body, module="udk log", stamp="2000-01-06 04:36:49.123456", slot=62, cpu=0):
    return (f"{stamp} device %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0; "
            f"(SlotId={slot}, CpuId={cpu}, ModuleName={module}, Info={body})")


def firmware(seg=0, log_type=0, stamp="2000-01-06 04:36:49.100000", body="[1.000][0-0] FW", device=4):
    return user(f"[INFO][HAL_UB]Unit[{device}] firmware logType[{log_type}] seg[{seg}]:", "FWD_SDK", stamp) + "\n" + body


def prepare_batch(tmp_path, files):
    batch = tmp_path / "batch"
    batch.mkdir()
    sources, shards = [], []
    for rank, (name, domain, text) in enumerate(files):
        path = tmp_path / "source" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        source = {"source_id": f"source-{rank}", "source_rank": rank, "path": str(path),
                  "relative_path": name, "origin_path": str(path), "archive_path": "", "member_path": "",
                  "device_source": "fixture-device", "log_domain": domain}
        shard = tmp_path / f"shard-{rank}.sqlite3"
        parse_file(path, shard, source)
        shards.append(shard)
        sources.append({**source, "text": text, "original_name": path.name})
    counts = build_index(batch, shards)
    write_manifest(batch / "manifest.json", {"status": "ready", "index_schema_version": INDEX_SCHEMA_VERSION,
                                            "time_unit": "microseconds", "counts": counts})
    return batch, sources, counts


@pytest.fixture
def mixed(tmp_path):
    first = "\n".join([
        "orphan retained", user("alpha (parentheses)"), user("second", "UDK upgrade", cpu=1),
        user("separate", "udk2"), user("UBE body", "UBE"),
        firmware(body="[1.0] first\n\n[2.0] second"),
        firmware(seg=3, log_type=1, body="[3] partial"),
        user("invalid", stamp="2000-02-30 04:36:49"),
        user("missing slot").replace("SlotId=62", "Missing=62"),
        user("no info", "HBOOT").replace("Info=", "Other="),
    ]) + "\n"
    second = "\n".join([
        firmware(seg=2, stamp="2000-01-06 04:36:48", body="[4] reverse"),
        firmware(seg=2, stamp="2000-01-06 04:36:50", body="[5] duplicate"),
        firmware(seg=0, stamp="2000-01-06 04:36:51", body="[6] new dump"),
        firmware(seg=1, stamp="2000-01-06 04:36:52", body="[7] inline"),
        firmware(log_type=2), firmware(log_type=3), firmware(log_type=9),
    ]) + "\n"
    kernel = "\n".join([
        "udk undated before", "[Thu Jan 6 04:36:49 2000] <thread:1:2> UDK mAmI hit",
        "udk undated after", "[Thu Feb 30 04:36:49 2000] udk invalid date",
        "[Thu Dec 6 04:36:49.987654 2000] HBOOT fractional", "Straße literal A_B%", "",
    ])
    return prepare_batch(tmp_path, [("diag/01.log", "user", first), ("diag/diag.log", "user", second),
                                    ("var/cpdt_dmesg_current.log", "kernel", kernel)])


RECORD_KEYS = ("kind", "driver_domain", "source_id", "source_rank", "line_number", "time", "time_value",
               "slot_id", "cpu_id", "module_name", "raw_module_name", "device_id", "log_type", "seg",
               "firmware_time", "text", "raw_text", "relative_path", "is_context")


def comparable(result):
    return {**{key: result[key] for key in ("total", "has_more", "offset", "limit")},
            "records": [{key: row[key] for key in RECORD_KEYS} for row in result["records"]]}


@pytest.mark.parametrize("options", [
    {"kind": "all"}, {"kind": "all", "view": "stitched", "mode": "raw"},
    {"kind": "driver", "driver_domain": "user", "module_name": "udk upgrade"},
    {"kind": "driver", "driver_domain": "kernel", "slot_id": "999", "cpu_id": "999", "module_name": "missing"},
    {"kind": "driver", "module": "udk"}, {"kind": "driver", "module": "UBE", "mode": "original"},
    {"kind": "firmware", "view": "stitched"}, {"kind": "firmware", "view": "time", "mode": "raw"},
    {"kind": "firmware", "log_type": "0", "device_id": "4"},
    {"kind": "all", "module": "udk", "cpu_id": "0", "log_type": "2", "device_id": "4"},
    {"kind": "all", "driver_domain": "kernel", "module": "udk"},
    {"kind": "all", "module_name": "FWD_SDK"},
    {"kind": "all", "start_time": "2000-01-06T04:36:49", "end_time": "2000-01-06 04:36:49.199999"},
    {"kind": "all", "keyword": "hit", "context_lines": 1, "start_time": "2000-01-06 04:36:49"},
    {"kind": "driver", "driver_domain": "kernel", "module": "udk", "keyword": "hit", "context_lines": 1},
    {"kind": "firmware", "keyword": "Unit[4]", "raw_search": True},
    {"kind": "firmware", "keyword": "Unit[4]", "raw_search": False},
    {"kind": "unclassified"}, {"kind": "all", "offset": 2, "limit": 3},
])
def test_browser_queries_match_production_parser_and_index(mixed, options):
    batch, sources, counts = mixed
    actual = browser(sources, [{"fn": "query", "options": options}, {"fn": "exportText", "options": options}])
    assert actual["counts"] == counts
    assert comparable(actual["results"][0]["value"]) == comparable(query_records(batch, **options))
    assert actual["results"][1]["value"] == "".join(iter_export(batch, **options))


def test_browser_groups_mappings_diagnostics_and_cross_file_partitions(mixed):
    batch, sources, _ = mixed
    actions = [{"fn": "listGroups", "kind": kind, "domain": domain} for kind, domain in
               [("all", ""), ("driver", "user"), ("driver", "kernel"), ("firmware", ""), ("unclassified", "")]]
    actions += [{"fn": "listMappings"}, {"fn": "query", "options": {"kind": "diagnostic"}}, {"fn": "dumpPartitions"}]
    actual = browser(sources, actions)["results"]
    for action, response in zip(actions[:5], actual[:5]):
        normalize = lambda rows: [{key: value for key, value in row.items() if key != "group_id"} for row in rows]
        assert normalize(response["value"]) == normalize(list_groups(batch, action["kind"], action["domain"]))
    assert actual[5]["value"] == list_module_mappings(batch)
    assert sorted((row["code"], row["line_number"]) for row in actual[6]["value"]["records"]) == sorted(
        (row["code"], row["line_number"]) for row in list_diagnostics(batch)["records"])
    with sqlite3.connect(batch / "index.sqlite3") as connection:
        partitions = connection.execute("SELECT dump_id,source_id,line_number,seg FROM segments ORDER BY source_rank,line_number").fetchall()
    expected = {}
    for dump_id, *segment in partitions:
        expected.setdefault(dump_id, []).append(segment)
    assert actual[7]["value"] == list(expected.values())


def test_browser_mapping_edits_apply_without_parsing_or_duplicate_rows(mixed):
    batch, sources, _ = mixed
    rows = [{"module": "udk", "user_module_name": "udk log", "kernel_keyword": "mami"},
            {"module": "missing", "user_module_name": "absent", "kernel_keyword": "udk"},
            {"module": "literal", "user_module_name": "", "kernel_keyword": "a_b%"},
            {"module": "unicode", "user_module_name": "", "kernel_keyword": "STRASSE"}]
    options = [{"kind": "driver", "module": module} for module in ("udk", "missing", "literal", "unicode")]
    actions = [{"fn": "saveMappings", "rows": rows}, *[{"fn": "query", "options": query} for query in options],
               {"fn": "query", "options": {"kind": "all"}}]
    actual = browser(sources, actions)["results"]
    assert actual[0]["value"] == save_module_mappings(batch, rows)
    for response, query in zip(actual[1:5], options):
        assert comparable(response["value"]) == comparable(query_records(batch, **query))
    assert actual[5]["value"]["total"] == query_records(batch, kind="all")["total"]


@pytest.mark.parametrize("options", [{"kind": "all", "limit": 501}, {"kind": "all", "offset": -1},
                                      {"kind": "driver", "driver_domain": "invalid"},
                                      {"kind": "all", "context_lines": 11}, {"kind": "all", "mode": "invalid"},
                                      {"kind": "all", "view": "invalid"},
                                      {"kind": "all", "start_time": "2000-02-30 00:00:00"},
                                      {"kind": "all", "start_time": "2000-01-02 00:00:00", "end_time": "2000-01-01 00:00:00"},
                                      {"kind": "driver", "module": "absent"}])
def test_invalid_browser_query_is_rejected_without_silently_widening_results(mixed, options):
    _, sources, _ = mixed
    assert "error" in browser(sources, [{"fn": "query", "options": options}])["results"][0]


def test_browser_invalid_mapping_does_not_replace_saved_table(mixed):
    _, sources, _ = mixed
    actions = [{"fn": "listMappings"}, {"fn": "saveMappings", "rows": [{"module": "x"}, {"module": "x"}]},
               {"fn": "listMappings"}]
    values = browser(sources, actions)["results"]
    assert "error" in values[1]
    assert values[0] == values[2]


def test_browser_original_source_context_preserves_order_and_empty_firmware_lines(mixed):
    batch, sources, _ = mixed
    actual = browser(sources, [{"fn": "sourceContext", "source_id": "source-0", "options": {"offset": 3, "limit": 6}}])["results"][0]["value"]
    expected = get_source(batch, "source-0", offset=3, limit=6)
    assert actual["records"] == expected["records"]
    assert actual["total"] == expected["total"]
    assert actual["source"]["log_domain"] == "user"


def test_browser_source_context_returns_metadata_without_resending_original_bytes(mixed):
    _, sources, _ = mixed
    sources[0]["bytes"] = [0, 1, 2, 3]
    result = browser(sources, [{"fn": "sourceContext", "source_id": "source-0"}])["results"][0]["value"]
    assert "bytes" not in result["source"] and "text" not in result["source"]
    assert result["lines"][0]["text"] == "orphan retained"


@pytest.mark.parametrize("stamp", ["2000-01-06 04:36:49.123456", "0001-01-01 00:00:00.123456", "9999-12-31 23:59:59.999999", "1969-12-31 23:59:59.999999", "2000-01-06", "2000-01-06T04:36"])
def test_browser_microseconds_are_exact_for_all_supported_device_dates(stamp):
    value = browser([], [{"fn": "timeValue", "value": stamp}])["results"][0]["value"]
    assert int(value) == time_value(stamp)


@pytest.mark.parametrize("kind", ["driver", "firmware", "unclassified"])
def test_browser_group_ids_can_query_every_discovered_group(mixed, kind):
    batch, sources, _ = mixed
    results = browser(sources, [{"fn": "queryEachGroup", "kind": kind}])["results"][0]["value"]
    for response, group in zip(results, list_groups(batch, kind)):
        assert comparable(response) == comparable(query_records(batch, kind=kind, group_id=group["group_id"]))


def test_browser_case_insensitive_search_is_literal_and_supports_raw_firmware_headers(mixed):
    _, sources, _ = mixed
    queries = [{"kind": "driver", "keyword": "ALPHA"},
               {"kind": "driver", "keyword": "ALPHA", "case_sensitive": False},
               {"kind": "driver", "keyword": "STRASSE", "case_sensitive": False},
               {"kind": "driver", "keyword": "a_b%", "case_sensitive": False},
               {"kind": "firmware", "keyword": "unit[4]", "raw_search": True, "case_sensitive": False}]
    values = browser(sources, [{"fn": "query", "options": query} for query in queries])["results"]
    assert [value["value"]["total"] for value in values[:4]] == [0, 1, 1, 1]
    assert values[4]["value"]["total"] > 5


@pytest.mark.parametrize("month,number", [(name, number) for number, name in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)])
def test_browser_all_dmesg_months_ignore_host_locale(tmp_path, month, number):
    batch, sources, _ = prepare_batch(tmp_path, [("cpdt_dmesg_current.log", "kernel", f"[Thu {month} 6 04:36:49.001234 2000] udk body")])
    result = browser(sources, [{"fn": "query", "options": {"kind": "driver"}}])["results"][0]["value"]
    assert comparable(result) == comparable(query_records(batch, kind="driver"))
    assert result["records"][0]["time"] == f"2000-{number:02d}-06 04:36:49.001234"


@pytest.mark.parametrize("text", ["\ufeff" + user("BOM retained in source bytes") + "\r\n",
                                 firmware(body="") + "\n", user("[INFO][HAL_UB]Unit[4] firmware logType[0] seg[0]:[1] inline", "FWD_SDK"),
                                 user("Unit[4] firmware logType[0] seg[2147483648]:", "FWD_SDK"),
                                 user("Unit[4] firmware logType[0] seg[bad]:", "FWD_SDK"), "binary\0payload"])
def test_browser_source_and_segment_edges_match_production(tmp_path, text):
    batch, sources, counts = prepare_batch(tmp_path, [("diag.log", "user", text)])
    actual = browser(sources, [{"fn": "query", "options": {"kind": "all"}},
                              {"fn": "query", "options": {"kind": "unclassified"}},
                              {"fn": "query", "options": {"kind": "diagnostic"}}])
    assert actual["counts"] == counts
    for response, kind in zip(actual["results"][:2], ("all", "unclassified")):
        assert comparable(response["value"]) == comparable(query_records(batch, kind=kind))
    assert [row["code"] for row in actual["results"][2]["value"]["records"]] == [row["code"] for row in list_diagnostics(batch)["records"]]


def test_browser_dense_real_nested_archive_matches_python_end_to_end(tmp_path):
    archive = tmp_path / "product.tar.gz"
    summary = build_domain_archive(archive)
    result = run_analysis(archive, tmp_path / "output", collection_mode="product", diag_directory_pattern="diag/slot62", var_directory_pattern="var")
    batch = Path(result["batch_dir"])
    with sqlite3.connect(batch / "index.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        sources = [dict(row) for row in connection.execute("SELECT * FROM sources ORDER BY source_rank")]
    for source in sources:
        source["text"] = Path(source["path"]).read_text(encoding="utf-8")
    queries = [{"kind": "all", "limit": 25}, {"kind": "driver", "driver_domain": "user", "module": "udk"},
               {"kind": "driver", "driver_domain": "kernel", "module": "udk", "mode": "original"},
               {"kind": "firmware", "device_id": "5", "log_type": "4", "view": "stitched"},
               {"kind": "all", "keyword": "WITHOUT DATE"}]
    actions = [{"fn": "query", "options": query} for query in queries] + [{"fn": "exportText", "options": {"kind": "all"}}, {"fn": "listMappings"}]
    actual = browser(sources, actions)
    assert actual["counts"] == result["counts"]
    assert actual["counts"]["firmware_records"] == summary["firmware_records"] == 3600
    for response, options in zip(actual["results"], queries):
        assert comparable(response["value"]) == comparable(query_records(batch, **options))
    assert actual["results"][-2]["value"] == "".join(iter_export(batch, kind="all"))
    assert actual["results"][-1]["value"] == list_module_mappings(batch)
