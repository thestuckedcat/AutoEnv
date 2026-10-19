"""RAR metadata validation and actual bsdtar decompression, without pip packages."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import zlib
import zipfile
from pathlib import Path

import pytest

from autoenv import FileHandle, detect_archive
from product_log import run_analysis
from product_log.index import query_records


FIXTURES = Path(__file__).parent / "fixtures/rar"
COMPRESSED = Path(__file__).parent / "fixtures/product_log_archives"


def stored_rar(files: dict[str, bytes]) -> bytes:
    def header(kind: int, flags: int, payload: bytes) -> bytes:
        body = struct.pack("<BHH", kind, flags, len(payload) + 7) + payload
        return struct.pack("<H", zlib.crc32(body) & 0xFFFF) + body
    result = b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\0" * 6)
    for name, data in files.items():
        encoded = name.encode("utf-8")
        payload = struct.pack("<IIBIIBBHI", len(data), len(data), 2,
                              zlib.crc32(data), 0, 20, 0x30, len(encoded), 0x20) + encoded
        result += header(0x74, 0x8000 | 0x0200, payload) + data
    return result + header(0x7B, 0, b"")


def test_rar_bracket_names_select_the_literal_member(tmp_path):
    archive = tmp_path / "literal.rar"
    archive.write_bytes(stored_rar({"target[1].log": b"good", "target1.log": b"evil"}))
    output = FileHandle(archive).unzip(tmp_path / "out")
    assert (output / "target[1].log").read_bytes() == b"good"
    assert (output / "target1.log").read_bytes() == b"evil"


@pytest.mark.parametrize("version", [4, 5])
def test_rar_detects_content_and_extracts_real_compressed_files(tmp_path, version):
    archive = tmp_path / "not-an-extension.data"
    archive.write_bytes((COMPRESSED / f"compressed-rar{version}.rar").read_bytes())
    assert detect_archive(archive) == "rar"
    output = FileHandle(archive).unzip(tmp_path / "out")
    assert (output / "diag.log").read_bytes() == (
        b"2000-01-06 03:51:36.383 C01-A06-sfu2 %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;"
        b"UB Forward SDK module. (SlotId=62, CpuId=0, ModuleName=udk, Info=RAR log)\n" * 64)


def test_rar5_solid_archive_extracts_every_member(tmp_path):
    output = FileHandle(FIXTURES / "rar5-solid.rar").unzip(tmp_path / "out")
    expected = b"".join(f"{index:03d}\n".encode() for index in range(512))
    assert (output / "stest1.txt").read_bytes() == expected
    assert (output / "stest2.txt").read_bytes() == expected


@pytest.mark.parametrize("version", [4, 5])
def test_nested_real_compressed_rar_reaches_the_log_parser_after_preparation(tmp_path, version):
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("driver.rar", (COMPRESSED / f"compressed-rar{version}.rar").read_bytes())
    package = tmp_path / "product.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("node/diag/stage.zip", nested.getvalue())
        archive.writestr("node/varlog/cpdt_dmesg_current.log", "[Thu Jan 6 04:36:49 2000] <thread:1:2> udk kernel\n")
    original = package.read_bytes()
    result = run_analysis(package, tmp_path / "batch", collection_mode="product")
    assert result["counts"]["user_records"] == 64
    assert result["counts"]["kernel_records"] == 1
    assert result["matched_archives"] == 2
    assert next(source for source in result["sources"] if source["log_domain"] == "user")["provenance_parts"] == [
        "node/diag/stage.zip", "driver.rar", "diag.log"]
    records = query_records(tmp_path / "batch", kind="driver", driver_domain="user")["records"]
    assert len(records) == 64
    assert all("RAR log" in record["text"] for record in records)
    assert package.read_bytes() == original


@pytest.mark.parametrize("name,reason", [
    ("rar3-symlink-unix.rar", "links"),
    ("rar5-hlink.rar", "links"),
    ("rar5-psw.rar", "encrypted"),
    ("rar5-hpsw.rar", "encrypted"),
    ("rar5-dups.rar", "links"),
])
def test_rar_unsupported_members_never_publish_partial_outputs(tmp_path, name, reason):
    output = tmp_path / "out"
    with pytest.raises((ValueError, FileExistsError), match=reason):
        FileHandle(FIXTURES / name).unzip(output)
    assert not output.exists()
    assert not list(tmp_path.glob(".autoenv-unzip-*"))


@pytest.mark.parametrize("limits", [{"max_files": 1}, {"max_bytes": 4095}])
def test_rar_declared_members_obey_existing_expansion_limits(tmp_path, limits):
    with pytest.raises(ValueError, match="exceed"):
        FileHandle(FIXTURES / "rar5-solid.rar").unzip(tmp_path / "out", **limits)
    assert not (tmp_path / "out").exists()


def test_rar_corruption_is_rejected_before_publication(tmp_path):
    data = bytearray((COMPRESSED / "compressed-rar5.rar").read_bytes())
    data[-18] ^= 0x10
    archive = tmp_path / "corrupt.rar"
    archive.write_bytes(data)
    with pytest.raises(ValueError, match="RAR"):
        FileHandle(archive).unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_rar_oversized_header_is_not_accepted_as_an_empty_archive(tmp_path):
    archive = tmp_path / "oversized.rar"
    # RAR5 header-size vint encodes 2 MiB + 1, exceeding the format limit.
    archive.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\0" * 4 + b"\x81\x80\x80\x01")
    with pytest.raises(ValueError, match="RAR"):
        FileHandle(archive).unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_rar_missing_backend_has_a_specific_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ValueError, match="bsdtar"):
        FileHandle(COMPRESSED / "compressed-rar5.rar").unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_vendored_rar_metadata_sources_and_fixture_bytes_match_provenance():
    vendor = Path(__file__).parents[1] / "autoenv/_vendor"
    for root, name in [(vendor, "rarfile_manifest.json"), (FIXTURES, "manifest.json")]:
        manifest = json.loads((root / name).read_text(encoding="utf-8"))
        for item in manifest["files"]:
            assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]
