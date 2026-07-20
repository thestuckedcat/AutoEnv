from __future__ import annotations

import io
import json
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoenv.package_manager import HDFSFileEntry
from autoenv.registry import (
    clear_registry_for_tests,
    register_func,
    register_script,
    run_script,
)
from autoenv.results import (
    CommandPhase,
    CommandProtocol,
    CommandResult,
    CommandStatus,
)
from autoenv.runtime import RunContext, RunMode
from autoenv.selectors import match, package
from autoenv.ssh_host import SSHDefaults
from autoenv.telnet_client import TelnetDefaults


@pytest.fixture(autouse=True)
def isolated_registry():
    clear_registry_for_tests()
    yield
    clear_registry_for_tests()


def _project_root(tmp_path: Path, *, with_package: bool = False) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    config: list[dict[str, object]] = []
    if with_package:
        config.append(
            {
                "name": "firmware",
                "link": "/remote/firmware",
                "base_link": "",
                "image_name": r"^firmware-\d+\.bin$",
            }
        )
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return root


class FakeHDFS:
    payload = b"offline-package"

    def __init__(self) -> None:
        self.listed: list[str] = []
        self.downloaded: list[tuple[str, str]] = []

    def list_directory(self, path: str):
        self.listed.append(path)
        return [
            HDFSFileEntry(
                name="firmware-1.bin",
                is_directory=False,
                length=len(self.payload),
                modification_time=datetime(2026, 7, 15, tzinfo=timezone.utc),
            )
        ]

    def download_file(self, remote_path: str, local_path: str) -> None:
        self.downloaded.append((remote_path, local_path))
        Path(local_path).write_bytes(self.payload)


def _command_failure() -> CommandResult:
    now = datetime(2026, 7, 15, tzinfo=timezone.utc)
    return CommandResult(
        run_id="operation-run",
        operation_id="0001",
        protocol=CommandProtocol.SSH,
        target_name="dut",
        command="false",
        status=CommandStatus.COMMAND_FAILED,
        phase=CommandPhase.COMPLETE,
        exit_code=1,
        stdout="",
        stderr="failed",
        raw_output="failed",
        started_at=now,
        finished_at=now,
        duration_ms=0,
        error_type="NON_ZERO_EXIT_CODE",
        error_message="exit 1",
    )


def test_run_defaults_last_run_and_rerun_are_offline_and_use_new_directories(tmp_path):
    root = _project_root(tmp_path, with_package=True)
    contexts: list[RunContext] = []

    @register_script("provision")
    def provision(ctx: RunContext):
        contexts.append(ctx)
        ctx.register_ssh_host(
            "dut",
            defaults=SSHDefaults(
                host="192.0.2.10",
                port=2222,
                username="root",
                password="default-password",
                connect_timeout=12,
            ),
        )
        ctx.register_telnet(
            "console",
            defaults=TelnetDefaults(
                host="192.0.2.20", port=2323, timeout=15, shell_mode="posix"
            ),
        )
        return ctx.download_package(package("firmware"))

    prompts: list[str] = []
    password_prompts: list[str] = []
    first_hdfs = FakeHDFS()
    first = run_script(
        "provision",
        root_dir=root,
        input_func=lambda prompt: prompts.append(prompt) or "",
        password_input=lambda prompt: password_prompts.append(prompt) or "",
        console=io.StringIO(),
        hdfs_client=first_hdfs,
    )
    assert first.success is True
    assert len(prompts) == 9
    assert len(password_prompts) == 1
    assert first_hdfs.listed == ["/remote/firmware"]
    assert (Path(first.package_dir) / "firmware-1.bin").read_bytes() == FakeHDFS.payload

    first_params = json.loads((Path(first.run_dir) / "params.json").read_text("utf-8"))
    assert first_params["ssh_hosts"]["dut"] == {
        "connect_timeout": 12.0,
        "host": "192.0.2.10",
        "password": "default-password",
        "port": 2222,
        "username": "root",
    }
    assert first_params["telnet_connections"]["console"] == {
        "host": "192.0.2.20",
        "port": 2323,
        "shell_mode": "posix",
        "timeout": 15.0,
    }
    assert first_params["packages"]["firmware"] == {
        "path_mode": "link",
        "path_override": None,
    }
    last_run_path = root / "state" / "last_runs" / "provision.json"
    assert json.loads(last_run_path.read_text("utf-8")) == first_params

    def unexpected_prompt(prompt: str) -> str:
        pytest.fail(f"rerun unexpectedly prompted: {prompt}")

    second = run_script(
        "provision",
        mode=RunMode.RERUN,
        root_dir=root,
        input_func=unexpected_prompt,
        password_input=unexpected_prompt,
        console=io.StringIO(),
        hdfs_client=FakeHDFS(),
    )
    assert second.success is True
    assert second.run_dir != first.run_dir
    assert second.package_dir != first.package_dir
    assert Path(first.run_dir).is_dir() and Path(second.run_dir).is_dir()
    second_params = json.loads((Path(second.run_dir) / "params.json").read_text("utf-8"))
    assert second_params == first_params
    assert json.loads(last_run_path.read_text("utf-8")) == second_params
    assert [ctx.mode for ctx in contexts] == [RunMode.RUN, RunMode.RERUN]


