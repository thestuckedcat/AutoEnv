from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from autoenv.recorder import RunRecorder, mask_sensitive
from autoenv.results import (
    CommandPhase,
    CommandProtocol,
    CommandResult,
    CommandStatus,
    result_to_dict,
)
from autoenv.selectors import (
    SelectorResolutionError,
    describe_selector,
    extra_file,
    match,
    package,
    resolve_local_file,
    validate_archive_target,
)


def _command_result(**changes: object) -> CommandResult:
    values: dict[str, object] = {
        "run_id": "run-1",
        "operation_id": "0001",
        "protocol": CommandProtocol.SSH,
        "target_name": "dut",
        "command": "echo ready",
        "status": CommandStatus.SUCCESS,
        "phase": CommandPhase.COMPLETE,
        "exit_code": 0,
        "stdout": "ready",
        "stderr": "warning",
        "raw_output": "ready\nwarning",
        "started_at": datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 7, 15, 1, 2, 4, tzinfo=timezone.utc),
        "duration_ms": 1000,
    }
    values.update(changes)
    return CommandResult(**values)  # type: ignore[arg-type]


def test_command_result_properties_failure_copy_and_serialization(tmp_path):
    result = _command_result()

    assert result.success is True
    assert result.timed_out is False
    assert result.output == "ready\nwarning"
    assert replace(result, stderr="").output == "ready"
    assert replace(result, stdout="").output == "warning"

    failed = result.with_failure("  READY marker missing  ")
    assert failed is not result
    assert result.status == CommandStatus.SUCCESS
    assert failed.status == CommandStatus.COMMAND_FAILED
    assert failed.phase == CommandPhase.COMPLETE
    assert failed.error_type == "BUSINESS_RULE_FAILED"
    assert failed.error_message == "READY marker missing"
    assert failed.with_failure("bad", error_type="CUSTOM").error_type == "CUSTOM"
    with pytest.raises(ValueError, match="must not be empty"):
        result.with_failure("  ")

    timed_out = replace(result, status=CommandStatus.TIMEOUT)
    assert timed_out.timed_out is True
    serialized = result_to_dict(
        {"result": timed_out, "directory": tmp_path, "items": (CommandPhase.CONNECT,)}
    )
    assert serialized["result"]["protocol"] == "ssh"
    assert serialized["result"]["status"] == "timeout"
    assert serialized["result"]["started_at"] == "2026-07-15T01:02:03+00:00"
    assert serialized["directory"] == str(tmp_path)
    assert serialized["items"] == ["connect"]
    json.dumps(serialized)


def test_all_selectors_resolve_and_match_has_a_stable_first_item(tmp_path):
    package_dir = tmp_path / "package-resolution"
    package_dir.mkdir()
    (package_dir / "firmware-001.bin").write_bytes(b"image")
    package_selector = package("  firmware  ")

    resolved_package = resolve_local_file(
        package_selector,
        package_dir,
        lambda name: rf"^{name}-\d+\.bin$" if name == "firmware" else r"$^",
    )
    assert resolved_package.path == (package_dir / "firmware-001.bin").resolve()
    assert (resolved_package.selector_type, resolved_package.selector) == (
        "package",
        "firmware",
    )
    assert describe_selector(package_selector) == ("package", "firmware")

    extra_dir = tmp_path / "extra-resolution"
    extra_dir.mkdir()
    (extra_dir / "manual.tar.gz").write_bytes(b"manual")
    extra_selector = extra_file(" manual.tar.gz ")
    resolved_extra = resolve_local_file(extra_selector, extra_dir, lambda _: r"$^")
    assert resolved_extra.path == (extra_dir / "manual.tar.gz").resolve()
    assert describe_selector(extra_selector) == ("extra_file", "manual.tar.gz")

    match_dir = tmp_path / "match-resolution"
    match_dir.mkdir()
    for name in ("z.bin", "b.bin", "A.bin", "00.bin.part"):
        (match_dir / name).write_bytes(name.encode())
    match_selector = match(r"^[A-Za-z]\.bin$")
    resolved_match = resolve_local_file(match_selector, match_dir, lambda _: r"$^")
    assert resolved_match.path.name == "A.bin"
    assert describe_selector(match_selector) == ("match", r"^[A-Za-z]\.bin$")


