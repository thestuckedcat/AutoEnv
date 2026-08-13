from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoenv.extractor import Extractor
from autoenv.package_manager import HDFSFileEntry, PackageManager
from autoenv.selectors import ExtraFileSelector, MatchSelector, PackageSelector


class FakeRecorder:
    def __init__(self) -> None:
        self.counter = 0
        self.records: list[tuple[str, object]] = []

    def next_operation_id(self) -> str:
        self.counter += 1
        return f"{self.counter:04d}"

    def record_result(self, operation: str, result: object) -> None:
        self.records.append((operation, result))


class FakeHDFS:
    def __init__(
        self,
        directories: dict[str, object] | None = None,
        downloads: dict[str, object] | None = None,
    ) -> None:
        self.directories = directories or {}
        self.downloads = downloads or {}
        self.list_calls: list[str] = []
        self.download_calls: list[tuple[str, str]] = []

    def list_directory(self, path: str) -> list[object]:
        self.list_calls.append(path)
        value = self.directories[path]
        if isinstance(value, BaseException):
            raise value
        return list(value)  # type: ignore[arg-type]

    def download_file(self, remote_path: str, local_path: str) -> None:
        self.download_calls.append((remote_path, local_path))
        value = self.downloads[remote_path]
        if isinstance(value, BaseException):
            raise value
        if callable(value):
            value(remote_path, local_path)
            return
        Path(local_path).write_bytes(value)  # type: ignore[arg-type]


NOW = datetime(2025, 1, 1, 12, 0, 0)


def entry(
    name: str,
    *,
    directory: bool = False,
    size: int = 0,
    age_minutes: int = 0,
) -> HDFSFileEntry:
    return HDFSFileEntry(
        name=name,
        is_directory=directory,
        length=size,
        modification_time=NOW + timedelta(minutes=age_minutes),
    )


def write_config(tmp_path: Path, entries: object) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def config_entry(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "sdk",
        "link": "/releases/direct",
        "base_link": "",
        "image_name": r"^sdk-.*\.(?:tar\.gz|tgz)$",
        "target_file": [],
    }
    value.update(overrides)
    return value


