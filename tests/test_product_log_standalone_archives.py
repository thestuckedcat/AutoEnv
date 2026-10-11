"""Use the real embedded browser codecs to exercise virtual archive collection."""
from __future__ import annotations

import base64
import bz2
from functools import lru_cache
import gzip
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
    return module.compression_source() + "\n" + archive


def _prepare(files: dict[str, bytes], rules: dict | None = None, *, cancel_at: str = "") -> dict:
    harness = r"""
const vm = require('node:vm'), fs = require('node:fs'), crypto = require('node:crypto');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = vm.createContext({Uint8Array,Uint32Array,ArrayBuffer,DataView,TextDecoder,
  Blob,Response,ReadableStream,WebAssembly,atob,btoa,setTimeout,clearTimeout});
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
    request = {"code": _javascript(), "files": [{"path": path, "bytes": base64.b64encode(data).decode("ascii")}
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


@pytest.mark.parametrize("format", ["tar", "tar.gz", "tgz", "tar.bz2", "tar.xz", "zip", "zip-descriptor", "zip-bzip", "gz", "bz2", "xz"])
def test_every_archive_type_uses_embedded_codec(format):
    payload = "2000-01-06 03:51:36.383 中文日志\n".encode()
    tar = _tar({"diag.log": payload})
    archive = {"tar": lambda: tar, "tar.gz": lambda: gzip.compress(tar), "tgz": lambda: gzip.compress(tar),
               "tar.bz2": lambda: bz2.compress(tar), "tar.xz": lambda: lzma.compress(tar),
               "zip": lambda: _zip({"diag.log": payload}),
               "zip-descriptor": lambda: _zip({"diag.log": payload}, descriptor=True),
               "zip-bzip": lambda: _zip({"diag.log": payload}, method=zipfile.ZIP_BZIP2),
               "gz": lambda: gzip.compress(payload), "bz2": lambda: bz2.compress(payload), "xz": lambda: lzma.compress(payload)}[format]()
    name = "diag.log." + format if format in {"gz", "bz2", "xz"} else "package." + format
    # Container recognition is content-based, including a gzip disguised as .tgz.
    output = _prepare({name: archive}, {"collection_mode": "generic"})
    assert "error" not in output, output
    assert output["result"]["sources"][0]["text"] == payload.decode()
    assert output["result"]["sources"][0]["original_name"] == "diag.log"
    assert output["result"]["input_unchanged"]


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


def test_diag_one_layer_var_recurses_and_numeric_order_ignores_diag_options():
    files = {"diag/package.zip": _zip({"deep.zip": _zip({"diag.log": b"too deep"}), "diaglog_01.log": b"history"}),
             "diag/diag.log": b"latest",
             "var/outer.tar.gz": gzip.compress(_tar({"middle.zip": _zip({
                 "cpdt_dmesg_current.log.10": b"10", "cpdt_dmesg_current.log.2": b"2",
                 "cpdt_dmesg_current.log.1": b"1", "cpdt_dmesg_current.log": b"current"})}))}
    result = _prepare(files, {"archive_patterns": ["*.zip"], "recursive": False})["result"]
    assert [source["text"] for source in result["sources"]] == ["history", "latest", "1", "2", "10", "current"]
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