def test_selectors_reject_unsafe_paths_and_package_ambiguity(tmp_path):
    for unsafe in ("../escape.bin", "nested/file.bin", r"C:\escape.bin"):
        with pytest.raises(ValueError):
            extra_file(unsafe)

    assert validate_archive_target(r"folder\file.bin", "target") == "folder/file.bin"
    for unsafe_target in ("../file.bin", "/absolute/file.bin", r"C:\absolute.bin"):
        with pytest.raises(ValueError):
            validate_archive_target(unsafe_target, "target")

    package_dir = tmp_path / "ambiguous"
    package_dir.mkdir()
    (package_dir / "firmware-1.bin").write_bytes(b"one")
    (package_dir / "firmware-2.bin").write_bytes(b"two")
    with pytest.raises(SelectorResolutionError) as raised:
        resolve_local_file(
            package("firmware"), package_dir, lambda _: r"^firmware-\d+\.bin$"
        )
    assert raised.value.code == "AMBIGUOUS_LOCAL_FILE"
    assert "firmware-1.bin, firmware-2.bin" in str(raised.value)


def test_match_rejects_symlink_that_escapes_package_directory(tmp_path):
    package_dir = tmp_path / "packages"
    package_dir.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    link = package_dir / "linked.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("creating symbolic links is not permitted on this Windows host")

    with pytest.raises(SelectorResolutionError) as raised:
        resolve_local_file(match(r"^linked\.bin$"), package_dir, lambda _: "")

    assert raised.value.code == "LOCAL_FILE_OUTSIDE_PACKAGE_DIR"


def test_recorder_numbers_operations_writes_json_and_masks_secrets(tmp_path):
    console = io.StringIO()
    log_path = tmp_path / "logs" / "run.log"
    json_path = tmp_path / "state" / "params.json"
    fixed_now = lambda: datetime(2026, 7, 15, 9, 30, tzinfo=timezone.utc)

    with RunRecorder(log_path, console=console, now=fixed_now) as recorder:
        assert recorder.final_operation_id is None
        assert [recorder.next_operation_id(), recorder.next_operation_id()] == [
            "0001",
            "0002",
        ]
        assert recorder.final_operation_id == "0002"
        recorder.record_result("COMMAND", _command_result(operation_id="0002"))
        recorder.write_json(
            json_path,
            {
                "password": "plain-password",
                "nested": {"api_token": "plain-token", "username": "root"},
                "empty_secret": "",
            },
            mask=True,
        )

    log_text = log_path.read_text(encoding="utf-8")
    payload = json.loads(log_text.split(" COMMAND ", 1)[1])
    assert payload["operation_id"] == "0002"
    assert payload["status"] == "success"
    console_text = console.getvalue()
    assert "=== COMMAND [SUCCESS] ===" in console_text
    assert "  operation_id: 0002" in console_text
    assert "  command:\n    echo ready\n" in console_text
    assert "  result: status=success phase=complete exit_code=0 duration_ms=1000" in console_text
    assert '"raw_output"' not in console_text

    stored = json.loads(json_path.read_text(encoding="utf-8"))
    assert stored == {
        "empty_secret": "",
        "nested": {"api_token": "******", "username": "root"},
        "password": "******",
    }
    assert not json_path.with_name("params.json.tmp").exists()
    assert mask_sensitive([{"passwd": "x"}, {"secret": None}]) == [
        {"passwd": "******"},
        {"secret": None},
    ]


def test_recorder_formats_upload_failure_as_readable_console_block(tmp_path):
    console = io.StringIO()
    log_path = tmp_path / "run.log"
    fixed_now = lambda: datetime(2026, 7, 15, 9, 30, tzinfo=timezone.utc)
    result = {
        "operation_id": "0007",
        "protocol": "sftp",
        "target_name": "udk_host",
        "resolved_local_file": r"D:\packages\udk_install.sh",
        "remote_file": "/root/autoEnv/udk_install.sh",
        "status": "protocol_error",
        "success": False,
        "duration_ms": 48,
        "local_md5": "abc",
        "remote_md5_after": None,
        "md5_verified": False,
        "error_type": "SSH_PROTOCOL_ERROR",
        "error_message": "EOF during negotiation",
    }

    with RunRecorder(log_path, console=console, now=fixed_now) as recorder:
        recorder.record_result("SFTP UPLOAD", result)

    console_text = console.getvalue()
    assert "=== SFTP UPLOAD [FAILED] ===" in console_text
    assert "  source: D:\\packages\\udk_install.sh" in console_text
    assert "  destination: /root/autoEnv/udk_install.sh" in console_text
    assert "  result: status=protocol_error duration_ms=48" in console_text
    assert "  error: SSH_PROTOCOL_ERROR: EOF during negotiation" in console_text
    assert console_text.startswith("\n[") and console_text.endswith("\n\n")

    log_line = log_path.read_text(encoding="utf-8").strip()
    assert "SFTP UPLOAD {" in log_line
    assert "\n" not in log_line
