"""Use the real embedded browser codecs to exercise virtual archive collection."""
from __future__ import annotations

import base64
import bz2
from functools import lru_cache
import gzip
import hashlib
import importlib.util
import io
import json
import lzma
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import zipfile
import zlib

import pytest

from product_log_domain_sample import build_domain_archive


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to execute browser JavaScript offline")


@lru_cache(maxsize=1)
def _javascript() -> str:
    spec = importlib.util.spec_from_file_location("standalone_page_builder", ROOT / "tools" / "build_product_log_page.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = (ROOT / "product_log" / "standalone" / "archive.js").read_text(encoding="utf-8")
    defaults = (ROOT / "product_log" / "default_config.json").read_text(encoding="utf-8")
    configuration = (ROOT / "product_log" / "standalone" / "configuration.js").read_text(encoding="utf-8")
    return (module.compression_source() + "\nglobalThis.ProductLogDefaultConfig=" + defaults + ";\n"
            + configuration + "\n" + archive)


def _prepare(files: dict[str, bytes], rules: dict | None = None, *, cancel_at: str = "", code: str | None = None) -> dict:
    harness = r"""
const vm = require('node:vm'), fs = require('node:fs'), crypto = require('node:crypto');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({Uint8Array,Uint32Array,ArrayBuffer,DataView,TextDecoder,
  Blob,Response,ReadableStream,WebAssembly,atob,btoa,setTimeout,clearTimeout,
  WorkerGlobalScope:function(){},location:{href:'blob:offline-decoder-test'},
  importScripts:()=>{throw new Error('Offline decoder attempted network access');},
  fetch:()=>{throw new Error('Offline decoder attempted network access');},
  XMLHttpRequest:()=>{throw new Error('Offline decoder attempted network access');}});
context.self=context;
vm.runInContext(request.code, context, {filename:'offline-archives.js'});
const files = request.files.map(file => ({path:file.path,bytes:Uint8Array.from(Buffer.from(file.bytes,'base64'))}));
const before = files.map(file => crypto.createHash('sha256').update(file.bytes).digest('hex'));
let cancelled = false;
(async () => {
  try {
    const result = await context.ProductLogOfflineArchives.prepare(files,request.rules,
      event => {if (event.stage === request.cancel_at) cancelled = true;}, () => cancelled);
    for (const source of result.sources) {
      source.sha256 = crypto.createHash('sha256').update(source.bytes).digest('hex'); delete source.bytes;
    }
    result.input_unchanged = files.every((file,index) => before[index] === crypto.createHash('sha256').update(file.bytes).digest('hex'));
    process.stdout.write(JSON.stringify({result}));
  } catch (error) { process.stdout.write(JSON.stringify({error:String(error.message || error)})); }
})();
"""
    request = {"code": _javascript() if code is None else code, "files": [{"path": path, "bytes": base64.b64encode(data).decode("ascii")}
                                                for path, data in files.items()],
               "rules": rules or {}, "cancel_at": cancel_at}
    completed = subprocess.run([NODE, "-e", harness], input=json.dumps(request), text=True,
                               encoding="utf-8", capture_output=True, timeout=45, check=True)
    return json.loads(completed.stdout)


def _tar(files: dict[str, bytes], *, format: int = tarfile.PAX_FORMAT) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=format) as archive:
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def _zip(files: dict[str, bytes], *, descriptor: bool = False, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    class NonSeekable(io.BytesIO):
        def seek(self, *args):
            raise io.UnsupportedOperation()
    output = NonSeekable() if descriptor else io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=method) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return output.getvalue()


