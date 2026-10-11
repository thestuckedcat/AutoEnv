from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import lzma
import os
import stat
import struct
import subprocess
import tarfile
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoenv import (
    FileHandle, FileHandleGroup, accurate_search, create_file, detect_archive,
    find_subdir, fuzz_search, get_handler,
)
import autoenv.file_handles as file_handles


def write(root: Path, relative: str, content: bytes = b"fixture") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def make_zip(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return path


def make_tar(path: Path, mode: str = "w", entries: list[tuple[str, bytes]] | None = None) -> Path:
    with tarfile.open(path, mode) as archive:
        for name, content in entries or [("nested/a.log", b"driver\nfirmware\n")]:
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return path


def test_search_semantics_and_stable_lexical_order(tmp_path):
    for name in ["target2.log", "Target.log", "target10.log", "other.txt", "a/target2.log", "z/target2.log"]:
        write(tmp_path, name)
    assert [handle.filename for handle in get_handler(tmp_path)] == [
        "Target.log", "other.txt", "target10.log", "target2.log",
    ]
    fuzzy = fuzz_search(tmp_path, "target", True)
    assert isinstance(fuzzy, FileHandleGroup)
    assert [handle.path.relative_to(tmp_path).as_posix() for handle in fuzzy] == [
        "target10.log", "a/target2.log", "target2.log", "z/target2.log",
    ]
    assert len(fuzz_search(tmp_path, "target?.log", True)) == 3
    assert len(fuzz_search(tmp_path, "[Tt]arget*.log", True)) == 5
    assert len(fuzz_search(tmp_path, "[!T]arget*.log", True)) == 4
    assert accurate_search(tmp_path, "target", True) is None
    assert accurate_search(tmp_path, "target*.log", True).filename == "target10.log"
    assert accurate_search(tmp_path, "target2.log", True).path == tmp_path / "a/target2.log"
    assert len(fuzz_search(tmp_path, "")) == len(get_handler(tmp_path)) == 4
    assert accurate_search(tmp_path, "").filename == "Target.log"
    assert len(fuzz_search(tmp_path, "missing")) == 0


def test_group_paths_are_lazy_and_handler_snapshots_are_cached(tmp_path, monkeypatch):
    first = write(tmp_path, "a.log", b"old")
    write(tmp_path, "b.log", b"second")
    digested = []
    original = file_handles._digest

    def track(path):
        digested.append(path)
        return original(path)

    monkeypatch.setattr(file_handles, "_digest", track)
    group = get_handler(tmp_path)
    assert len(group) == 2 and group.paths[0] == first
    assert digested == []
    first.write_bytes(b"before-first-use")
    handle = group[0]
    assert handle.sha256 == hashlib.sha256(b"before-first-use").hexdigest()
    assert group[0] is group[0] is group[:1][0]
    assert group[-1].filename == "b.log"
    assert len(digested) == 2
    assert list(group)[0] is list(group)[0]
    first.write_bytes(b"after-first-use")
    with pytest.raises(ValueError, match="changed"):
        group[0].verify()
    with pytest.raises(FrozenInstanceError):
        handle.filename = "mutable.log"
    first.unlink()
    assert len(group) == 2  # The path snapshot is stable even when the tree changes.
    with pytest.raises(FileNotFoundError):
        handle.verify()
    with pytest.raises(IndexError):
        group[2]


def test_unused_deleted_path_fails_when_handle_is_requested(tmp_path):
    path = write(tmp_path, "late.log")
    group = get_handler(tmp_path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        next(iter(group))


@pytest.mark.parametrize("operation", [fuzz_search, accurate_search, get_handler])
def test_search_validates_input_tree(tmp_path, operation):
    with pytest.raises(FileNotFoundError):
        operation(tmp_path / "missing")
    plain = write(tmp_path, "plain")
    with pytest.raises(NotADirectoryError):
        operation(plain)
    with pytest.raises(TypeError, match="recursive"):
        operation(tmp_path, recursive="yes")


@pytest.mark.parametrize("pattern", ["a/b", "a\\b", "C:*.log", "x\x00"])
@pytest.mark.parametrize("operation", [fuzz_search, accurate_search])
def test_search_patterns_cannot_contain_paths(tmp_path, pattern, operation):
    with pytest.raises(ValueError):
        operation(tmp_path, pattern)


def test_search_does_not_hide_permission_errors(tmp_path, monkeypatch):
    original = file_handles.os.scandir

    def reject(path):
        if Path(path) == tmp_path:
            raise PermissionError("fixture permission denied")
        return original(path)

    monkeypatch.setattr(file_handles.os, "scandir", reject)
    with pytest.raises(PermissionError):
        fuzz_search(tmp_path, "missing", True)


def test_find_subdir_component_matching_and_ambiguity(tmp_path):
    for directory in ["a/diag/slot62", "b/trace/slot62", "undiag/slot620", "a/diag/slot62/logs"]:
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError, match="ambiguous.*a.*slot62.*b.*slot62"):
        find_subdir(tmp_path, "slot62")
    assert find_subdir(tmp_path, "diag/slot62") == tmp_path / "a/diag/slot62"
    assert find_subdir(tmp_path, "diag\\slot62") == tmp_path / "a/diag/slot62"
    assert find_subdir(tmp_path, "d?ag/slot[6]2") == tmp_path / "a/diag/slot62"
    assert find_subdir(tmp_path / "a/diag", "diag/slot62", False) == tmp_path / "a/diag/slot62"
    assert find_subdir(tmp_path / "a/diag/slot62", "slot62", False) == tmp_path / "a/diag/slot62"
    with pytest.raises(FileNotFoundError):
        find_subdir(tmp_path, "diag/slot62", False)
    with pytest.raises(FileNotFoundError):
        find_subdir(tmp_path, "no-such-directory")


@pytest.mark.parametrize("pattern", ["", "/diag/slot62", "C:diag", "C:/diag", "\\\\server\\share", ".", "..", "diag/../slot62", "diag/**", "diag//slot62", "x\x00"])
def test_find_subdir_rejects_unsafe_or_ambiguous_patterns(tmp_path, pattern):
    with pytest.raises(ValueError):
        find_subdir(tmp_path, pattern)


def test_find_subdir_excludes_generated_output_tree(tmp_path):
    original = tmp_path / "original/diag/slot62"
    generated = tmp_path / "output/product_log/diag/slot62"
    original.mkdir(parents=True)
    generated.mkdir(parents=True)
    with pytest.raises(ValueError, match="ambiguous"):
        find_subdir(tmp_path, "diag/slot62")
    assert find_subdir(tmp_path, "diag/slot62", exclude_paths=(tmp_path / "output",)) == original
    with pytest.raises(FileNotFoundError):
        find_subdir(tmp_path, "slot62", exclude_paths=(tmp_path,))
    with pytest.raises(TypeError, match="exclude_paths"):
        find_subdir(tmp_path, "slot62", exclude_paths=tmp_path / "output")


def test_no_follow_directory_or_file_symlinks(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    source = write(outside, "foreign.log")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
        (root / "file.log").symlink_to(source)
    except OSError:
        pytest.skip("platform does not permit symlink creation")
    assert not get_handler(root, True)
    with pytest.raises(ValueError, match="links"):
        get_handler(root / "link", True)
    with pytest.raises(ValueError):
        FileHandle(root / "file.log")
    with pytest.raises(FileNotFoundError):
        find_subdir(root, "link")


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_windows_junctions_are_not_traversed(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    write(outside, "foreign.log")
    junction = root / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
    if result.returncode:
        pytest.skip("Windows filesystem does not permit junction creation")
    try:
        assert not get_handler(root, True)
        with pytest.raises(ValueError, match="junctions"):
            get_handler(junction, True)
        with pytest.raises(FileNotFoundError):
            find_subdir(root, "junction")
    finally:
        junction.rmdir()


def test_link_stat_recognizes_windows_reparse_metadata():
    assert file_handles._is_link_stat(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400))
    assert file_handles._is_link_stat(SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0))
    assert not file_handles._is_link_stat(SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0))