def test_missing_last_run_does_not_execute_body(tmp_path):
    root = _project_root(tmp_path)
    called = False

    @register_script("fresh")
    def fresh(_ctx: RunContext):
        nonlocal called
        called = True

    result = run_script("fresh", mode="rerun", root_dir=root, console=io.StringIO())
    assert result.success is False
    assert result.status == "last_run_not_found"
    assert result.error_type == "LAST_RUN_NOT_FOUND"
    assert result.run_dir == ""
    assert called is False
    assert not (root / "state" / "last_runs" / "fresh.json").exists()


def test_match_lists_candidates_copies_selection_and_reuses_it(tmp_path):
    root = _project_root(tmp_path)
    search_path = tmp_path / "build-output"
    search_path.mkdir()
    for name, content in (("firmware-A.tgz", b"A"), ("firmware-b.tgz", b"B")):
        with tarfile.open(search_path / name, "w:gz") as archive:
            info = tarfile.TarInfo("marker.txt")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    answers = iter(("invalid", "2"))
    prompts: list[str] = []
    console = io.StringIO()
    context = RunContext(
        root_dir=root,
        script_name="choose_firmware",
        mode=RunMode.RUN,
        input_func=lambda prompt: prompts.append(prompt) or next(answers),
        console=console,
    )
    selector = match(r"^firmware-.*\.tgz$", search_path=search_path)

    try:
        extract_result = context.extract_file_from(selector, target_file="marker.txt")
        resolved = context.resolve_local_file(selector)
    finally:
        context.close()

    assert extract_result.success is True
    assert resolved.path == (context.package_dir / "firmware-b.tgz").resolve()
    assert Path(extract_result.source_file or "") == resolved.path
    assert (context.package_dir / "marker.txt").read_bytes() == b"B"
    assert prompts == [
        "Select matched file [1-2]: ",
        "Select matched file [1-2]: ",
    ]
    console_text = console.getvalue()
    assert "1. firmware-A.tgz" in console_text
    assert "2. firmware-b.tgz" in console_text
    assert "INVALID MATCH SELECTION [RETRY]" in console_text
    assert "file='firmware-b.tgz'" in context.log_path.read_text("utf-8")