def make_manager(
    tmp_path: Path,
    hdfs: FakeHDFS,
    entries: object | None = None,
) -> tuple[PackageManager, FakeRecorder, Path]:
    recorder = FakeRecorder()
    package_dir = tmp_path / "packages"
    manager = PackageManager(
        write_config(tmp_path, entries or [config_entry()]),
        package_dir,
        "run-1",
        recorder,  # type: ignore[arg-type]
        hdfs,
    )
    return manager, recorder, package_dir


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def make_tar(
    path: Path,
    members: dict[str, bytes | None],
    *,
    links: dict[str, str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            else:
                info.size = len(content)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(content))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            archive.addfile(info)
    return path


def make_extractor(
    package_dir: Path,
    patterns: dict[str, str] | None = None,
    *,
    runner: object | None = None,
    bash: str | None = None,
) -> tuple[Extractor, FakeRecorder, list[str]]:
    recorder = FakeRecorder()
    pattern_calls: list[str] = []

    def image_pattern_for(name: str) -> str:
        pattern_calls.append(name)
        return (patterns or {"sdk": r"^sdk-.*\.(?:tar\.gz|tgz|run)$"})[name]

    extractor = Extractor(
        package_dir,
        "run-1",
        recorder,  # type: ignore[arg-type]
        image_pattern_for,
        command_runner=runner,  # type: ignore[arg-type]
        bash_executable=bash,
    )
    return extractor, recorder, pattern_calls


def assert_recorded(recorder: FakeRecorder, operation: str, result: object) -> None:
    assert recorder.records == [(operation, result)]


def test_legacy_config_compatibility_and_normalization(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        [
            config_entry(
                name=" sdk ",
                link=" /direct/path/// ",
                image_name=r"sdk-\d+\.tgz",
                target_file=" boot.bin ",
            ),
            config_entry(
                name="tools",
                link="",
                base_link=" /base/path/// ",
                image_name=r"tools\.tar\.gz$",
                target_file=[" a ", 7, "", None],
            ),
        ],
    )
    manager = PackageManager(path, tmp_path / "packages", "run", FakeRecorder())  # type: ignore[arg-type]

    sdk = manager.get_spec(" sdk ")
    tools = manager.get_spec("tools")
    assert (sdk.name, sdk.link, sdk.target_file) == (
        "sdk",
        "/direct/path",
        ["boot.bin"],
    )
    assert (tools.base_link, tools.target_file) == (
        "/base/path",
        ["a", "7", "None"],
    )
    assert manager.image_pattern_for("sdk") == r"sdk-\d+\.tgz"
    with pytest.raises(KeyError, match="package config does not exist"):
        manager.get_spec("missing")
    with pytest.raises(ValueError, match="must not be empty"):
        manager.get_spec("  ")


@pytest.mark.parametrize(
    ("raw", "exception", "message"),
    [
        ({"name": "sdk"}, ValueError, "top level must be an array"),
        (["not-an-object"], TypeError, "entry 0 must be an object"),
        ([config_entry(name=3)], TypeError, "name must be a string"),
        ([config_entry(name=" ")], ValueError, "name must not be empty"),
        ([config_entry(link=4)], TypeError, "link must be a string"),
        ([config_entry(image_name="[")], ValueError, "invalid image_name regex"),
        (
            [config_entry(), config_entry()],
            ValueError,
            "duplicate package config name",
        ),
        ([config_entry(target_file={})], ValueError, "target_file must be"),
    ],
)
def test_config_validation_errors(
    tmp_path: Path,
    raw: object,
    exception: type[Exception],
    message: str,
) -> None:
    with pytest.raises(exception, match=message):
        PackageManager(
            write_config(tmp_path, raw),
            tmp_path / "packages",
            "run",
            FakeRecorder(),  # type: ignore[arg-type]
        )


def test_empty_config_paths_are_allowed_when_download_uses_manual_override(
    tmp_path: Path,
) -> None:
    payload = b"manual"
    hdfs = FakeHDFS(
        directories={"/manual": [entry("sdk-1.tgz", size=len(payload))]},
        downloads={"/manual/sdk-1.tgz": payload},
    )
    manager, _, package_dir = make_manager(
        tmp_path,
        hdfs,
        [config_entry(link="", base_link="")],
    )

    result = manager.download(
        PackageSelector("sdk"),
        path_override="/manual",
        path_mode="override",
    )

    assert result.success is True
    assert (package_dir / "sdk-1.tgz").read_bytes() == payload
    with pytest.raises(ValueError, match="does not define base_link"):
        manager.download(PackageSelector("sdk"), path_mode="base_link_newest")


def test_malformed_json_config_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[{", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON config"):
        PackageManager(path, tmp_path / "packages", "run", FakeRecorder())  # type: ignore[arg-type]


def test_link_resolution_override_and_latest_matching_file(tmp_path: Path) -> None:
    payload = b"newest"
    hdfs = FakeHDFS(
        {
            "/override": [
                entry("sdk-old.tgz", size=3, age_minutes=1),
                entry("sdk-new.tgz", size=len(payload), age_minutes=3),
                entry("sdk-directory.tgz", directory=True, age_minutes=9),
                entry("other.txt", size=1, age_minutes=10),
            ]
        },
        {"/override/sdk-new.tgz": payload},
    )
    manager, recorder, package_dir = make_manager(tmp_path, hdfs)

    result = manager.download(PackageSelector("sdk"), " /override/// ")

    assert result.success and result.status == "success"
    assert result.remote_dir == "/override"
    assert result.remote_file == "/override/sdk-new.tgz"
    assert result.remote_size == len(payload)
    assert result.local_size == len(payload)
    assert result.size_verified is True
    assert result.local_md5_before is None
    assert result.local_md5_after == md5(payload)
    assert result.md5_changed is True
    assert (package_dir / "sdk-new.tgz").read_bytes() == payload
    assert hdfs.list_calls == ["/override"]
    assert hdfs.download_calls[0][1].endswith("sdk-new.tgz.part")
    assert not (package_dir / "sdk-new.tgz.part").exists()
    assert_recorded(recorder, "DOWNLOAD", result)


def test_direct_link_is_used_without_override(tmp_path: Path) -> None:
    payload = b"direct"
    hdfs = FakeHDFS(
        {"/releases/direct": [entry("sdk-1.tgz", size=len(payload))]},
        {"/releases/direct/sdk-1.tgz": payload},
    )
    manager, _, _ = make_manager(tmp_path, hdfs)

    assert manager.download(PackageSelector("sdk")).success
    assert hdfs.list_calls == ["/releases/direct"]


def test_base_link_uses_latest_child_and_tries_newest_candidates_in_order(
    tmp_path: Path,
) -> None:
    payload = b"candidate"
    hdfs = FakeHDFS(
        {
            "/base": [
                entry("build-old", directory=True, age_minutes=1),
                entry("README", age_minutes=50),
                entry("build-new", directory=True, age_minutes=4),
            ],
            "/base/build-new": [
                entry("newest-backup", directory=True, age_minutes=5),
                entry("ordinary", directory=True, age_minutes=100),
                entry("NEWEST-current", directory=True, age_minutes=8),
            ],
            "/base/build-new/NEWEST-current": [entry("wrong.txt", size=1)],
            "/base/build-new/newest-backup": [
                entry("sdk-a.tgz", size=1, age_minutes=2),
                entry("sdk-b.tgz", size=len(payload), age_minutes=9),
            ],
        },
        {"/base/build-new/newest-backup/sdk-b.tgz": payload},
    )
    manager, _, package_dir = make_manager(
        tmp_path,
        hdfs,
        [config_entry(link="", base_link="/base")],
    )

    result = manager.download(PackageSelector("sdk"))

    assert result.success
    assert result.remote_dir == "/base/build-new/newest-backup"
    assert result.remote_file.endswith("/sdk-b.tgz")
    assert (package_dir / "sdk-b.tgz").read_bytes() == payload
    assert hdfs.list_calls == [
        "/base",
        "/base/build-new",
        "/base/build-new/NEWEST-current",
        "/base/build-new/newest-backup",
    ]


def test_base_link_skips_a_newest_candidate_that_disappears(tmp_path: Path) -> None:
    payload = b"survivor"
    hdfs = FakeHDFS(
        directories={
            "/base": [entry("build", directory=True, age_minutes=2)],
            "/base/build": [
                entry("newest-a", directory=True, age_minutes=3),
                entry("newest-b", directory=True, age_minutes=2),
            ],
            "/base/build/newest-a": FileNotFoundError("rotated"),
            "/base/build/newest-b": [entry("sdk-1.tgz", size=len(payload))],
        },
        downloads={"/base/build/newest-b/sdk-1.tgz": payload},
    )
    manager, _, package_dir = make_manager(
        tmp_path,
        hdfs,
        [config_entry(link="", base_link="/base")],
    )

    result = manager.download(PackageSelector("sdk"), path_mode="base_link_newest")

    assert result.success is True
    assert (package_dir / "sdk-1.tgz").read_bytes() == payload
    assert hdfs.list_calls == [
        "/base",
        "/base/build",
        "/base/build/newest-a",
        "/base/build/newest-b",
    ]


@pytest.mark.parametrize(
    ("directories", "expected_status"),
    [
        ({"/missing": FileNotFoundError("gone")}, "remote_directory_not_found"),
        ({"/base": [entry("file", directory=False)]}, "newest_directory_not_found"),
        (
            {
                "/base": [entry("build", directory=True)],
                "/base/build": [entry("ordinary", directory=True)],
            },
            "newest_directory_not_found",
        ),
        (
            {
                "/base": [entry("build", directory=True)],
                "/base/build": [entry("newest", directory=True)],
                "/base/build/newest": [entry("other.zip")],
            },
            "remote_file_not_found",
        ),
    ],
)
def test_remote_lookup_failure_statuses(
    tmp_path: Path,
    directories: dict[str, object],
    expected_status: str,
) -> None:
    root = next(iter(directories))
    if root == "/missing":
        spec = config_entry(link="/missing", base_link="")
    else:
        spec = config_entry(link="", base_link="/base")
    manager, recorder, _ = make_manager(tmp_path, FakeHDFS(directories), [spec])

    result = manager.download(PackageSelector("sdk"))

    assert not result.success
    assert result.status == expected_status
    assert result.error_type == expected_status.upper()
    assert_recorded(recorder, "DOWNLOAD", result)


def test_generic_listing_error_is_connection_failed(tmp_path: Path) -> None:
    manager, _, _ = make_manager(
        tmp_path,
        FakeHDFS({"/releases/direct": OSError("offline")}),
    )
    result = manager.download(PackageSelector("sdk"))
    assert (result.success, result.status, result.error_type) == (
        False,
        "connection_failed",
        "CONNECTION_FAILED",
    )


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (TimeoutError("slow"), "download_timeout"),
        (OSError("broken stream"), "download_failed"),
    ],
)
def test_download_failure_status_and_part_cleanup(
    tmp_path: Path, failure: Exception, status: str
) -> None:
    remote = "/releases/direct/sdk-1.tgz"
    hdfs = FakeHDFS(
        {"/releases/direct": [entry("sdk-1.tgz", size=4)]},
        {remote: failure},
    )
    manager, recorder, package_dir = make_manager(tmp_path, hdfs)
    package_dir.mkdir()
    final = package_dir / "sdk-1.tgz"
    final.write_bytes(b"keep")
    (package_dir / "sdk-1.tgz.part").write_bytes(b"stale")

    result = manager.download(PackageSelector("sdk"))

    assert result.status == status and not result.success
    assert result.error_type == status.upper()
    assert final.read_bytes() == b"keep"
    assert not (package_dir / "sdk-1.tgz.part").exists()
    assert_recorded(recorder, "DOWNLOAD", result)