@pytest.mark.parametrize("suffix,mode", [(".tar", "w"), (".tar.gz", "w:gz"), (".tgz", "w:gz"), (".tar.bz2", "w:bz2"), (".tar.xz", "w:xz")])
def test_all_tar_types_and_content_detection(tmp_path, suffix, mode):
    source = make_tar(tmp_path / ("fixture" + suffix), mode)
    assert detect_archive(source) == "tar"
    handle = FileHandle(source)
    output = handle.unzip(tmp_path / "output")
    assert output.is_absolute() and output == tmp_path / "output"
    assert (output / "nested/a.log").read_bytes() == b"driver\nfirmware\n"
    handle.verify()
    misnamed = source.rename(tmp_path / "wrong.extension")
    assert detect_archive(misnamed) == "tar"
    assert (FileHandle(misnamed).unzip(tmp_path / "misnamed") / "nested/a.log").is_file()


def test_zip_preserves_relative_structure_and_accepts_empty_destination(tmp_path):
    source = make_zip(tmp_path / "archive.data", [("nested/", b""), ("nested/a.log", b"A"), ("b.log", b"B")])
    assert detect_archive(source) == "zip"
    destination = tmp_path / "empty"
    destination.mkdir()
    assert FileHandle(source).unzip(destination) == destination
    assert (destination / "nested/a.log").read_bytes() == b"A"
    assert (destination / "b.log").read_bytes() == b"B"
    assert source.exists()