def test_rerun_preserves_saved_package_path_mode(tmp_path):
    root = _project_root(tmp_path, with_package=True)
    config = json.loads((root / "config.json").read_text("utf-8"))
    config[0]["base_link"] = "/base"
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    last_path = root / "state" / "last_runs" / "rerun_mode.json"
    last_path.parent.mkdir(parents=True)
    last_path.write_text(
        json.dumps(
            {
                "script_name": "rerun_mode",
                "ssh_hosts": {},
                "telnet_connections": {},
                "packages": {
                    "firmware": {
                        "path_mode": "base_link_newest",
                        "path_override": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeManager:
        def get_spec(self, _name: str):
            return SimpleNamespace(link="/remote/firmware", base_link="/base")

        def download(self, selector, **kwargs):
            captured.update(selector=selector, **kwargs)
            return "downloaded"

    context = RunContext(
        root_dir=root,
        script_name="rerun_mode",
        mode=RunMode.RERUN,
        console=io.StringIO(),
    )
    try:
        context._package_manager = FakeManager()
        assert context.download_package(package("firmware")) == "downloaded"
        assert captured["path_mode"] == "base_link_newest"
        assert captured["path_override"] is None
    finally:
        context.close()
        context.finish_recording()


def test_package_newest_shortcut_overrides_saved_manual_path(tmp_path):
    root = _project_root(tmp_path, with_package=True)
    config = json.loads((root / "config.json").read_text("utf-8"))
    config[0]["base_link"] = "/config/newest-root"
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    last_path = root / "state" / "last_runs" / "newest_shortcut.json"
    last_path.parent.mkdir(parents=True)
    last_path.write_text(
        json.dumps(
            {
                "script_name": "newest_shortcut",
                "ssh_hosts": {},
                "telnet_connections": {},
                "packages": {
                    "firmware": {
                        "path_mode": "override",
                        "path_override": "/saved/manual-build",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeManager:
        def get_spec(self, _name: str):
            return SimpleNamespace(
                link="/config/fixed-build",
                base_link="/config/newest-root",
            )

        def download(self, selector, **kwargs):
            captured.update(selector=selector, **kwargs)
            return "downloaded"

    prompts: list[str] = []
    context = RunContext(
        root_dir=root,
        script_name="newest_shortcut",
        mode=RunMode.RUN,
        input_func=lambda prompt: prompts.append(prompt) or "!NEWEST",
        console=io.StringIO(),
    )
    try:
        context._package_manager = FakeManager()
        assert context.download_package(package("firmware")) == "downloaded"
        assert captured["path_mode"] == "base_link_newest"
        assert captured["path_override"] is None
        assert context.params["packages"]["firmware"] == {
            "path_mode": "base_link_newest",
            "path_override": None,
        }
        assert prompts == [
            "Package firmware remote directory "
            "[default: /saved/manual-build; "
            "!newest: /config/newest-root (automatic newest)]: "
        ]
    finally:
        context.close()
        context.finish_recording()


def test_exception_after_operation_keeps_collected_parameters_as_last_run(tmp_path):
    root = _project_root(tmp_path)

    @register_script("crashes_after_operation")
    def crashes_after_operation(ctx: RunContext):
        ctx.register_telnet("console", defaults=TelnetDefaults(host="192.0.2.2"))
        ctx.recorder.next_operation_id()
        raise RuntimeError("after operation")

    result = run_script(
        "crashes_after_operation",
        root_dir=root,
        input_func=lambda _: "",
        console=io.StringIO(),
    )

    assert result.status == "program_error"
    last = json.loads(
        (root / "state" / "last_runs" / "crashes_after_operation.json").read_text(
            "utf-8"
        )
    )
    assert last["telnet_connections"]["console"]["host"] == "192.0.2.2"


def test_duplicate_registration_is_rejected_for_scripts_and_runtime_objects(tmp_path):
    root = _project_root(tmp_path)

    @register_script("duplicate")
    def first(_ctx: RunContext):
        return None

    with pytest.raises(ValueError, match="script already registered"):

        @register_script("duplicate")
        def second(_ctx: RunContext):
            return None

    context = RunContext(
        root_dir=root,
        script_name="objects",
        mode=RunMode.RUN,
        input_func=lambda _: "",
        password_input=lambda _: "",
        console=io.StringIO(),
    )
    try:
        context.register_ssh_host("dut", defaults=SSHDefaults(host="192.0.2.1"))
        with pytest.raises(ValueError, match="already registered"):
            context.register_ssh_host("dut", defaults=SSHDefaults(host="192.0.2.1"))
        context.register_telnet("console", defaults=TelnetDefaults(host="192.0.2.2"))
        with pytest.raises(ValueError, match="already registered"):
            context.register_telnet("console", defaults=TelnetDefaults(host="192.0.2.2"))
        with pytest.raises(ValueError, match="already registered"):
            context.register_telnet("dut", defaults=TelnetDefaults(host="192.0.2.2"))
    finally:
        context.close()
        context.finish_recording()


def test_run_context_shares_uploaded_files_with_hosts_and_generated_scripts(tmp_path):
    root = _project_root(tmp_path)
    context = RunContext(
        root_dir=root,
        script_name="shared_upload_registry",
        mode=RunMode.RUN,
        input_func=lambda _: "",
        password_input=lambda _: "",
        console=io.StringIO(),
    )
    try:
        host = context.register_ssh_host(
            "dut", defaults=SSHDefaults(host="192.0.2.1")
        )
        host.uploaded_files.record(
            "api", "/release/api-2.4.tgz", target_name=host.name
        )

        generated = context.generate_sh_file(
            "install.sh", "#!/bin/sh\ntar -xf S{api}\n"
        )

        assert generated == context.package_dir / "install.sh"
        assert generated.read_text("utf-8") == "#!/bin/sh\ntar -xf api-2.4.tgz\n"
    finally:
        context.close()
        context.finish_recording()


def test_telnet_upload_source_must_be_a_registered_ssh_host(tmp_path):
    root = _project_root(tmp_path)
    context = RunContext(
        root_dir=root,
        script_name="telnet_upload_source",
        mode=RunMode.RUN,
        input_func=lambda _: "",
        password_input=lambda _: "",
        console=io.StringIO(),
    )
    try:
        with pytest.raises(ValueError, match="SSH host is not registered"):
            context.register_telnet(
                "console",
                defaults=TelnetDefaults(host="192.0.2.2"),
                uploaded_files_from="dut",
            )

        context.register_ssh_host("dut", defaults=SSHDefaults(host="192.0.2.1"))
        console = context.register_telnet(
            "console",
            defaults=TelnetDefaults(host="192.0.2.2"),
            uploaded_files_from="dut",
        )
        assert console.uploaded_files_from == "dut"
    finally:
        context.close()
        context.finish_recording()


def test_registered_body_return_rules_are_reflected_in_script_results(tmp_path):
    root = _project_root(tmp_path)

    @register_script("returns_none")
    def returns_none(_ctx: RunContext):
        return None

    @register_script("returns_failure")
    def returns_failure(_ctx: RunContext):
        return _command_failure()

    @register_script("returns_invalid")
    def returns_invalid(_ctx: RunContext):
        return {"not": "a result"}

    successful = run_script("returns_none", root_dir=root, console=io.StringIO())
    failed = run_script("returns_failure", root_dir=root, console=io.StringIO())
    invalid = run_script("returns_invalid", root_dir=root, console=io.StringIO())

    assert (successful.success, successful.status, successful.final_operation) == (
        True,
        "success",
        None,
    )
    assert failed.success is False
    assert failed.status == "command_failed"
    assert failed.error_type == "NON_ZERO_EXIT_CODE"
    assert isinstance(failed.final_operation, CommandResult)
    assert invalid.success is False
    assert invalid.status == "program_error"
    assert invalid.error_type == "TypeError"
    assert "must return None or an AutoEnv result object" in invalid.error_message
    assert (root / "state" / "last_runs" / "returns_failure.json").is_file()
    assert (Path(invalid.run_dir) / "result.json").is_file()
    failed_summary = json.loads((Path(failed.run_dir) / "result.json").read_text("utf-8"))
    assert failed_summary["final_operation_id"] is None
    assert "final_operation" not in failed_summary


def test_register_func_requires_an_active_registered_script() -> None:
    with pytest.raises(RuntimeError, match="inside a running registered script"):

        @register_func("status")
        def status(_ctx: RunContext):
            return None


def test_registered_funcs_reuse_context_and_loop_until_exit(tmp_path):
    root = _project_root(tmp_path)
    seen: list[tuple[str, RunContext, object]] = []
    answers = iter(["", "", "", "", "1", "2", "3", "0"])
    console = io.StringIO()

    @register_script("interactive_funcs")
    def interactive_funcs(ctx: RunContext):
        host = ctx.register_ssh_host(
            "dut",
            defaults=SSHDefaults(
                host="192.0.2.1",
                username="root",
                password="secret",
            ),
        )

        @register_func("status", description="Check environment status")
        def status(func_ctx: RunContext):
            seen.append(("status", func_ctx, host))

        @register_func("failing_check", description="Demonstrate a failed check")
        def failing_check(func_ctx: RunContext):
            seen.append(("failing_check", func_ctx, host))
            raise ValueError("check failed")

        @register_func("failed_result", description="Return a failed operation")
        def failed_result(func_ctx: RunContext):
            seen.append(("failed_result", func_ctx, host))
            return _command_failure()

    result = run_script(
        "interactive_funcs",
        root_dir=root,
        input_func=lambda _prompt: next(answers),
        password_input=lambda _prompt: "",
        console=console,
    )

    assert result.success is True
    assert [name for name, _, _ in seen] == [
        "status",
        "failing_check",
        "failed_result",
    ]
    assert len({id(ctx) for _, ctx, _ in seen}) == 1
    assert len({id(host) for _, _, host in seen}) == 1
    summary = json.loads((Path(result.run_dir) / "result.json").read_text("utf-8"))
    assert summary["func_runs"] == [
        {
            "error_message": None,
            "error_type": None,
            "name": "status",
            "status": "success",
            "success": True,
        },
        {
            "error_message": "check failed",
            "error_type": "ValueError",
            "name": "failing_check",
            "status": "program_error",
            "success": False,
        },
        {
            "error_message": "exit 1",
            "error_type": "NON_ZERO_EXIT_CODE",
            "name": "failed_result",
            "status": "command_failed",
            "success": False,
        },
    ]
    output = console.getvalue()
    assert output.count("AVAILABLE FUNCS") == 4
    assert "1. status" in output
    assert "2. failing_check" in output
    assert "3. failed_result" in output
    assert "0. exit" in output


def test_func_menu_reprompts_after_invalid_selections(tmp_path):
    root = _project_root(tmp_path)
    answers = iter(["not-a-number", "9", "0"])
    called = False
    console = io.StringIO()

    @register_script("invalid_func_selection")
    def invalid_func_selection(_ctx: RunContext):
        @register_func("never_called")
        def never_called(_func_ctx: RunContext):
            nonlocal called
            called = True

    result = run_script(
        "invalid_func_selection",
        root_dir=root,
        input_func=lambda _prompt: next(answers),
        console=console,
    )

    assert result.success is True
    assert called is False
    assert "selection_must_be_number" in console.getvalue()
    assert "selection_out_of_range" in console.getvalue()


def test_func_menu_is_not_opened_when_main_flow_fails(tmp_path):
    root = _project_root(tmp_path)

    @register_script("failed_before_func_menu")
    def failed_before_func_menu(_ctx: RunContext):
        @register_func("not_available")
        def not_available(_func_ctx: RunContext):
            pytest.fail("failed main flow unexpectedly ran a func")

        return _command_failure()

    result = run_script(
        "failed_before_func_menu",
        root_dir=root,
        input_func=lambda prompt: pytest.fail(f"unexpected func prompt: {prompt}"),
        console=io.StringIO(),
    )

    assert result.success is False
    assert result.status == "command_failed"


def test_duplicate_func_name_fails_the_main_program(tmp_path):
    root = _project_root(tmp_path)

    @register_script("duplicate_func")
    def duplicate_func(_ctx: RunContext):
        @register_func("status")
        def first(_func_ctx: RunContext):
            return None

        @register_func("status")
        def second(_func_ctx: RunContext):
            return None

    result = run_script("duplicate_func", root_dir=root, console=io.StringIO())

    assert result.success is False
    assert result.status == "program_error"
    assert result.error_type == "ValueError"
    assert "func already registered" in result.error_message


def test_registered_func_requires_one_context_argument(tmp_path):
    root = _project_root(tmp_path)

    @register_script("invalid_func_signature")
    def invalid_func_signature(_ctx: RunContext):
        @register_func("status")
        def status():
            return None

    result = run_script(
        "invalid_func_signature", root_dir=root, console=io.StringIO()
    )

    assert result.success is False
    assert result.status == "program_error"
    assert result.error_type == "TypeError"
    assert "exactly one RunContext" in result.error_message


def test_telnet_only_context_does_not_require_config_json(tmp_path):
    root = _project_root(tmp_path)
    (root / "config.json").unlink()
    context = RunContext(
        root_dir=root,
        script_name="telnet_only",
        mode=RunMode.RUN,
        input_func=lambda _: "",
        console=io.StringIO(),
    )
    try:
        client = context.register_telnet(
            "console", defaults=TelnetDefaults(host="192.0.2.2")
        )
        assert client.info.host == "192.0.2.2"
    finally:
        context.close()
        context.finish_recording()


def test_composite_calls_create_independent_child_runs_and_propagate_rerun(tmp_path):
    root = _project_root(tmp_path)
    seen: list[tuple[str, RunMode, Path]] = []

    @register_script("child")
    def child(ctx: RunContext):
        seen.append(("child", ctx.mode, ctx.run_dir))

    @register_script("parent")
    def parent(ctx: RunContext):
        seen.append(("parent", ctx.mode, ctx.run_dir))
        return child()

    first = run_script("parent", root_dir=root, console=io.StringIO())
    second = run_script(
        "parent", mode=RunMode.RERUN, root_dir=root, console=io.StringIO()
    )

    assert first.success is True and second.success is True
    assert [(name, mode) for name, mode, _ in seen] == [
        ("parent", RunMode.RUN),
        ("child", RunMode.RUN),
        ("parent", RunMode.RERUN),
        ("child", RunMode.RERUN),
    ]
    run_dirs = [run_dir for _, _, run_dir in seen]
    assert len(set(run_dirs)) == 4
    assert all(run_dir.is_dir() for run_dir in run_dirs)
    assert first.final_operation.script_name == "child"
    assert second.final_operation.script_name == "child"
    assert (root / "state" / "last_runs" / "parent.json").is_file()
    assert (root / "state" / "last_runs" / "child.json").is_file()


def test_package_cache_cleanup_removes_oldest_historical_content_only(tmp_path):
    root = _project_root(tmp_path)
    logs = root / "logs"
    oldest = logs / "oldest" / "packages"
    newer = logs / "newer" / "packages"
    oldest.mkdir(parents=True)
    newer.mkdir(parents=True)
    (oldest / "old.bin").write_bytes(b"123456")
    (newer / "new.bin").write_bytes(b"12345")
    os.utime(oldest.parent, (100, 100))
    os.utime(newer.parent, (200, 200))

    context = RunContext(
        root_dir=root,
        script_name="cleanup",
        mode=RunMode.RUN,
        console=io.StringIO(),
        package_cache_limit=5,
    )
    try:
        assert oldest.is_dir()
        assert list(oldest.iterdir()) == []
        assert (newer / "new.bin").read_bytes() == b"12345"
        assert context.package_dir.is_dir()
        assert list(context.package_dir.iterdir()) == []
        assert "PACKAGE CACHE CLEANED" in context.log_path.read_text("utf-8")
    finally:
        context.close()
        context.finish_recording()