def test_size_failure_keeps_existing_file_and_removes_part(tmp_path: Path) -> None:
    remote = "/releases/direct/sdk-1.tgz"
    hdfs = FakeHDFS(
        {"/releases/direct": [entry("sdk-1.tgz", size=100)]},
        {remote: b"short"},
    )
    manager, _, package_dir = make_manager(tmp_path, hdfs)
    package_dir.mkdir()
    final = package_dir / "sdk-1.tgz"
    final.write_bytes(b"original")

    result = manager.download(PackageSelector("sdk"))

    assert result.status == "size_verification_failed"
    assert result.local_size == 5 and result.size_verified is False
    assert result.local_md5_before == md5(b"original")
    assert result.local_md5_after is None
    assert final.read_bytes() == b"original"
    assert not final.with_name(final.name + ".part").exists()


@pytest.mark.parametrize(
    ("before", "after", "changed"),
    [(b"old", b"new", True), (b"same", b"same", False)],
)
def test_successful_download_reports_md5_and_atomically_overwrites(
    tmp_path: Path, before: bytes, after: bytes, changed: bool
) -> None:
    remote = "/releases/direct/sdk-1.tgz"
    hdfs = FakeHDFS(
        {"/releases/direct": [entry("sdk-1.tgz", size=len(after))]},
        {remote: after},
    )
    manager, _, package_dir = make_manager(tmp_path, hdfs)
    package_dir.mkdir()
    final = package_dir / "sdk-1.tgz"
    final.write_bytes(before)

    result = manager.download(PackageSelector("sdk"))

    assert result.success and result.local_existed
    assert result.local_md5_before == md5(before)
    assert result.local_md5_after == md5(after)
    assert result.md5_changed is changed
    assert result.size_verified
    assert final.read_bytes() == after


