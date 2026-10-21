"""Real local product directories and nested archives verify collection semantics."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from product_log import AnalysisCancelled, run_analysis
from product_log.__main__ import main
from product_log.index import query_records


def user_line(message: str) -> str:
    return ("2000-01-06 04:36:49 host %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;"
            "(SlotId=62, CpuId=0, ModuleName=UBE, Info=" + message + ")\n")


def kernel_line(message: str) -> str:
    return "[Thu Jan 6 04:36:49 2000] <LpoThread_u10_p:25170:28546> " + message + "\n"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def tar_bytes(files: dict[str, bytes], mode: str = "w:gz") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def file_names(manifest: dict, domain: str) -> list[str]:
    return [source["original_name"] for source in manifest["sources"] if source["log_domain"] == domain]


def tree_digest(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def test_product_selects_both_domains_and_business_file_order(tmp_path):
    root = tmp_path / "input"
    for name in ("diag.log", "diaglog_62_20000106043418.log", "diaglog_62_20000106043416.log"):
        write(root / "node/diag" / name, user_line(name))
    write(root / "node/diag" / "unrelated.log", user_line("ignore"))
    write(root / "node/diag" / "diag.log.old", user_line("ignore backup"))
    for name in ("cpdt_dmesg_current.log", "cpdt_dmesg_current.log.10",
                 "cpdt_dmesg_current.log.2", "cpdt_dmesg_current.log.1"):
        write(root / "node/var/log" / name, kernel_line(name))
    write(root / "node/var/log" / "cpdt_dmesg_current.log.old", kernel_line("ignore"))
    before = tree_digest(root)
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product", workers=4)
    assert manifest["collection_mode"] == "product"
    assert manifest["target_paths"] == {"user": str(root / "node/diag"), "kernel": str(root / "node/var")}
    assert file_names(manifest, "user") == ["diaglog_62_20000106043416.log", "diaglog_62_20000106043418.log", "diag.log"]
    assert file_names(manifest, "kernel") == ["cpdt_dmesg_current.log.1", "cpdt_dmesg_current.log.2",
                                            "cpdt_dmesg_current.log.10", "cpdt_dmesg_current.log"]
    assert [source["source_rank"] for source in manifest["sources"]] == list(range(7))
    assert manifest["counts"]["files"] == 7
    assert tree_digest(root) == before


def test_var_recursively_expands_real_zip_tar_gzip_with_complete_provenance(tmp_path):
    leaf = gzip.compress(kernel_line("UDK deepest").encode())
    middle = tar_bytes({"logs/cpdt_dmesg_current.log.10.gz": leaf,
                        "logs/cpdt_dmesg_current.log.2": kernel_line("mami middle").encode()})
    archive = tmp_path / "product.tar.gz"
    archive.write_bytes(tar_bytes({"diag/diag.log": user_line("user newest").encode(),
                                  "var/outer.zip": zip_bytes({"inner.tar.gz": middle}),
                                  "var/cpdt_dmesg_current.log": kernel_line("kernel newest").encode()}))
    before = archive.read_bytes()
    manifest = run_analysis(archive, tmp_path / "batch", collection_mode="product",
                            archive_patterns=["*.does-not-match"], file_patterns=["*.log"])
    assert file_names(manifest, "kernel") == ["cpdt_dmesg_current.log.2", "cpdt_dmesg_current.log.10", "cpdt_dmesg_current.log"]
    deepest = next(source for source in manifest["sources"] if source["original_name"].endswith(".10"))
    assert deepest["provenance_parts"] == ["var/outer.zip", "inner.tar.gz",
                                           "logs/cpdt_dmesg_current.log.10.gz", "cpdt_dmesg_current.log.10"]
    assert deepest["origin_path"] == str(archive) + "!/" + "!/".join(deepest["provenance_parts"])
    assert Path(deepest["path"]).read_text() == kernel_line("UDK deepest")
    assert [item["level"] for item in manifest["archives"]] == [0, 1, 2, 3]
    assert [item["format"] for item in manifest["archives"]] == ["tar", "zip", "tar", "gz"]
    assert manifest["matched_archives"] == 3
    assert manifest["expanded_files"] == 7
    assert archive.read_bytes() == before


def test_diag_recursively_expands_all_child_archives(tmp_path):
    root = tmp_path / "input"
    (root / "diag").mkdir(parents=True)
    (root / "diag/payload.zip").write_bytes(zip_bytes({
        "diaglog_62_20000106043418.log": user_line("historical").encode(),
        "inner.zip": zip_bytes({"diag.log": user_line("nested latest").encode()}),
    }))
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert file_names(manifest, "user") == ["diaglog_62_20000106043418.log", "diag.log"]
    assert manifest["matched_archives"] == 2
    assert manifest["counts"]["driver_records"] == 2


@pytest.mark.parametrize("domain", ["user", "kernel"])
def test_product_accepts_one_domain_missing(tmp_path, domain):
    root = tmp_path / "input"
    if domain == "user":
        write(root / "diag/diag.log", user_line("only user"))
    else:
        write(root / "var/log/cpdt_dmesg_current.log.1", kernel_line("only kernel"))
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert set(manifest["target_paths"]) == {domain}
    assert manifest["matched_files"] == 1
    assert manifest["sources"][0]["log_domain"] == domain


def test_product_reports_both_directories_missing(tmp_path):
    root = tmp_path / "input"
    write(root / "a.log", user_line("generic"))
    batch = tmp_path / "batch"
    with pytest.raises(FileNotFoundError, match="No configured source directory"):
        run_analysis(root, batch, collection_mode="product")
    assert json.loads((batch / "manifest.json").read_text())["status"] == "failed"


def test_default_product_directory_names_ignore_substring_lookalikes(tmp_path):
    root = tmp_path / "input"
    write(root / "diag/diag.log", user_line("correct user"))
    write(root / "var/cpdt_dmesg_current.log", kernel_line("correct kernel"))
    write(root / "diagnostics/diag.log", user_line("wrong user"))
    write(root / "variant/cpdt_dmesg_current.log", kernel_line("wrong kernel"))
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product")
    assert manifest["target_paths"] == {"user": str(root / "diag"), "kernel": str(root / "var")}
    assert manifest["matched_files"] == 2


@pytest.mark.parametrize("layout", ["diag/var", "var/diag"])
def test_nested_domain_directories_own_their_files_and_archives_once(tmp_path, layout):
    root = tmp_path / "input"
    user_root = root / ("diag" if layout == "diag/var" else "var/diag")
    kernel_root = root / ("diag/var" if layout == "diag/var" else "var")
    write(user_root / "diag.log", user_line("bare user"))
    write(kernel_root / "cpdt_dmesg_current.log", kernel_line("bare kernel"))
    (user_root / "user.zip").write_bytes(zip_bytes({
        "diaglog_62_20000106043418.log": user_line("packed user").encode(),
    }))
    (kernel_root / "kernel.zip").write_bytes(zip_bytes({
        "inner.zip": zip_bytes({"cpdt_dmesg_current.log.1": kernel_line("packed kernel").encode()}),
    }))
    before = tree_digest(root)
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product",
                            file_patterns=["*.log"], copy_patterns=["*.log"])
    assert manifest["target_paths"] == {"user": str(user_root), "kernel": str(kernel_root)}
    assert file_names(manifest, "user") == ["diaglog_62_20000106043418.log", "diag.log"]
    assert file_names(manifest, "kernel") == ["cpdt_dmesg_current.log.1", "cpdt_dmesg_current.log"]
    assert manifest["matched_files"] == 4
    assert manifest["matched_archives"] == 3
    assert len({item["origin_path"] for item in manifest["archives"]}) == 3
    assert sorted(item["log_domain"] for item in manifest["archives"]) == ["kernel", "kernel", "user"]
    assert tree_digest(root) == before


def test_product_does_not_accept_only_substring_directory_names(tmp_path):
    root = tmp_path / "input"
    write(root / "diagnostics/diag.log", user_line("lookalike user"))
    write(root / "variant/cpdt_dmesg_current.log", kernel_line("lookalike kernel"))
    with pytest.raises(FileNotFoundError, match="No configured source directory"):
        run_analysis(root, tmp_path / "batch", collection_mode="product")
    custom = run_analysis(root, tmp_path / "custom", collection_mode="product",
                          diag_directory_pattern="diagnost", var_directory_pattern="variant")
    assert custom["target_paths"] == {"user": str(root / "diagnostics"), "kernel": str(root / "variant")}
    assert custom["matched_files"] == 2


@pytest.mark.parametrize("domain", ["diag", "var"])
def test_ambiguous_product_directory_requires_partial_path(tmp_path, domain):
    root = tmp_path / "input"
    name = "diag.log" if domain == "diag" else "cpdt_dmesg_current.log"
    for parent in ("first", "second"):
        write(root / parent / domain / name, user_line(parent) if domain == "diag" else kernel_line(parent))
    with pytest.raises(ValueError, match="ambiguous directory"):
        run_analysis(root, tmp_path / "ambiguous", collection_mode="product")
    options = {"diag_directory_pattern" if domain == "diag" else "var_directory_pattern": "first/" + domain}
    manifest = run_analysis(root, tmp_path / "unique", collection_mode="product", **options)
    assert manifest["matched_files"] == 1
    assert "first/" in manifest["sources"][0]["relative_path"]


def test_auto_generic_explicit_directory_and_diag_override_remain_available(tmp_path):
    root = tmp_path / "input"
    write(root / "diag/arbitrary.txt", user_line("custom filename"))
    write(root / "var/log/cpdt_dmesg_current.log.1", kernel_line("kernel rotation"))
    generic = run_analysis(root, tmp_path / "generic", directory_pattern="diag")
    assert generic["collection_mode"] == "generic"
    assert generic["counts"]["driver_records"] == 1
    product = run_analysis(root, tmp_path / "product", file_patterns=["*.txt"])
    assert product["collection_mode"] == "product"
    assert file_names(product, "user") == ["arbitrary.txt"]
    assert file_names(product, "kernel") == ["cpdt_dmesg_current.log.1"]
    no_product = tmp_path / "generic-input"
    write(no_product / "a.log", user_line("no product directories"))
    auto = run_analysis(no_product, tmp_path / "auto")
    assert auto["collection_mode"] == "generic"
    assert auto["counts"]["driver_records"] == 1


def test_kernel_always_recursive_but_diag_respects_no_recursive(tmp_path):
    root = tmp_path / "input"
    write(root / "diag/diag.log", user_line("direct"))
    write(root / "diag/nested/diaglog_62_20000106043418.log", user_line("nested"))
    write(root / "var/deep/log/cpdt_dmesg_current.log.2", kernel_line("nested kernel"))
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product", recursive=False)
    assert file_names(manifest, "user") == ["diag.log"]
    assert file_names(manifest, "kernel") == ["cpdt_dmesg_current.log.2"]


def test_copy_rename_preserves_product_original_file_order(tmp_path):
    root = tmp_path / "input"
    write(root / "diag/diag.log", user_line("latest"))
    write(root / "diag/diaglog_1.log", user_line("oldest"))
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product",
                            rename_map={"diaglog_1.log": "zz-renamed.log", "diag.log": "aa-renamed.log"})
    assert file_names(manifest, "user") == ["diaglog_1.log", "diag.log"]
    assert [Path(source["path"]).name for source in manifest["sources"]] == ["zz-renamed.log", "aa-renamed.log"]
    assert [row["text"].split("Info=", 1)[1] for row in query_records(tmp_path / "batch", kind="driver")["records"]] == ["oldest", "latest"]
    assert (root / "diag/diag.log").exists()
    assert (root / "diag/diaglog_1.log").exists()


def test_nested_same_names_have_distinct_sources_and_stable_tie_order(tmp_path):
    root = tmp_path / "input"
    (root / "var").mkdir(parents=True)
    for name in ("b.zip", "a.zip"):
        (root / "var" / name).write_bytes(zip_bytes({"inner.zip": zip_bytes({
            "cpdt_dmesg_current.log.1": kernel_line(name).encode(),
        })}))
    serial = run_analysis(root, tmp_path / "serial", collection_mode="product", workers=1)
    parallel = run_analysis(root, tmp_path / "parallel", collection_mode="product", workers=4)
    assert len({source["source_id"] for source in serial["sources"]}) == 2
    assert [source["relative_path"] for source in serial["sources"]] == [
        "var/a.zip!/inner.zip!/cpdt_dmesg_current.log.1", "var/b.zip!/inner.zip!/cpdt_dmesg_current.log.1"]
    assert [(source["source_id"], source["source_rank"]) for source in serial["sources"]] == [
        (source["source_id"], source["source_rank"]) for source in parallel["sources"]]


def test_diag_latest_is_last_for_cross_file_firmware_segment_assembly(tmp_path):
    root = tmp_path / "input"
    def firmware(seg: int, message: str) -> str:
        return ("2000-01-06 04:36:49 host %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;"
                "(SlotId=62, CpuId=0, ModuleName=UBE, info=[INFO][HAL_UB]"
                f"Unit[4] firmware logType[1] seg[{seg}]:\n[87527.625] {message}\n")
    write(root / "diag/diag.log", firmware(2, "latest"))
    write(root / "diag/diaglog_62_20000106043418.log", firmware(1, "middle"))
    write(root / "diag/diaglog_62_20000106043416.log", firmware(0, "oldest"))
    manifest = run_analysis(root, tmp_path / "batch", collection_mode="product", workers=4)
    assert manifest["counts"]["dumps"] == 1
    records = query_records(tmp_path / "batch", kind="firmware", view="stitched")["records"]
    assert [row["seg"] for row in records] == [0, 1, 2]
    assert len({row["dump_id"] for row in records}) == 1


def test_nested_expansion_shares_file_budget(tmp_path, monkeypatch):
    import product_log.workflow as workflow
    root = tmp_path / "input"
    (root / "var").mkdir(parents=True)
    (root / "var/outer.zip").write_bytes(zip_bytes({"inner.zip": zip_bytes({
        "cpdt_dmesg_current.log": kernel_line("leaf").encode(),
    })}))
    monkeypatch.setattr(workflow, "MAX_EXPANDED_FILES", 1)
    batch = tmp_path / "batch"
    with pytest.raises(ValueError, match="budget"):
        run_analysis(root, batch, collection_mode="product")
    assert json.loads((batch / "manifest.json").read_text())["status"] == "failed"
    assert (root / "var/outer.zip").exists()


def test_nested_expansion_cancellation_is_persisted(tmp_path):
    root = tmp_path / "input"
    (root / "var").mkdir(parents=True)
    (root / "var/outer.zip").write_bytes(zip_bytes({"inner.zip": zip_bytes({
        "cpdt_dmesg_current.log": kernel_line("leaf").encode(),
    })}))
    cancelled = False

    def progress(event):
        nonlocal cancelled
        if event["stage"] == "extracting" and event["count"] == 2:
            cancelled = True

    batch = tmp_path / "batch"
    with pytest.raises(AnalysisCancelled):
        run_analysis(root, batch, collection_mode="product", progress=progress, cancelled=lambda: cancelled)
    assert json.loads((batch / "manifest.json").read_text())["status"] == "cancelled"
    assert not (batch / "index.sqlite3").exists()


def test_product_cli_exposes_collection_and_partial_directory_options(tmp_path, capsys):
    root = tmp_path / "input"
    write(root / "selected/diag/diag.log", user_line("CLI"))
    write(root / "other/diag/diag.log", user_line("ignore"))
    write(root / "selected/var/log/cpdt_dmesg_current.log.10", kernel_line("CLI kernel"))
    assert main([str(root), "--output", str(tmp_path / "batch"), "--collection-mode", "product",
                 "--diag-directory", "selected/diag", "--var-directory", "selected/var"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["collection_mode"] == "product"
    assert result["matched_files"] == 2


def test_product_output_directory_is_not_a_directory_candidate(tmp_path):
    root = tmp_path / "input"
    write(root / "source/diag/diag.log", user_line("source"))
    manifest = run_analysis(root, root / "diag", collection_mode="product")
    assert manifest["target_paths"] == {"user": str(root / "source/diag")}
    assert manifest["matched_files"] == 1


def test_invalid_collection_mode_fails_before_creating_output(tmp_path):
    root = tmp_path / "input"
    write(root / "a.log", user_line("source"))
    batch = tmp_path / "batch"
    with pytest.raises(ValueError, match="collection_mode"):
        run_analysis(root, batch, collection_mode="unknown")
    assert not batch.exists()
