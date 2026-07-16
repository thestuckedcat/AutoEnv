from __future__ import annotations

from pathlib import Path

import pytest

from autoenv.command_files import UploadedFileRegistry, generate_sh_file


def test_uploaded_file_registry_resolves_repeated_placeholders() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record("api", "/release/api-2.4.1.tgz")

    assert uploaded_files.resolve("tar -xf S{api} && rm S{api}") == (
        "tar -xf api-2.4.1.tgz && rm api-2.4.1.tgz"
    )


def test_selector_with_braces_can_be_used_as_an_exact_placeholder() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record(r"^api-\d{2}\.tgz$", "/release/api-24.tgz")

    assert uploaded_files.resolve(r"tar -xf S{^api-\d{2}\.tgz$}") == (
        "tar -xf api-24.tgz"
    )


def test_placeholder_must_reference_a_successful_upload() -> None:
    uploaded_files = UploadedFileRegistry()

    with pytest.raises(ValueError, match="successfully uploaded file"):
        uploaded_files.resolve("tar -xf S{api}")


@pytest.mark.parametrize("command", ["echo S{}", "echo S{api", "echo S{{api}}"])
def test_malformed_placeholder_is_rejected(command: str) -> None:
    with pytest.raises(ValueError, match="malformed"):
        UploadedFileRegistry().resolve(command)


def test_selector_cannot_resolve_to_two_actual_names() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record("api", "/release/api-1.tgz")

    with pytest.raises(ValueError, match="ambiguous"):
        uploaded_files.record("api", "/release/api-2.tgz")


def test_uploaded_file_names_are_isolated_by_target() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record("api", "/release/api-a.tgz", target_name="host_a")
    uploaded_files.record("api", "/release/api-b.tgz", target_name="host_b")

    assert uploaded_files.resolve("tar -xf S{api}", target_name="host_a") == (
        "tar -xf api-a.tgz"
    )
    assert uploaded_files.resolve("tar -xf S{api}", target_name="host_b") == (
        "tar -xf api-b.tgz"
    )


def test_unbound_command_rejects_an_ambiguous_upload_target() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record("api", "/a/api.tgz", target_name="host_a")
    uploaded_files.record("api", "/b/api.tgz", target_name="host_b")

    with pytest.raises(ValueError, match="upload target is ambiguous"):
        uploaded_files.resolve("tar -xf S{api}")


def test_script_placeholders_must_share_an_upload_target() -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record("api", "/a/api.tgz", target_name="host_a")
    uploaded_files.record("config", "/b/config.ini", target_name="host_b")

    with pytest.raises(ValueError, match="common target"):
        uploaded_files.resolve_script("tar -xf S{api}\ncp S{config} /etc/app.ini")


def test_generate_sh_file_resolves_a_complete_script_without_modifying_its_layout(
    tmp_path: Path,
) -> None:
    uploaded_files = UploadedFileRegistry()
    uploaded_files.record("api", "/release/api-2.4.1.tgz")
    script = "#!/bin/sh\r\nset -eu\r\ntar -xf S{api}\r\n./install"

    generated = generate_sh_file(
        "install.sh",
        script,
        output_dir=tmp_path,
        uploaded_files=uploaded_files,
    )

    assert generated == tmp_path / "install.sh"
    assert generated.read_bytes() == (
        b"#!/bin/sh\r\nset -eu\r\ntar -xf api-2.4.1.tgz\r\n./install"
    )


def test_generate_sh_file_requires_one_complete_script_string(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="shell script must be a string"):
        generate_sh_file("install.sh", ["echo first", "echo second"], output_dir=tmp_path)  # type: ignore[arg-type]


@pytest.mark.parametrize("file_name", ["install", "../install.sh", "dir/install.sh"])
def test_generate_sh_file_rejects_invalid_filename(
    tmp_path: Path, file_name: str
) -> None:
    with pytest.raises(ValueError):
        generate_sh_file(file_name, "true", output_dir=tmp_path)