def test_replace_failure_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = "/releases/direct/sdk-1.tgz"
    hdfs = FakeHDFS(
        {"/releases/direct": [entry("sdk-1.tgz", size=3)]},
        {remote: b"new"},
    )
    manager, _, package_dir = make_manager(tmp_path, hdfs)
    package_dir.mkdir()
    final = package_dir / "sdk-1.tgz"
    final.write_bytes(b"old")

    def fail_replace(_source: object, _destination: object) -> None:
        raise OSError("locked")

    monkeypatch.setattr("autoenv.package_manager.os.replace", fail_replace)
    result = manager.download(PackageSelector("sdk"))

    assert result.status == "local_replace_failed"
    assert result.error_type == "LOCAL_REPLACE_FAILED"
    assert final.read_bytes() == b"old"
    assert not final.with_name(final.name + ".part").exists()


def test_invalid_remote_filename_is_rejected_before_download(tmp_path: Path) -> None:
    hdfs = FakeHDFS(
        {"/releases/direct": [entry("sdk-/evil.tgz", size=1)]},
    )
    manager, _, _ = make_manager(tmp_path, hdfs)
    result = manager.download(PackageSelector("sdk"))
    assert result.status == "download_failed"
    assert result.error_type == "INVALID_REMOTE_FILENAME"
    assert hdfs.download_calls == []