def test_browser_collector_real_dense_nested_domain_archive(tmp_path):
    archive = tmp_path / "product.tar.gz"
    build_domain_archive(archive)
    output = _prepare({archive.name: archive.read_bytes()}, {"diag_directory_pattern": "diag/slot62"})
    assert "error" not in output, output
    result = output["result"]
    assert result["counts"]["files"] == 11
    assert result["counts"]["archives"] == 12
    assert result["targets"] == {"user": "diag/slot62", "kernel": "var"}
    assert result["input_unchanged"]
    assert [source["original_name"] for source in result["sources"][-4:]] == [
        "cpdt_dmesg_current.log.1", "cpdt_dmesg_current.log.2", "cpdt_dmesg_current.log.10", "cpdt_dmesg_current.log"]
    assert result["sources"][6]["original_name"] == "diag.log"
    assert result["sources"][6]["log_domain"] == "user"
    assert result["sources"][6]["text"].count("ModuleName=udk") > 0
    assert result["sources"][-2]["origin_path"].count("!/") == 4
    assert sum(source["text"].count("WITHOUT DATE") for source in result["sources"]) == 8


@pytest.mark.parametrize("format", ["tar", "tar.gz", "tgz", "tar.bz2", "tar.xz", "zip", "zip-descriptor", "zip-bzip", "zip-lzma", "zip-lzma-descriptor", "gz", "bz2", "xz"])
def test_every_archive_type_uses_embedded_codec(format):
    payload = "2000-01-06 03:51:36.383 中文日志\n".encode()
    tar = _tar({"diag.log": payload})
    archive = {"tar": lambda: tar, "tar.gz": lambda: gzip.compress(tar), "tgz": lambda: gzip.compress(tar),
               "tar.bz2": lambda: bz2.compress(tar), "tar.xz": lambda: lzma.compress(tar),
               "zip": lambda: _zip({"diag.log": payload}),
               "zip-descriptor": lambda: _zip({"diag.log": payload}, descriptor=True),
               "zip-bzip": lambda: _zip({"diag.log": payload}, method=zipfile.ZIP_BZIP2),
               "zip-lzma": lambda: _zip({"diag.log": payload}, method=zipfile.ZIP_LZMA),
               "zip-lzma-descriptor": lambda: _zip({"diag.log": payload}, method=zipfile.ZIP_LZMA, descriptor=True),
               "gz": lambda: gzip.compress(payload), "bz2": lambda: bz2.compress(payload), "xz": lambda: lzma.compress(payload)}[format]()
    name = "diag.log." + format if format in {"gz", "bz2", "xz"} else "package." + format
    # Container recognition is content-based, including a gzip disguised as .tgz.
    output = _prepare({name: archive}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == payload.decode()
    assert output["result"]["sources"][0]["original_name"] == "diag.log"
    assert output["result"]["input_unchanged"]


@pytest.mark.parametrize("version", [4, 5])
def test_rar_compressed_fixtures_are_decoded_without_external_runtime(version):
    archive = ROOT / "tests" / "fixtures" / "product_log_archives" / f"compressed-rar{version}.rar"
    payload = (b"2000-01-06 03:51:36.383 C01-A06-sfu2 %%01UBFWD/7/UBSDK_DIAG(D):Service=fwmd0;"
               b"UB Forward SDK module. (SlotId=62, CpuId=0, ModuleName=udk, Info=RAR log)\n") * 64
    # Genuine compressed archives, generated once by RAR 6.00 with -m5.
    output = _prepare({"disguised.bundle": archive.read_bytes()}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == payload.decode()
    assert output["result"]["sources"][0]["original_name"] == "diag.log"
    assert output["result"]["archives"][0]["format"] == "rar"
    assert output["result"]["input_unchanged"]


@pytest.mark.parametrize("version", [4, 5])
def test_rar_solid_fixture_extracts_all_files_without_losing_decoder_state(version):
    fixture = ROOT / "tests/fixtures/product_log_archives" / f"solid-rar{version}.rar"
    output = _prepare({"logs.rar": fixture.read_bytes()}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert [source["original_name"] for source in output["result"]["sources"]] == ["diag.log", "diaglog_01.log"]
    assert output["result"]["sources"][0]["text"].count("Info=RAR log)\n") == 64
    assert output["result"]["sources"][1]["text"].count("RAR historical log)\n") == 64


def test_rar5_blake2_checksum_does_not_assume_an_optional_crc_field():
    fixture = ROOT / "tests/fixtures/product_log_archives/blake2-rar5.rar"
    output = _prepare({"logs.rar": fixture.read_bytes()}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"].count("Info=RAR log)\n") == 64


def test_rar5_hardlink_is_rejected_as_a_link_before_native_extraction():
    fixture = ROOT / "tests/fixtures/product_log_archives/hardlink-rar5.rar"
    output = _prepare({"logs.rar": fixture.read_bytes()}, {"collection_mode": "generic"})
    assert "链接" in output["error"], output


def test_rar_unicode_member_name_is_preserved_by_native_decoder():
    fixture = ROOT / "tests/fixtures/product_log_archives/unicode-rar4.rar"
    output = _prepare({"logs.rar": fixture.read_bytes()}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["original_name"] == "diaglog_中文.log"
    assert output["result"]["sources"][0]["text"] == "Unicode RAR 日志\n"


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize("encryption", ["file", "header"])
def test_rar_encrypted_headers_and_members_are_rejected_explicitly(version, encryption):
    fixture = ROOT / "tests/fixtures/product_log_archives" / f"encrypted-{encryption}-rar{version}.rar"
    output = _prepare({"logs.rar": fixture.read_bytes()}, {"collection_mode": "generic"})
    assert "加密" in output["error"], output


def _rar4_stored(files: list[tuple[str, bytes, int]]) -> bytes:
    """Build genuine RAR4 stored members with header/data CRCs for metadata cases."""
    def header(kind: int, flags: int, body: bytes = b"") -> bytes:
        data = struct.pack("<BHH", kind, flags, len(body) + 7) + body
        return struct.pack("<H", zlib.crc32(data) & 0xffff) + data
    output = bytearray(b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\0" * 6))
    for name, payload, mode in files:
        raw_name = name.encode("ascii")
        body = struct.pack("<IIBIIBBHI", len(payload), len(payload), 3, zlib.crc32(payload),
                           0, 20, 0x30, len(raw_name), mode) + raw_name
        output.extend(header(0x74, 0x8000, body) + payload)
    output.extend(header(0x7b, 0x4000))
    return bytes(output)


@pytest.mark.parametrize("files,rule,message", [
    ([("../diag.log", b"escape", 0o100644)], {}, "不安全"),
    ([("a.log", b"a", 0o100644), ("A.log", b"b", 0o100644)], {}, "冲突"),
    ([("diag.log", b"target.log", 0o120777)], {}, "链接"),
    ([("diag.log", b"larger than budget", 0o100644)], {"max_bytes": 3}, "限制"),
])
def test_rar_validated_metadata_rejects_unsafe_members_before_data(files, rule, message):
    result = _prepare({"logs.rar": _rar4_stored(files)}, {"collection_mode": "generic", **rule})
    assert message in result["error"], result


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA])
def test_zip_crc_rejection_is_preserved_when_lzma_decoder_is_present(method):
    archive = bytearray(_zip({"diag.log": b"real log payload"}, method=method))
    central = archive.index(b"PK\x01\x02")
    archive[central + 16] ^= 1
    output = _prepare({"logs.zip": bytes(archive)}, {"collection_mode": "generic"})
    assert "CRC" in output["error"], output


@pytest.mark.parametrize("version", [4, 5])
def test_rar_rejects_truncated_compressed_payload(version):
    fixture = ROOT / "tests" / "fixtures" / "product_log_archives" / f"compressed-rar{version}.rar"
    output = _prepare({"logs.rar": fixture.read_bytes()[:100]}, {"collection_mode": "generic"})
    assert "error" in output, output
    assert "读取" in output["error"] or "截断" in output["error"] or "解压" in output["error"], output


def test_generated_single_html_decodes_nested_rar_and_zip_lzma_offline(tmp_path):
    import re
    spec = importlib.util.spec_from_file_location("archive_generated_builder", ROOT / "tools" / "build_product_log_page.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    output = tmp_path / "offline.html"
    builder.build_page(output)
    text = output.read_text(encoding="utf-8")
    encoded = re.search(r'<script id="offline-worker" type="application/octet-stream">([^<]+)</script>', text).group(1)
    code = base64.b64decode(encoded).decode("utf-8")
    rar4 = (ROOT / "tests/fixtures/product_log_archives/compressed-rar4.rar").read_bytes()
    rar5 = (ROOT / "tests/fixtures/product_log_archives/compressed-rar5.rar").read_bytes()
    nested = _zip({"rar4.rar": rar4, "rar5.rar": rar5, "last.zip": _zip({
        "diaglog_03.log": b"nested LZMA log\n"}, method=zipfile.ZIP_LZMA)}, method=zipfile.ZIP_LZMA)
    result = _prepare({"outer.zip": nested}, {"collection_mode": "generic"}, code=code)
    assert "error" not in result, result
    assert result["result"]["counts"]["archives"] == 4
    assert [source["original_name"] for source in result["result"]["sources"]] == ["diag.log", "diag.log", "diaglog_03.log"]
    assert result["result"]["sources"][-1]["text"] == "nested LZMA log\n"
    assert result["result"]["input_unchanged"]


def test_zip_lzma_reads_all_chunks_and_empty_unicode_members(monkeypatch):
    payload = ("large 中文 log\n" * 12000).encode("utf-8")
    with monkeypatch.context() as context:
        context.setattr(zipfile, "ZIP64_LIMIT", 32)
        archive = _zip({"diaglog_中文.log": payload, "diag.log": b""},
                       method=zipfile.ZIP_LZMA, descriptor=True)
    output = _prepare({"logs.zip": archive}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert [(source["original_name"], source["text"]) for source in output["result"]["sources"]] == [
        ("diag.log", ""), ("diaglog_中文.log", payload.decode("utf-8"))]


def test_zip_lzma_central_directory_order_is_independent_of_local_member_order():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("diaglog_中文.log", b"LZMA", compress_type=zipfile.ZIP_LZMA)
        archive.writestr("diaglog_01.log", b"deflate", compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("diag.log", b"bzip2", compress_type=zipfile.ZIP_BZIP2)
    data = bytearray(output.getvalue())
    end = data.rindex(b"PK\x05\x06")
    start = struct.unpack_from("<I", data, end + 16)[0]
    records, cursor = [], start
    while cursor < end:
        length = 46 + sum(struct.unpack_from("<HHH", data, cursor + 28))
        records.append(bytes(data[cursor:cursor + length]))
        cursor += length
    data[start:end] = b"".join(reversed(records))
    with zipfile.ZipFile(io.BytesIO(data)) as oracle:
        assert oracle.read("diaglog_中文.log") == b"LZMA"
    result = _prepare({"logs.zip": bytes(data)}, {"collection_mode": "generic"})
    assert "error" not in result, result
    assert {source["original_name"]: source["text"] for source in result["result"]["sources"]} == {
        "diaglog_中文.log": "LZMA", "diaglog_01.log": "deflate", "diag.log": "bzip2"}


def test_zip_lzma_binary_payload_is_never_automatically_converted_to_utf8():
    payload = bytes(range(256)) * 300 + b"\xc0\xaf\xed\xa0\x80\0"
    result = _prepare({"logs.zip": _zip({"diag.log": payload}, method=zipfile.ZIP_LZMA)},
                      {"collection_mode": "generic"})
    assert "error" not in result, result
    assert result["result"]["sources"][0]["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("damage", ["properties", "stream"])
def test_zip_lzma_rejects_invalid_properties_and_truncated_stream(damage):
    data = bytearray(_zip({"diag.log": b"real LZMA log\n" * 200}, method=zipfile.ZIP_LZMA))
    central = data.index(b"PK\x01\x02")
    start = 30 + len("diag.log")
    if damage == "properties":
        data[start + 4] = 255
    else:
        # Declare only the ZIP SDK/properties header, leaving no range-coded stream.
        struct.pack_into("<I", data, central + 20, 9)
        struct.pack_into("<I", data, 18, 9)
    result = _prepare({"logs.zip": bytes(data)}, {"collection_mode": "generic"})
    assert "error" in result, result


@pytest.mark.parametrize("payload", [b"", b"known size without EOS\n" * 200], ids=["empty", "nonempty"])
def test_zip_lzma_without_eos_flag_uses_declared_unpacked_size(payload):
    data = bytearray(_zip({"diag.log": payload}, method=zipfile.ZIP_LZMA))
    start, central = 30 + len("diag.log"), data.index(b"PK\x01\x02")
    packed = bytes(data[start:central])
    packed = packed[:-5] if payload else packed[:9] + b"\0" * 5
    delta = len(packed) - (central - start)
    data[start:central] = packed
    central += delta
    end = data.rindex(b"PK\x05\x06")
    for offset in (6, central + 8):
        struct.pack_into("<H", data, offset, struct.unpack_from("<H", data, offset)[0] & ~2)
    struct.pack_into("<I", data, 18, len(packed))
    struct.pack_into("<I", data, central + 20, len(packed))
    struct.pack_into("<I", data, end + 16, central)
    with zipfile.ZipFile(io.BytesIO(data)) as oracle:
        assert oracle.read("diag.log") == payload
    result = _prepare({"logs.zip": bytes(data)}, {"collection_mode": "generic"})
    assert "error" not in result, result
    assert result["result"]["sources"][0]["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("format", [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT])
def test_tar_long_utf8_paths_and_explicit_empty_directory(format):
    long_name = "diag/" + "目录" * 50 + "/diag.log"
    output = _prepare({"logs.tar": _tar({long_name: b"long name"}, format=format)})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["relative_path"] == long_name
    empty = io.BytesIO()
    with tarfile.open(fileobj=empty, mode="w") as archive:
        directory = tarfile.TarInfo("diag")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
    result = _prepare({"empty.tar": empty.getvalue()})["result"]
    assert result["targets"] == {"user": "diag"}
    assert result["sources"] == []


def test_directory_matching_and_domain_ownership():
    files = {"bundle/diag/slot62/diag.log": b"user", "bundle/variant/diag.log": b"ignored",
             "bundle/diag/slot62/var/log/cpdt_dmesg_current.log": b"kernel",
             "bundle/diag/slot62/var/log/ignored-user.zip": _zip({"diag.log": b"not user"})}
    result = _prepare(files, {"upload_kind": "directory", "diag_directory_pattern": "diag/slot62"})["result"]
    assert [source["text"] for source in result["sources"]] == ["user", "kernel"]
    assert result["targets"]["kernel"] == "bundle/diag/slot62/var"
    ambiguous = _prepare({"root/diag/slot62/diag.log": b"a", "root/other/diag/slot62/diag.log": b"b"})
    assert "不唯一" in ambiguous["error"]
    result = _prepare({"root/diag/slot62/diag.log": b"a", "root/other/diag/slot62/diag.log": b"b"},
                      {"diag_directory_pattern": "root/diag/slot62"})["result"]
    assert [source["text"] for source in result["sources"]] == ["a"]
    # Directory mode with exactly one nested archive must retain its folder paths.
    result = _prepare({"bundle/diag/slot62/logs.zip": _zip({"diag.log": b"directory"})},
                      {"upload_kind": "directory", "diag_directory_pattern": "diag/slot62"})["result"]
    assert result["targets"] == {"user": "bundle/diag/slot62"}
    assert len(result["archives"]) == 1 and result["archives"][0]["level"] == 1


def test_all_nested_archives_expand_before_domain_collection_and_numeric_order():
    files = {"diag/package.zip": _zip({"deep.zip": _zip({"diag.log": b"too deep"}), "diaglog_01.log": b"history"}),
             "diag/diag.log": b"latest",
             "var/outer.tar.gz": gzip.compress(_tar({"middle.zip": _zip({
                 "cpdt_dmesg_current.log.10": b"10", "cpdt_dmesg_current.log.2": b"2",
                 "cpdt_dmesg_current.log.1": b"1", "cpdt_dmesg_current.log": b"current"})}))}
    result = _prepare(files, {"archive_patterns": ["*.zip"], "recursive": False})["result"]
    assert [source["text"] for source in result["sources"]] == ["history", "latest", "too deep", "1", "2", "10", "current"]
    assert all(source["log_domain"] == "kernel" for source in result["sources"][-4:])


def test_copy_rename_original_order_encoding_and_literal_archive_boundary():
    files = {"diag/b.zip": _zip({"diaglog_02.log": b"archive"}), "diag/b.zip!/diaglog_02.log": b"literal",
             "diag/diaglog_01.log": "UTF16 日志".encode("utf-16"), "diag/diag.log": "GB18030 日志".encode("gb18030"),
             "diag/extra.txt": "UTF8 日志".encode()}
    result = _prepare(files, {"copy_patterns": ["extra"], "rename_map": {"diaglog_01.log": "zz.log"}})["result"]
    assert [source["text"] for source in result["sources"]] == ["UTF16 日志", "literal", "archive", "UTF8 日志", "GB18030 日志"]
    assert result["sources"][0]["path"].endswith("/zz.log")
    assert result["sources"][0]["original_name"] == "diaglog_01.log"
    equal_relative = [source for source in result["sources"] if source["original_name"] == "diaglog_02.log"]
    assert equal_relative[0]["relative_path"] == equal_relative[1]["relative_path"]
    assert equal_relative[0]["provenance_parts"] != equal_relative[1]["provenance_parts"]
    assert len({source["source_id"] for source in result["sources"]}) == 5
    ambiguous = _prepare(files, {"rename_map": {"diag/b.zip!/diaglog_02.log": "renamed.log"}})
    assert "不唯一" in ambiguous["error"]


@pytest.mark.parametrize("name", ["../escape.log", "/absolute.log", "C:/absolute.log", "CON.log", "bad./log", "bad\\..\\log"])
def test_reject_unsafe_archive_member_paths(name):
    output = _prepare({"unsafe.zip": _zip({name: b"bad"})}, {"collection_mode": "generic"})
    assert "error" in output


def test_reject_links_collisions_corruption_and_limits():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo("diag.log")
        member.type = tarfile.SYMTYPE
        member.linkname = "elsewhere.log"
        archive.addfile(member)
    assert "链接" in _prepare({"link.tar": output.getvalue()}, {"collection_mode": "generic"})["error"]
    assert "冲突" in _prepare({"duplicate.zip": _zip({"A.log": b"a", "a.log": b"b"})}, {"collection_mode": "generic"})["error"]
    corrupted = bytearray(_zip({"diag.log": b"original"}, method=zipfile.ZIP_STORED))
    corrupted[30 + len("diag.log")] ^= 1
    assert "CRC" in _prepare({"corrupt.zip": bytes(corrupted)}, {"collection_mode": "generic"})["error"]
    bad_tar = bytearray(_tar({"diag.log": b"original"}))
    bad_tar[0] ^= 1
    assert "校验" in _prepare({"corrupt.tar": bytes(bad_tar)}, {"collection_mode": "generic"})["error"]
    assert "截断" in _prepare({"tiny.tar": b"short"}, {"collection_mode": "generic"})["error"]
    for rule in ({"max_files": 0}, {"max_bytes": 3}):
        assert "限制" in _prepare({"logs.zip": _zip({"diag.log": b"original"})}, {"collection_mode": "generic", **rule})["error"]
    assert "取消" in _prepare({"logs.zip": _zip({"diag.log": b"original"})}, {"collection_mode": "generic"}, cancel_at="extracting")["error"]


def test_zip64_metadata_and_fnmatch_character_classes(monkeypatch):
    # Lowering stdlib's threshold creates genuine ZIP64 headers without a giant fixture.
    with monkeypatch.context() as context:
        context.setattr(zipfile, "ZIP64_LIMIT", 1)
        archive = _zip({"diag.log": b"ZIP64 actual container"}, descriptor=True)
    output = _prepare({"package.zip": archive}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == "ZIP64 actual container"
    files = {"diag/diaglog_1.log": b"1", "diag/diaglog_2.log": b"2", "diag/diaglog_9.log": b"9"}
    result = _prepare(files, {"file_patterns": ["diaglog_[!3-9].log"]})["result"]
    assert [source["text"] for source in result["sources"]] == ["1", "2"]


def test_compressed_container_checksums_and_concatenated_gzip_members():
    payload = b"joined first\njoined second\n"
    joined = gzip.compress(b"joined first\n") + gzip.compress(b"joined second\n")
    output = _prepare({"diag.log.gz": joined}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == payload.decode()
    gzip_bad = bytearray(gzip.compress(b"payload"))
    gzip_bad[-8] ^= 1
    assert "error" in _prepare({"diag.log.gz": bytes(gzip_bad)}, {"collection_mode": "generic"})
    xz_bad = bytearray(lzma.compress(b"payload"))
    xz_bad[-5] ^= 1
    assert "error" in _prepare({"diag.log.xz": bytes(xz_bad)}, {"collection_mode": "generic"})
    bzip_bad = bytearray(bz2.compress(b"payload"))
    bzip_bad[10] ^= 1
    assert "error" in _prepare({"diag.log.bz2": bytes(bzip_bad)}, {"collection_mode": "generic"})


def test_mixed_utf8_gb18030_line_decoding_keeps_each_line():
    payload = "UTF8 中文日志\r\n".encode("utf-8") + "GB18030 中文日志\n".encode("gb18030")
    output = _prepare({"diag/diag.log": payload}, {"upload_kind": "directory"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == "UTF8 中文日志\r\nGB18030 中文日志\n"


@pytest.mark.parametrize("payload", [b"", b"a", b"x" * 256, b"x" * 65536], ids=["empty", "single", "256", "65536"])
@pytest.mark.parametrize("padding", [1, 3, 16])
def test_gzip_zero_padding_keeps_real_trailer_zeros(payload, padding):
    data = gzip.compress(payload, mtime=0) + b"\0" * padding
    assert gzip.decompress(data) == payload
    output = _prepare({"diag.log.gz": data}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == payload.decode()
    assert output["result"]["input_unchanged"]


def _gzip_optional_header(payload: bytes) -> tuple[bytes, int]:
    ordinary = gzip.compress(payload, mtime=0)
    header = bytearray(ordinary[:10])
    header[3] = 4 | 8 | 16 | 2  # FEXTRA, FNAME, FCOMMENT, FHCRC.
    extra = b"ordinary extra metadata"
    header.extend(struct.pack("<H", len(extra)) + extra + b"ignored-original-name\0a comment\0")
    crc_offset = len(header)
    header.extend(struct.pack("<H", zlib.crc32(header) & 0xffff))
    return bytes(header) + ordinary[10:], crc_offset


def test_gzip_padding_between_members_optional_headers_and_empty_members():
    first, _ = _gzip_optional_header(b"first\n")
    second, _ = _gzip_optional_header(b"second\n")
    data = first + b"\0" * 5 + gzip.compress(b"", mtime=0) + b"\0" * 2 + second + b"\0" * 19
    assert gzip.decompress(data) == b"first\nsecond\n"
    output = _prepare({"diag.log.gz": data}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == "first\nsecond\n"


@pytest.mark.parametrize("member", [0, 1])
@pytest.mark.parametrize("damage", ["crc", "size", "header_crc"])
def test_gzip_padded_multimember_rejects_each_member_checksum_damage(member, damage):
    first, crc_first = _gzip_optional_header(b"first\n")
    second, crc_second = _gzip_optional_header(b"second\n")
    chunks = [bytearray(first), bytearray(second)]
    offset = -8 if damage == "crc" else -4 if damage == "size" else [crc_first, crc_second][member]
    chunks[member][offset] ^= 1
    data = bytes(chunks[0]) + b"\0" * 5 + bytes(chunks[1]) + b"\0" * 16
    output = _prepare({"diag.log.gz": data}, {"collection_mode": "generic"})
    assert "CRC" in output["error"], output


@pytest.mark.parametrize("garbage", [b"garbage", b"\0" * 16 + b"garbage", b"\0\1\0"])
def test_gzip_rejects_nonzero_trailing_garbage(garbage):
    output = _prepare({"diag.log.gz": gzip.compress(b"payload", mtime=0) + garbage},
                      {"collection_mode": "generic"})
    assert "error" in output, output
