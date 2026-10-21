"""Real offline collection expands the complete archive forest before selection."""

from test_product_log_standalone_archives import _prepare, _zip


def test_varlog_is_selected_before_legacy_var():
    output = _prepare({"node/varlog/cpdt_dmesg_current.log": b"correct varlog",
                       "node/var/cpdt_dmesg_current.log": b"legacy"})
    assert "error" not in output, output
    assert [source["text"] for source in output["result"]["sources"]] == ["correct varlog"]
    assert output["result"]["targets"]["kernel"] == "node/varlog"


def test_hidden_product_directories_are_discovered_after_every_archive_layer():
    output = _prepare({"outer.zip": _zip({"middle/inner.zip": _zip({
        "node/diag/diag.log": b"hidden user",
        "node/varlog/cpdt_dmesg_current.log.1": b"hidden kernel",
    })})})
    assert "error" not in output, output
    result = output["result"]
    assert [source["text"] for source in result["sources"]] == ["hidden user", "hidden kernel"]
    assert result["counts"]["archives"] == 2
    assert result["sources"][0]["provenance_parts"] == ["middle/inner.zip", "node/diag/diag.log"]
    assert result["input_unchanged"]


def test_nested_diag_and_unrelated_archives_are_all_expanded_before_filtering():
    output = _prepare({
        "diag/outer.zip": _zip({"inner.zip": _zip({"diag.log": b"deep user"})}),
        "unrelated/other.zip": _zip({"readme.txt": b"prepare this too"}),
    }, {"upload_kind": "directory", "archive_patterns": ["*.does-not-match"]})
    assert "error" not in output, output
    result = output["result"]
    assert [source["text"] for source in result["sources"]] == ["deep user"]
    assert result["counts"]["archives"] == 3
    assert len(result["archives"]) == 3
    assert result["input_unchanged"]


def test_hidden_directory_ambiguity_requires_a_unique_partial_path():
    files = {"one.zip": _zip({"first/diag/diag.log": b"first"}),
             "two.zip": _zip({"second/diag/diag.log": b"second"})}
    ambiguous = _prepare(files, {"upload_kind": "directory"})
    assert "不唯一" in ambiguous["error"]
    output = _prepare(files, {"upload_kind": "directory", "diag_directory_pattern": "first/diag"})
    assert "error" not in output, output
    assert [source["text"] for source in output["result"]["sources"]] == ["first"]
    assert output["result"]["counts"]["archives"] == 2


def test_generic_directory_can_be_located_inside_nested_archives():
    output = _prepare({"wrapper.zip": _zip({"inner.zip": _zip({"wanted/input.log": b"generic"})})},
                      {"collection_mode": "generic", "directory_pattern": "wanted", "file_patterns": ["*.log"]})
    assert "error" not in output, output
    assert [source["text"] for source in output["result"]["sources"]] == ["generic"]
    assert output["result"]["counts"]["archives"] == 2


def test_legacy_var_and_explicit_directory_choice_remain_usable():
    legacy = _prepare({"root/var/cpdt_dmesg_current.log": b"legacy"})
    assert [source["text"] for source in legacy["result"]["sources"]] == ["legacy"]
    chosen = _prepare({"root/varlog/cpdt_dmesg_current.log": b"new",
                       "root/var/cpdt_dmesg_current.log": b"explicit"}, {"var_directory_pattern": "var"})
    assert [source["text"] for source in chosen["result"]["sources"]] == ["explicit"]


def test_partial_directory_components_span_archive_packing_boundaries():
    output = _prepare({"node/diag/slot62.zip": _zip({"slot62/diag.log": b"matched"})},
                      {"upload_kind": "directory", "diag_directory_pattern": "diag/slot62"})
    assert "error" not in output, output
    assert [source["text"] for source in output["result"]["sources"]] == ["matched"]
    assert output["result"]["sources"][0]["relative_path"] == "node/diag/slot62.zip!/slot62/diag.log"


def test_literal_archive_boundary_directory_is_distinct_from_packed_directory():
    files = {"outer.zip": _zip({"diag/diag.log": b"packed"}),
             "outer.zip!/diag/diag.log": b"literal"}
    preferred = _prepare(files, {"upload_kind": "directory"})
    assert "error" not in preferred, preferred
    assert [source["text"] for source in preferred["result"]["sources"]] == ["literal"]
    assert preferred["result"]["sources"][0]["provenance_parts"] == ["outer.zip!/diag/diag.log"]
    selected = _prepare(files, {"upload_kind": "directory", "diag_directory_pattern": "outer.zip!/diag"})
    assert "error" not in selected, selected
    assert [source["text"] for source in selected["result"]["sources"]] == ["literal"]


def test_nonrecursive_selection_preserves_parent_directory_depth_across_archives():
    output = _prepare({"diag/diag.log": b"direct", "diag/sub/inner.zip": _zip({"diag.log": b"nested directory"})},
                      {"upload_kind": "directory", "recursive": False})
    assert "error" not in output, output
    assert [source["text"] for source in output["result"]["sources"]] == ["direct"]
    assert output["result"]["counts"]["archives"] == 1