def test_download_call_shape_validation(tmp_path: Path) -> None:
    manager, recorder, _ = make_manager(tmp_path, FakeHDFS())
    with pytest.raises(TypeError, match="PackageSelector only"):
        manager.download(MatchSelector("sdk"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="path_override"):
        manager.download(PackageSelector("sdk"), 3)  # type: ignore[arg-type]
    assert recorder.records == []


@pytest.mark.parametrize("extension", ["tar.gz", "tgz"])
def test_tar_extensions_extract_exact_file_and_record(
    tmp_path: Path, extension: str
) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(
        package_dir / f"sdk-1.{extension}",
        {"root/bin/tool.txt": b"tool"},
    )
    extractor, recorder, pattern_calls = make_extractor(package_dir)

    result = extractor.extract(ExtraFileSelector(archive.name), target_file="root/bin/tool.txt")

    assert result.success and result.status == "success"
    assert result.selector_type == "extra_file"
    assert result.target_type == "file"
    assert Path(result.destination or "").read_bytes() == b"tool"
    assert result.md5_before is None and result.md5_after == md5(b"tool")
    assert result.destination_existed is False and result.content_changed is True
    assert pattern_calls == []
    assert_recorded(recorder, "EXTRACT", result)
    assert not list(package_dir.glob(".autoenv_extract_*"))


def test_package_extra_and_match_sources_are_resolved_offline(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    make_tar(package_dir / "sdk-1.tgz", {"package.txt": b"package"})
    make_tar(package_dir / "extra.tgz", {"extra.txt": b"extra"})
    make_tar(package_dir / "a-match.tgz", {"match.txt": b"first"})
    make_tar(package_dir / "z-match.tgz", {"match.txt": b"last"})
    (package_dir / "0-match.tgz.part").write_bytes(b"ignored")
    extractor, recorder, pattern_calls = make_extractor(
        package_dir, {"sdk": r"^sdk-\d+\.tgz$"}
    )

    package_result = extractor.extract(PackageSelector("sdk"), target_file="package.txt")
    extra_result = extractor.extract(ExtraFileSelector("extra.tgz"), target_file="extra.txt")
    match_result = extractor.extract(MatchSelector(r"match\.tgz$"), target_file="match.txt")

    assert package_result.success and package_result.selector_type == "package"
    assert extra_result.success and extra_result.selector_type == "extra_file"
    assert match_result.success and match_result.selector_type == "match"
    assert Path(match_result.source_file or "").name == "a-match.tgz"
    assert (package_dir / "package.txt").read_bytes() == b"package"
    assert (package_dir / "extra.txt").read_bytes() == b"extra"
    assert (package_dir / "match.txt").read_bytes() == b"first"
    assert pattern_calls == ["sdk"]
    assert [operation for operation, _ in recorder.records] == [
        "EXTRACT",
        "EXTRACT",
        "EXTRACT",
    ]


def test_package_source_ambiguity_is_a_recorded_resolution_failure(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    make_tar(package_dir / "sdk-1.tgz", {"file.txt": b"one"})
    make_tar(package_dir / "sdk-2.tgz", {"file.txt": b"two"})
    extractor, recorder, _ = make_extractor(package_dir, {"sdk": r"^sdk-\d+\.tgz$"})

    result = extractor.extract(PackageSelector("sdk"), target_file="file.txt")

    assert not result.success and result.status == "ambiguous_local_file"
    assert result.error_type == "AMBIGUOUS_LOCAL_FILE"
    assert result.source_file is None
    assert_recorded(recorder, "EXTRACT", result)


def test_file_target_uses_unique_basename_fallback(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(package_dir / "archive.tgz", {"prefix/deep/config.ini": b"ok"})
    extractor, _, _ = make_extractor(package_dir)

    result = extractor.extract(
        ExtraFileSelector(archive.name), target_file="expected/config.ini"
    )

    assert result.success
    assert (package_dir / "config.ini").read_bytes() == b"ok"


def test_file_target_fallback_reports_not_found_and_ambiguity(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(
        package_dir / "archive.tgz",
        {"one/config.ini": b"one", "two/config.ini": b"two"},
    )
    extractor, _, _ = make_extractor(package_dir)

    ambiguous = extractor.extract(
        ExtraFileSelector(archive.name), target_file="missing/config.ini"
    )
    missing = extractor.extract(
        ExtraFileSelector(archive.name), target_file="missing/absent.ini"
    )

    assert (ambiguous.status, ambiguous.error_type) == (
        "multiple_target_files_found",
        "MULTIPLE_TARGET_FILES_FOUND",
    )
    assert "one/config.ini" in (ambiguous.error_message or "")
    assert "two/config.ini" in (ambiguous.error_message or "")
    assert (missing.status, missing.error_type) == (
        "target_file_not_found",
        "TARGET_FILE_NOT_FOUND",
    )


def test_directory_target_exact_and_overwrite_summary(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(
        package_dir / "archive.tar.gz",
        {"root/assets/a.txt": b"A", "root/assets/sub/b.txt": b"B"},
    )
    destination = package_dir / "assets"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_bytes(b"old")
    extractor, _, _ = make_extractor(package_dir)

    result = extractor.extract(
        ExtraFileSelector(archive.name), target_dir="root/assets"
    )

    assert result.success and result.destination_existed
    assert result.tree_md5_before is not None
    assert result.tree_md5_after is not None
    assert result.tree_md5_before != result.tree_md5_after
    assert result.file_count_before == 1 and result.file_count_after == 2
    assert result.content_changed is True
    assert not (destination / "old.txt").exists()
    assert (destination / "a.txt").read_bytes() == b"A"
    assert (destination / "sub" / "b.txt").read_bytes() == b"B"


def test_directory_target_uses_unique_basename_fallback(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(
        package_dir / "archive.tgz",
        {"prefix/deep/assets/file.txt": b"asset"},
    )
    extractor, _, _ = make_extractor(package_dir)

    result = extractor.extract(
        ExtraFileSelector(archive.name), target_dir="expected/assets"
    )

    assert result.success
    assert (package_dir / "assets" / "file.txt").read_bytes() == b"asset"
    assert result.file_count_before is None and result.file_count_after == 1


def test_directory_target_fallback_reports_ambiguity(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(
        package_dir / "archive.tgz",
        {"one/assets/a": b"1", "two/assets/b": b"2"},
    )
    extractor, _, _ = make_extractor(package_dir)

    result = extractor.extract(
        ExtraFileSelector(archive.name), target_dir="missing/assets"
    )

    assert (result.status, result.error_type) == (
        "multiple_target_dirs_found",
        "MULTIPLE_TARGET_DIRS_FOUND",
    )
    assert "one/assets" in (result.error_message or "")
    assert "two/assets" in (result.error_message or "")


def test_file_overwrite_summary_reports_md5_and_change(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(package_dir / "archive.tgz", {"nested/config.ini": b"new"})
    destination = package_dir / "config.ini"
    destination.write_bytes(b"old")
    extractor, _, _ = make_extractor(package_dir)

    result = extractor.extract(
        ExtraFileSelector(archive.name), target_file="nested/config.ini"
    )

    assert result.success and result.destination_existed
    assert result.md5_before == md5(b"old")
    assert result.md5_after == md5(b"new")
    assert result.content_changed is True
    assert destination.read_bytes() == b"new"


@pytest.mark.parametrize(
    ("member", "error_type"),
    [
        ("../escaped.txt", "ARCHIVE_PATH_TRAVERSAL"),
        ("/absolute.txt", "ARCHIVE_PATH_TRAVERSAL"),
        (r"C:\escaped.txt", "ARCHIVE_PATH_TRAVERSAL"),
    ],
)
def test_tar_path_traversal_is_rejected(
    tmp_path: Path, member: str, error_type: str
) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(package_dir / "unsafe.tgz", {member: b"escape"})
    extractor, recorder, _ = make_extractor(package_dir)

    result = extractor.extract(
        ExtraFileSelector(archive.name), target_file="escaped.txt"
    )

    assert not result.success and result.status == "unsafe_archive"
    assert result.error_type == error_type
    assert not (tmp_path / "escaped.txt").exists()
    assert not (package_dir / "escaped.txt").exists()
    assert_recorded(recorder, "EXTRACT", result)


def test_tar_link_is_rejected(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    archive = make_tar(
        package_dir / "unsafe.tgz",
        {"safe.txt": b"safe"},
        links={"link.txt": "safe.txt"},
    )
    extractor, _, _ = make_extractor(package_dir)

    result = extractor.extract(ExtraFileSelector(archive.name), target_file="safe.txt")

    assert result.status == "unsafe_archive"
    assert result.error_type == "ARCHIVE_LINK_NOT_ALLOWED"


def test_run_archive_uses_injected_runner_without_executing_package(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    source = package_dir / "installer.run"
    source.write_bytes(b"not a real executable")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs))
        extract_arg = next(item for item in command if item.startswith("--extract="))
        destination = Path(extract_arg.split("=", 1)[1])
        (destination / "payload").mkdir()
        (destination / "payload" / "tool.bin").write_bytes(b"run-content")
        return SimpleNamespace(returncode=0, stderr="")

    extractor, recorder, _ = make_extractor(
        package_dir, runner=runner, bash="fake-bash"
    )

    result = extractor.extract(
        ExtraFileSelector(source.name), target_file="payload/tool.bin"
    )

    assert result.success
    assert (package_dir / "tool.bin").read_bytes() == b"run-content"
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == ["fake-bash", str(source.resolve()), "--noexec"]
    assert command[3].startswith("--extract=")
    assert kwargs == {
        "check": True,
        "capture_output": True,
        "text": True,
        "timeout": 300.0,
    }
    assert_recorded(recorder, "EXTRACT", result)


def test_run_runner_nonzero_result_is_recorded_as_extraction_failure(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    source = package_dir / "installer.run"
    source.write_bytes(b"fake")
    calls = 0

    def runner(_command: list[str], **_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=7, stderr="failed")

    extractor, recorder, _ = make_extractor(package_dir, runner=runner, bash="bash-test")
    result = extractor.extract(
        ExtraFileSelector(source.name), target_file="payload.bin"
    )

    assert calls == 1
    assert not result.success and result.status == "extraction_failed"
    assert result.error_type == "EXTRACTION_FAILED"
    assert "exit status 7" in (result.error_message or "")
    assert_recorded(recorder, "EXTRACT", result)


def test_extractor_validates_target_shape_before_recording(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    extractor, recorder, _ = make_extractor(package_dir)

    with pytest.raises(ValueError, match="exactly one"):
        extractor.extract(ExtraFileSelector("x.tgz"))
    with pytest.raises(ValueError, match="exactly one"):
        extractor.extract(
            ExtraFileSelector("x.tgz"), target_file="a", target_dir="b"
        )
    with pytest.raises(ValueError, match="must not contain"):
        extractor.extract(ExtraFileSelector("x.tgz"), target_file="../escape")
    assert recorder.records == []


def test_invalid_zip_and_missing_source_are_recorded(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    (package_dir / "plain.zip").write_bytes(b"zip")
    extractor, recorder, _ = make_extractor(package_dir)

    invalid_zip = extractor.extract(
        ExtraFileSelector("plain.zip"), target_file="payload"
    )
    missing = extractor.extract(
        ExtraFileSelector("missing.tgz"), target_file="payload"
    )

    assert (invalid_zip.status, invalid_zip.error_type) == (
        "extraction_failed",
        "EXTRACTION_FAILED",
    )
    assert (missing.status, missing.error_type) == (
        "local_file_not_found",
        "LOCAL_FILE_NOT_FOUND",
    )
    assert len(recorder.records) == 2


def test_zip_extracts_one_file_and_rejects_traversal(tmp_path: Path) -> None:
    import zipfile

    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    archive = package_dir / "logs.zip"
    with zipfile.ZipFile(archive, "w") as value:
        value.writestr("nested/device.log", "ready")
    extractor, _, _ = make_extractor(package_dir)
    result = extractor.extract(ExtraFileSelector("logs.zip"), target_file="nested/device.log")
    assert result.success
    assert (package_dir / "device.log").read_text() == "ready"

    unsafe = package_dir / "unsafe.zip"
    with zipfile.ZipFile(unsafe, "w") as value:
        value.writestr("../escape.log", "bad")
    failed = extractor.extract(ExtraFileSelector("unsafe.zip"), target_file="escape.log")
    assert (failed.status, failed.error_type) == ("unsafe_archive", "ARCHIVE_PATH_TRAVERSAL")