def test_tar_explicit_directory_can_follow_an_implicit_parent(tmp_path):
    source = tmp_path / "directories.tar"
    with tarfile.open(source, "w") as archive:
        member = tarfile.TarInfo("./dir/a.log")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"A"))
        for name in ["dir", "."]:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            archive.addfile(member)
    assert (FileHandle(source).unzip(tmp_path / "out") / "dir/a.log").read_bytes() == b"A"


def test_tar_extended_metadata_is_bounded_before_payload_allocation(tmp_path):
    header = tarfile.TarInfo("././@PaxHeader")
    header.type = tarfile.XHDTYPE
    header.size = 1024 * 1024 * 1024
    source = write(tmp_path, "misnamed.data", header.tobuf() + b"\x00" * 512)
    assert detect_archive(source) == "tar"
    with pytest.raises(ValueError, match="metadata"):
        FileHandle(source).unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_normal_tar_pax_metadata_is_supported(tmp_path):
    source = tmp_path / "pax.tar.gz"
    with tarfile.open(source, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("a.log")
        member.size = 1
        member.pax_headers = {"comment": "unicode 日志 " * 512}
        archive.addfile(member, io.BytesIO(b"A"))
    assert detect_archive(source) == "tar"
    assert (FileHandle(source).unzip(tmp_path / "out") / "a.log").read_bytes() == b"A"


@pytest.mark.parametrize("name,expected", [("fixture.log.gz", "fixture.log"), ("fixture.LOG.GZ", "fixture.LOG"), ("wrong.extension", "data")])
def test_single_gzip_output_name_and_payload(tmp_path, name, expected):
    source = tmp_path / name
    with source.open("wb") as stream, gzip.GzipFile(filename="../../unsafe.log", mode="wb", fileobj=stream) as archive:
        archive.write(b"firmware content\n")
    assert detect_archive(source) == "gz"
    output = FileHandle(source).unzip(tmp_path / "output")
    assert [path.name for path in output.iterdir()] == [expected]
    assert (output / expected).read_bytes() == b"firmware content\n"


def test_empty_archives_and_plain_files(tmp_path):
    zip_path = make_zip(tmp_path / "empty.zip", [])
    tar_path = make_tar(tmp_path / "empty.tar", entries=[("empty", b"")])
    with tarfile.open(tar_path, "w"):
        pass
    assert not list(FileHandle(zip_path).unzip(tmp_path / "zip-output").iterdir())
    assert not list(FileHandle(tar_path).unzip(tmp_path / "tar-output").iterdir())
    ordinary = write(tmp_path, "plain.zip", b"not an archive")
    assert detect_archive(ordinary) is None
    with pytest.raises(ValueError, match="unsupported"):
        FileHandle(ordinary).unzip(tmp_path / "plain-output")
    assert not (tmp_path / "plain-output").exists()


@pytest.mark.parametrize("archive_type", ["zip", "tar"])
@pytest.mark.parametrize("name", ["../escape.log", "nested/../../escape.log", "/absolute.log", "C:/absolute.log", "C:relative.log", "\\\\host\\share\\file.log", "nested\\..\\escape.log", "file:stream", "NUL", "dir./file", "file "])
def test_unsafe_archive_members_never_publish(tmp_path, archive_type, name):
    source = tmp_path / ("unsafe." + archive_type)
    entries = [("safe.log", b"SAFE"), (name, b"BAD")]
    (make_zip(source, entries) if archive_type == "zip" else make_tar(source, entries=entries))
    with pytest.raises(ValueError, match="unsafe"):
        FileHandle(source).unzip(tmp_path / "output")
    assert not (tmp_path / "output").exists()
    assert not list(tmp_path.glob(".autoenv-unzip-*"))
    assert not (tmp_path / "escape.log").exists()


@pytest.mark.parametrize("archive_type", ["zip", "tar"])
@pytest.mark.parametrize("names", [["a.log", "a.log"], ["A.log", "a.log"], ["a", "a/b"], ["a/b", "a"], ["Dir/a", "dir/b"]])
def test_archive_member_collisions_never_overwrite(tmp_path, archive_type, names):
    source = tmp_path / ("collision." + archive_type)
    entries = [(name, b"DATA") for name in names]
    if archive_type == "zip":
        with pytest.warns(UserWarning) if names[0] == names[1] else contextlib.nullcontext():
            make_zip(source, entries)
    else:
        make_tar(source, entries=entries)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(FileExistsError, match="collision"):
        FileHandle(source).unzip(output)
    assert list(output.iterdir()) == []
    assert not list(tmp_path.glob(".autoenv-unzip-*"))


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_tar_links_and_special_members_are_rejected(tmp_path, kind):
    source = tmp_path / "unsafe.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo("special")
        member.type = kind
        member.linkname = "../../outside"
        archive.addfile(member)
    with pytest.raises(ValueError, match="links|special"):
        FileHandle(source).unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("file_type", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_zip_links_and_special_members_are_rejected(tmp_path, file_type):
    member = zipfile.ZipInfo("special")
    member.create_system = 3
    member.external_attr = (file_type | 0o644) << 16
    source = make_zip(tmp_path / "unsafe.zip", [(member, b"../../outside")])
    with pytest.raises(ValueError, match="links|special"):
        FileHandle(source).unzip(tmp_path / "out")


@pytest.mark.parametrize("kind", ["zip", "tar", "gz"])
def test_expansion_limits_are_enforced_without_partial_output(tmp_path, kind):
    source = tmp_path / ("size." + kind)
    if kind == "zip":
        make_zip(source, [("a.log", b"a" * 100), ("b.log", b"b")])
    elif kind == "tar":
        make_tar(source, entries=[("a.log", b"a" * 100), ("b.log", b"b")])
    else:
        source.write_bytes(gzip.compress(b"a" * 100))
    with pytest.raises(ValueError, match="expanded bytes"):
        FileHandle(source).unzip(tmp_path / "out-size", max_bytes=5)
    with pytest.raises(ValueError, match="file count"):
        FileHandle(source).unzip(tmp_path / "out-count", max_files=0)
    assert not (tmp_path / "out-size").exists()
    assert not (tmp_path / "out-count").exists()


@pytest.mark.parametrize("kwargs,error", [({"max_files": -1}, ValueError), ({"max_bytes": -1}, ValueError), ({"max_files": True}, TypeError), ({"max_bytes": 1.5}, TypeError)])
def test_expansion_budgets_are_validated(tmp_path, kwargs, error):
    source = make_zip(tmp_path / "budget.zip", [])
    with pytest.raises(error):
        FileHandle(source).unzip(tmp_path / "out", **kwargs)


def test_nonempty_destination_and_file_are_not_overwritten(tmp_path):
    source = make_zip(tmp_path / "archive.zip", [("a.log", b"A")])
    handle = FileHandle(source)
    sentinel = write(tmp_path, "output/sentinel", b"keep")
    with pytest.raises(FileExistsError):
        handle.unzip(sentinel.parent)
    with pytest.raises(FileExistsError):
        handle.unzip(sentinel)
    assert sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize("kind", ["gz", "tar.gz", "tar.bz2", "tar.xz"])
def test_damaged_compressed_streams_fail_before_publication(tmp_path, kind):
    source = tmp_path / ("corrupt." + kind)
    if kind == "gz":
        source.write_bytes(gzip.compress(b"payload" * 1000))
    else:
        make_tar(source, {"tar.gz": "w:gz", "tar.bz2": "w:bz2", "tar.xz": "w:xz"}[kind])
    payload = bytearray(source.read_bytes())
    payload[-5] ^= 0xFF  # Corrupt CRC/footer beyond TAR's end marker.
    source.write_bytes(payload)
    assert detect_archive(source) is not None
    with pytest.raises((ValueError, OSError, EOFError, tarfile.ReadError, lzma.LZMAError)):
        FileHandle(source).unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_zip_crc_failure_cleans_up_output(tmp_path):
    source = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("a.log", b"A" * 128)
    payload = bytearray(source.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", payload, 26)
    payload[30 + name_length + extra_length + 20] ^= 0xFF
    source.write_bytes(payload)
    assert detect_archive(source) == "zip"
    with pytest.raises(zipfile.BadZipFile, match="CRC"):
        FileHandle(source).unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not list(tmp_path.glob(".autoenv-unzip-*"))


@pytest.mark.parametrize("signature,expected", [(b"PK\x03\x04", "zip"), (b"\x1f\x8b", "gz"), (b"BZh9", "tar"), (b"\xfd7zXZ\x00", "tar")])
def test_broken_compression_signatures_are_not_silently_plain_logs(tmp_path, signature, expected):
    source = write(tmp_path, "wrong.extension", signature + b"corrupted")
    assert detect_archive(source) == expected
    with pytest.raises(Exception):
        FileHandle(source).unzip(tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_tar_filename_is_a_final_hint_for_a_broken_header(tmp_path):
    source = write(tmp_path, "corrupt.tar", b"broken tar header")
    assert detect_archive(source) == "tar"
    with pytest.raises(tarfile.ReadError):
        FileHandle(source).unzip(tmp_path / "out")


def test_encrypted_zip_is_explicitly_rejected(tmp_path):
    source = make_zip(tmp_path / "encrypted.zip", [("a.log", b"A")])
    payload = bytearray(source.read_bytes())
    central = payload.index(b"PK\x01\x02")
    payload[6] |= 1  # Local header's general-purpose flag.
    payload[central + 8] |= 1  # Central-directory flag.
    source.write_bytes(payload)
    with pytest.raises(ValueError, match="encrypted"):
        FileHandle(source).unzip(tmp_path / "out")


@pytest.mark.parametrize("kind", ["zip", "tar", "gz"])
def test_cancelled_extraction_propagates_and_does_not_publish(tmp_path, kind):
    source = tmp_path / ("cancel." + kind)
    content = b"large fixture" * 200_000
    if kind == "zip":
        make_zip(source, [("a.log", content)])
    elif kind == "tar":
        make_tar(source, entries=[("a.log", content)])
    else:
        source.write_bytes(gzip.compress(content))
    handle = FileHandle(source)
    calls = 0

    class CancelledFixture(RuntimeError):
        pass

    def check_cancelled():
        nonlocal calls
        calls += 1
        if calls == 6:
            raise CancelledFixture("cancel requested")

    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(CancelledFixture, match="cancel requested"):
        handle.unzip(output, check_cancelled=check_cancelled)
    assert list(output.iterdir()) == []
    assert not list(tmp_path.glob(".autoenv-unzip-*"))


def test_cancelled_handler_verification_precedes_extraction(tmp_path):
    handle = FileHandle(write(tmp_path, "fixture.log", b"A" * 2_000_000))

    def cancel():
        raise RuntimeError("cancel fixture")

    with pytest.raises(RuntimeError, match="cancel fixture"):
        handle.verify(check_cancelled=cancel)
    with pytest.raises(RuntimeError, match="cancel fixture"):
        handle.unzip(tmp_path / "out", check_cancelled=cancel)
    assert not (tmp_path / "out").exists()


def test_copy_and_rename_return_new_immutable_handlers(tmp_path):
    source = create_file(tmp_path / "input", "original.log", b"retained")
    copied = source.move_to(tmp_path / "copies")
    assert source.path.read_bytes() == copied.path.read_bytes() == b"retained"
    assert copied.sha256 == source.sha256
    assert copied.path == tmp_path / "copies/original.log"
    renamed = copied.rename("renamed.log")
    assert renamed.path == tmp_path / "copies/renamed.log"
    assert renamed.path.read_bytes() == b"retained"
    assert not copied.path.exists()
    with pytest.raises(FileNotFoundError):
        copied.verify()
    source.verify()
    renamed.verify()


def test_copy_and_rename_do_not_overwrite_conflicts(tmp_path):
    handle = create_file(tmp_path, "original.log", b"original")
    same = create_file(tmp_path / "copies", "original.log", b"keep")
    with pytest.raises(FileExistsError):
        handle.move_to(same.path.parent)
    with pytest.raises(ValueError, match="source"):
        handle.move_to(tmp_path)
    conflict = create_file(tmp_path, "conflict.log", b"keep")
    with pytest.raises(FileExistsError):
        handle.rename("conflict.log")
    with pytest.raises(ValueError, match="source"):
        handle.rename("original.log")
    assert same.path.read_bytes() == conflict.path.read_bytes() == b"keep"
    handle.verify()
    assert not list(tmp_path.rglob(".autoenv-copy-*"))


@pytest.mark.parametrize("name", ["", "../other.log", "nested/name", "nested\\name", "C:other.log", "..", ".", "nul\x00"])
def test_rename_accepts_only_a_basename(tmp_path, name):
    handle = create_file(tmp_path, "original.log", b"original")
    with pytest.raises(ValueError):
        handle.rename(name)
    handle.verify()


@pytest.mark.parametrize("operation", ["copy", "rename", "unzip"])
def test_new_operations_keep_integrity_verification(tmp_path, operation):
    source = make_zip(tmp_path / "archive.zip", [("a.log", b"A")])
    handle = FileHandle(source)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        if operation == "copy":
            handle.move_to(tmp_path / "copies")
        elif operation == "rename":
            handle.rename("new.zip")
        else:
            handle.unzip(tmp_path / "out")
