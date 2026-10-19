"""All archive layers are prepared before directory selection and parsing."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from product_log import run_analysis
from product_log.index import query_records


def packed(files: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


def user(message: str) -> bytes:
    return ("2000-01-06 04:36:49 host %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;"
            "(SlotId=62, CpuId=0, ModuleName=udk log, "
            f"Info={message})\n").encode()


def kernel(message: str) -> bytes:
    return f"[Thu Jan 6 04:36:49 2000] <thread:1:2> udk {message}\n".encode()


def save(root: Path, name: str, data: bytes) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_varlog_is_primary_and_legacy_var_is_only_absence_fallback(tmp_path):
    root = tmp_path / "input"
    save(root, "diag/diag.log", user("user"))
    save(root, "varlog/cpdt_dmesg_current.log.2", kernel("correct"))
    save(root, "var/cpdt_dmesg_current.log", kernel("legacy must not be chosen"))
    result = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert result["target_paths"]["kernel"] == str(root / "varlog")
    rows = query_records(tmp_path / "batch", kind="driver", driver_domain="kernel")["records"]
    assert len(rows) == 1
    assert "correct" in rows[0]["text"]


def test_directories_hidden_inside_two_archive_layers_are_found(tmp_path):
    root = tmp_path / "input"
    save(root, "reports/outer.zip", packed({"nested/inner.zip": packed({
        "node/diag/diag.log": user("hidden user"),
        "node/varlog/cpdt_dmesg_current.log.1": kernel("hidden kernel"),
    })}))
    before = hashlib.sha256((root / "reports/outer.zip").read_bytes()).hexdigest()
    result = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert result["counts"]["driver_records"] == 2
    assert result["matched_archives"] == 2
    assert {tuple(item["provenance_parts"]) for item in result["sources"]} == {
        ("reports/outer.zip", "nested/inner.zip", "node/diag/diag.log"),
        ("reports/outer.zip", "nested/inner.zip", "node/varlog/cpdt_dmesg_current.log.1"),
    }
    assert hashlib.sha256((root / "reports/outer.zip").read_bytes()).hexdigest() == before


def test_diag_and_generic_expand_all_layers_and_unselected_archives_before_parsing(tmp_path):
    root = tmp_path / "input"
    save(root, "diag/outer.zip", packed({"inner.zip": packed({"diag.log": user("deep")})}))
    save(root, "unrelated/deep.zip", packed({"readme.txt": b"fully expanded before parse"}))
    result = run_analysis(root, tmp_path / "product", collection_mode="product",
                          archive_patterns=["*.does-not-match"])
    assert result["counts"]["driver_records"] == 1
    assert result["matched_archives"] == 3
    assert any((Path(item["destination"]) / "readme.txt").exists() for item in result["archives"])
    generic = run_analysis(root, tmp_path / "generic", collection_mode="generic", file_patterns=["*.log"])
    assert generic["counts"]["driver_records"] == 1
    assert generic["matched_archives"] == 3


def test_duplicate_directories_discovered_in_archives_still_require_partial_path(tmp_path):
    root = tmp_path / "input"
    save(root, "one.zip", packed({"first/diag/diag.log": user("first")}))
    save(root, "two.zip", packed({"second/diag/diag.log": user("second")}))
    with pytest.raises(ValueError, match="ambiguous directory"):
        run_analysis(root, tmp_path / "ambiguous", collection_mode="product")
    result = run_analysis(root, tmp_path / "unique", collection_mode="product",
                          diag_directory_pattern="first/diag")
    assert result["counts"]["driver_records"] == 1
    assert "first" in query_records(tmp_path / "unique", kind="driver")["records"][0]["text"]


def test_nested_zip_lzma_method_14_extracts_in_python_collection(tmp_path):
    root = tmp_path / "input"
    save(root, "outer.zip", packed({"inside.zip": packed({"diag/diag.log": user("LZMA")}, zipfile.ZIP_LZMA)}))
    result = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert result["counts"]["driver_records"] == 1
    assert result["matched_archives"] == 2
