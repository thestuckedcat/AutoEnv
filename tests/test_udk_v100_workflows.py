"""Run the shipped UDK workflows against real, isolated loopback protocols.

Only the device environment and HDFS endpoint are substituted. Production
CLI/session, SSH/SCP/SFTP/Telnet clients, archive/file handlers and sleeps run
unchanged. The simulated device records commands without invoking a host shell.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import tarfile
import time
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from autoenv.cli import main as cli_main
from autoenv.package_manager import HDFSFileEntry, PackageManager
from autoenv.remote_catalog import save_connection, validate_connection
from autoenv.script_files import inspect_script
from local_remote.ssh import CommandReply, LocalSSHServer
from local_remote.telnet import LocalTelnetServer, TelnetReply


PROJECT = Path(__file__).resolve().parents[1]
TOPOLOGY = "SingleUnions_S3"
BOOT_COMMAND = "cat /proc/sys/kernel/random/boot_id"
START_COMMAND = "cd /root/autoenv && sh start_env.sh"
PROCESS_COMMAND = "ps -ef | grep mami | grep devId"
FLASH_COMMAND = "cd /data && sh unions.sh"
BEFORE_BOOT = "11111111-1111-4111-8111-111111111111"
AFTER_BOOT = "22222222-2222-4222-8222-222222222222"
PACKAGE_FILENAMES = {
    "ube_driver": "UBEngine-mgmt-driver-v100-rtos1.aarch64.run",
    "ube_testcase": "UBEngine-testcase-v100-rtos1.aarch64.tar.gz",
    "udk_drv_v100": "udk_drv_v100-local.tar.gz",
    "firmware": "firmware-local.tar.gz",
}
IMAGE_BYTES = bytes(range(256)) * 17 + b"\x00\r\n\xffOnChipAsic\x00"


def _archive(files: dict[str, bytes], kind: str = "tar.gz") -> bytes:
    output = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
    else:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


class UDKDevice(LocalSSHServer):
    """A rebootable device shell backed only by the test's sandbox directory."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.reboot_changes_id = True
        self.old_reads_after_reboot = 0
        self.boot_id_override: bytes | None = None
        self.reboot_exit: int | None = None
        self.reboot_count = 0
        self.start_exit = 0
        self.process_output = b"root 123 1 0 mami --devId 4\n"
        self.events: list[tuple[str, float]] = []
        self.boot_reads: list[tuple[str, float]] = []

    def _metadata_reply(self, command: str) -> CommandReply:
        self.events.append((command, time.monotonic()))
        if command == BOOT_COMMAND:
            value = AFTER_BOOT if self.reboot_count and self.reboot_changes_id else BEFORE_BOOT
            if self.reboot_count and self.old_reads_after_reboot:
                self.old_reads_after_reboot -= 1
                value = BEFORE_BOOT
            self.boot_reads.append((value, time.monotonic()))
            return CommandReply(stdout=(self.boot_id_override or (value + "\n").encode(),))
        if command == "reboot":
            self.reboot_count += 1
            if self.reboot_exit is not None:
                return CommandReply(stderr=(b"reboot rejected\n",), exit_code=self.reboot_exit)
            return CommandReply(disconnect=True)
        if command == "rm -rf /root/autoenv":
            target = self.path("/root/autoenv")
            target.relative_to(self.root)  # Verify the absolute deletion boundary.
            if target.exists():
                shutil.rmtree(target)
            return CommandReply()
        if command == START_COMMAND:
            return CommandReply(stdout=(b"TODO: UDK V100 start_env.sh is a placeholder\n",),
                                stderr=(b"start failed\n",) if self.start_exit else (),
                                exit_code=self.start_exit)
        if command == PROCESS_COMMAND:
            return CommandReply(stdout=(self.process_output,))
        return super()._metadata_reply(command)

    def _after_upload(self, protocol: str, remote_path: str) -> None:
        super()._after_upload(protocol, remote_path)
        self.events.append(("upload " + remote_path, time.monotonic()))


@dataclass
class Environment:
    root: Path
    dut: UDKDevice
    bmc: LocalSSHServer
    console: LocalTelnetServer
    hdfs: object
    payloads: dict[str, bytes]


@pytest.fixture
def environment_factory(tmp_path, monkeypatch, webhdfs_factory):
    """Generate isolated declarations; redirect only HDFS's endpoint factory."""
    stack = ExitStack()

    def create(*, protocol="sftp", shell_mode="posix", firmware_kind="tar.gz", firmware_files=None):
        root = tmp_path / "project"
        root.mkdir()
        scripts = root / "scripts"
        scripts.mkdir()
        for script in ("UDK_V100.py", "UDK_V100_Firmware.py"):
            shutil.copyfile(PROJECT / "scripts" / script, scripts / script)
        specifications = PackageManager._load_specs(PROJECT / "config.json")
        files = {"build/subdir/OnChipAsic.bin": IMAGE_BYTES} if firmware_files is None else firmware_files
        payloads = {
            "ube_driver": b"#!/bin/sh\nprintf 'driver package\n'\n" + bytes(range(256)) * 20,
            "ube_testcase": _archive({"tests/input.bin": bytes(range(256)) * 12}),
            "udk_drv_v100": _archive({"driver/udk.bin": bytes(reversed(range(256))) * 13}),
            "firmware": _archive(files, firmware_kind),
        }
        names = dict(PACKAGE_FILENAMES)
        if firmware_kind == "zip":
            names["firmware"] = "firmware-local.zip"
        entries, downloads, config = {}, {}, []
        for name, payload in payloads.items():
            directory = "/build/" + name
            filename = names[name]
            assert re.fullmatch(specifications[name].image_name, filename)
            entries[directory] = [HDFSFileEntry(filename, False, len(payload), datetime(2026, 10, 11))]
            downloads[directory + "/" + filename] = payload
            config.append({"name": name, "link": directory, "base_link": "",
                           "image_name": specifications[name].image_name})
        (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        hdfs = webhdfs_factory(entries, downloads)
        monkeypatch.setattr("autoenv.package_manager.HDFSClient", lambda: hdfs.client)
        dut = stack.enter_context(UDKDevice(tmp_path / "1260"))
        bmc = stack.enter_context(LocalSSHServer(tmp_path / "bmc"))
        console = stack.enter_context(LocalTelnetServer(
            shell_mode=shell_mode, passive=True, login_required=True,
            replies={"status": TelnetReply(output=b"READY\r\n")},
        ))
        for name, peer in (("1260网口", dut), ("BMC网口", bmc)):
            save_connection(root, TOPOLOGY, {
                "name": name, "type": "ssh", "host": peer.host, "port": peer.port,
                "username": peer.username, "password": peer.password,
                "connect_timeout": 2, "upload_type": protocol,
            })
        save_connection(root, TOPOLOGY, {
            "name": "BMC串口", "type": "telnet", "host": console.host,
            "port": console.port, "timeout": 1, "shell_mode": shell_mode,
            "username": console.username, "password": console.password,
            "probe_command": "status", "probe_keyword": "READY",
        })
        return Environment(root, dut, bmc, console, hdfs, payloads)

    yield create
    stack.close()


def _run(environment: Environment, script: str, capsys, **arguments):
    descriptor = inspect_script(PROJECT / "scripts" / (script + ".py"))
    command = ["run", descriptor["path"], "--non-interactive", "--topology", TOPOLOGY]
    for slot in descriptor["slots"]:
        command.extend(["--bind", slot["role"] + "=" + slot["allowed_envs"][0][1]])
    for name, value in arguments.items():
        command.extend(["--arg", f"{name}={value}"])
    capsys.readouterr()
    exit_code = cli_main(command, root_dir=environment.root)
    captured = capsys.readouterr()
    assert not captured.err
    lines = captured.out.strip().splitlines()
    assert len(lines) == 1, captured.out
    log = Path(lines[0])
    assert log.is_absolute() and log.is_file()
    result = json.loads((log.parent / "result.json").read_text("utf-8"))
    params = json.loads((log.parent / "params.json").read_text("utf-8"))
    assert result["success"] is (exit_code == 0)
    assert params["selected_topology"] == TOPOLOGY
    return exit_code, result, params, log


def test_workflow_metadata_uses_shared_topology_and_real_config_rules():
    driver = inspect_script(PROJECT / "scripts" / "UDK_V100.py")
    firmware = inspect_script(PROJECT / "scripts" / "UDK_V100_Firmware.py")
    assert driver["packages"] == ["ube_driver", "ube_testcase", "udk_drv_v100"]
    assert firmware["packages"] == ["firmware"]
    assert [(slot["role"], slot["required_type"], slot["allowed_envs"]) for slot in driver["slots"]] == [
        ("dut", "ssh", [(TOPOLOGY, "1260网口")]),
        ("bmc", "ssh", [(TOPOLOGY, "BMC网口")]),
        ("console", "telnet", [(TOPOLOGY, "BMC串口")]),
    ]
    assert firmware["slots"][0]["allowed_envs"] == driver["slots"][1]["allowed_envs"]
    assert firmware["slots"][1]["allowed_envs"] == driver["slots"][2]["allowed_envs"]
    specs = PackageManager._load_specs(PROJECT / "config.json")
    for name, filename in PACKAGE_FILENAMES.items():
        assert re.fullmatch(specs[name].image_name, filename)
    assert specs["firmware"].target_file == ["OnChipAsic.bin"]
    assert {item["name"]: item["default"] for item in driver["arguments"]} == {
        "reboot_timeout": 180.0, "command_timeout": 300.0,
    }
    assert {item["name"]: item["default"] for item in firmware["arguments"]} == {"command_timeout": 600.0}
    template = json.loads((PROJECT / "docs" / "examples" / "SingleUnions_S3.connections.json").read_text("utf-8"))
    connections = {item["name"]: validate_connection(item) for item in template}
    assert {name: item["type"] for name, item in connections.items()} == {
        "1260网口": "ssh", "BMC网口": "ssh", "BMC串口": "telnet",
    }
    assert all(item["enabled"] is False for item in connections.values())
    assert connections["BMC串口"]["shell_mode"] == "posix"


def test_cli_discovers_and_describes_both_workflows_without_device_connections(environment_factory, capsys):
    env = environment_factory()
    assert cli_main(["list", "--json"], root_dir=env.root) == 0
    descriptions = json.loads(capsys.readouterr().out)
    assert {item["script"] for item in descriptions} == {"UDK_V100", "UDK_V100_Firmware"}
    for script in ("UDK_V100", "UDK_V100_Firmware"):
        assert cli_main(["describe", script, "--json"], root_dir=env.root) == 0
        description = json.loads(capsys.readouterr().out)
        assert description["topologies"] == [TOPOLOGY]
        assert not description.get("error")
    assert not env.dut.auth_attempts and not env.bmc.auth_attempts
    assert not env.console.login_attempts and not env.hdfs.open_calls


@pytest.mark.parametrize("outcome", ["success", "empty_processes", "start_failure"])
def test_driver_downloads_reboots_waits_and_transfers_actual_bytes(environment_factory, capsys, outcome):
    env = environment_factory()
    env.dut.seed("/root/autoenv/obsolete.bin", b"old remote content")
    if outcome == "success":
        # A healthy SSH connection alone must not count as a completed reboot.
        env.dut.old_reads_after_reboot = 1
    elif outcome == "empty_processes":
        env.dut.process_output = b" \n\t\n"
    elif outcome == "start_failure":
        env.dut.start_exit = 7
    exit_code, result, params, log = _run(env, "UDK_V100", capsys, command_timeout=2)
    assert exit_code == (0 if outcome == "success" else 1)
    assert params["bindings"] == {"dut": "1260网口", "bmc": "BMC网口", "console": "BMC串口"}
    assert env.dut.reboot_count == 1
    assert env.dut.boot_reads[0][0] == BEFORE_BOOT
    observed_change = next(at for value, at in env.dut.boot_reads if value == AFTER_BOOT)
    if outcome == "success":
        assert [value for value, _at in env.dut.boot_reads] == [BEFORE_BOOT, BEFORE_BOOT, AFTER_BOOT]
        assert observed_change - env.dut.boot_reads[1][1] >= 2
    cleanup_time = next(at for command, at in env.dut.events if command == "rm -rf /root/autoenv")
    assert cleanup_time - observed_change >= 10, "the real post-reboot sleep was shortened"
    assert len(env.dut.auth_attempts) >= 2
    assert env.bmc.auth_attempts and env.console.login_attempts
    assert not env.dut.path("/root/autoenv/obsolete.bin").exists()
    expected_paths = []
    for name in ("ube_driver", "ube_testcase", "udk_drv_v100"):
        filename = PACKAGE_FILENAMES[name]
        expected_paths.append("/root/autoenv/" + filename)
        assert env.dut.path(expected_paths[-1]).read_bytes() == env.payloads[name]
        assert (log.parent / "runtime" / "packages" / name / filename).read_bytes() == env.payloads[name]
    start_script = env.dut.path("/root/autoenv/start_env.sh").read_bytes()
    assert start_script.startswith(b"#!/bin/sh\n") and b"TODO" in start_script
    assert start_script == (log.parent / "runtime" / "start_env.sh").read_bytes()
    uploads = [item for item in env.dut.transfers if item[1] == "upload"]
    assert uploads == [("sftp", "upload", path) for path in [*expected_paths, "/root/autoenv/start_env.sh"]]
    business_events = [command for command, _ in env.dut.events if command in {
        BOOT_COMMAND, "reboot", "rm -rf /root/autoenv", START_COMMAND, PROCESS_COMMAND,
    } or command.startswith("upload ")]
    prefix = [BOOT_COMMAND, "reboot", *([BOOT_COMMAND] * (len(env.dut.boot_reads) - 1)), "rm -rf /root/autoenv"]
    assert business_events[:len(prefix)] == prefix
    after_reboot = business_events[len(prefix):]
    assert after_reboot[:4] == ["upload " + path for path in [*expected_paths, "/root/autoenv/start_env.sh"]]
    assert after_reboot[4:] == ([START_COMMAND] if outcome == "start_failure" else [START_COMMAND, PROCESS_COMMAND])
    assert env.hdfs.open_calls == ["/build/" + name + "/" + PACKAGE_FILENAMES[name]
                                   for name in ("ube_driver", "ube_testcase", "udk_drv_v100")]
    assert env.hdfs.list_calls == ["/build/" + name for name in ("ube_driver", "ube_testcase", "udk_drv_v100")]
    assert not env.bmc.transfers and not env.dut.errors and not env.bmc.errors and not env.console.errors
    if outcome == "empty_processes":
        assert result["final_operation"]["result"]["error_type"] == "BUSINESS_RULE_FAILED"
    if outcome == "start_failure":
        assert result["final_operation"]["result"]["exit_code"] == 7


@pytest.mark.parametrize("problem", ["same_boot_id", "invalid_boot_id", "reboot_rejected"])
def test_driver_does_not_deploy_until_reboot_is_proven(environment_factory, capsys, problem):
    env = environment_factory()
    if problem == "same_boot_id":
        env.dut.reboot_changes_id = False
    elif problem == "invalid_boot_id":
        env.dut.boot_id_override = b"not-a-boot-id\n"
    else:
        env.dut.reboot_exit = 7
    exit_code, result, _params, _log = _run(env, "UDK_V100", capsys,
                                          reboot_timeout=0.15, command_timeout=1)
    assert exit_code == 1 and not env.dut.transfers
    assert "rm -rf /root/autoenv" not in env.dut.commands
    assert START_COMMAND not in env.dut.commands and PROCESS_COMMAND not in env.dut.commands
    assert env.dut.reboot_count == (0 if problem == "invalid_boot_id" else 1)
    if problem == "same_boot_id":
        assert result["error_type"] == "TimeoutError"
    elif problem == "invalid_boot_id":
        assert result["error_type"] == "ValueError"
    else:
        assert result["final_operation"]["result"]["exit_code"] == 7


def test_driver_hdfs_failure_stops_before_reboot_or_remote_deployment(environment_factory, capsys):
    env = environment_factory()
    failed_path = "/build/ube_testcase/" + PACKAGE_FILENAMES["ube_testcase"]
    env.hdfs.downloads[failed_path] = TimeoutError("local HDFS package unavailable")
    exit_code, result, _params, _log = _run(env, "UDK_V100", capsys)
    assert exit_code == 1 and not env.dut.reboot_count and not env.dut.transfers
    final = result["final_operation"]["result"]
    assert final["config_name"] == "ube_testcase" and final["success"] is False
    assert env.hdfs.open_calls == ["/build/ube_driver/" + PACKAGE_FILENAMES["ube_driver"], failed_path]
    assert BOOT_COMMAND not in env.dut.commands and "rm -rf /root/autoenv" not in env.dut.commands


@pytest.mark.parametrize("protocol,kind", [("sftp", "tar.gz"), ("scp", "zip")])
def test_firmware_unzips_uploads_real_bytes_and_checks_split_success_after_exit(
    environment_factory, capsys, protocol, kind,
):
    env = environment_factory(protocol=protocol, firmware_kind=kind)
    env.console.replies[FLASH_COMMAND] = TelnetReply(output=(b"begin\r\nSUC", b"CESS\r\n"))
    exit_code, result, params, log = _run(env, "UDK_V100_Firmware", capsys, command_timeout=1)
    assert exit_code == 0 and result["status"] == "success"
    assert params["bindings"] == {"bmc": "BMC网口", "console": "BMC串口"}
    assert env.bmc.path("/tmp/OnChipAsic.bin").read_bytes() == IMAGE_BYTES
    assert (log.parent / "runtime" / "firmware" / "build" / "subdir" / "OnChipAsic.bin").read_bytes() == IMAGE_BYTES
    assert [item for item in env.bmc.transfers if item[1] == "upload"] == [(protocol, "upload", "/tmp/OnChipAsic.bin")]
    assert env.console.commands.count(FLASH_COMMAND) == 1
    assert env.console.login_attempts == [(env.console.username, env.console.password)]
    assert not env.dut.auth_attempts and not env.dut.transfers
    final = result["final_operation"]["result"]
    assert final["protocol"] == "telnet" and final["exit_code"] == 0
    assert final["stdout"] == "begin\nSUCCESS"
    assert "__AUTOENV_RC_" not in final["stdout"]
    assert env.hdfs.open_calls == ["/build/firmware/firmware-local." + ("zip" if kind == "zip" else "tar.gz")]
    assert not env.bmc.errors and not env.console.errors


@pytest.mark.parametrize("reply,expected_status,expected_error", [
    (TelnetReply(output=b"finished\r\n"), "command_failed", "BUSINESS_RULE_FAILED"),
    (TelnetReply(output=b"unsuccessful\r\n"), "command_failed", "BUSINESS_RULE_FAILED"),
    (TelnetReply(output=b"success\r\n", exit_code=7), "command_failed", "NON_ZERO_EXIT_CODE"),
    (TelnetReply(output=(b"success\r\nroot@loopback:~# ", b"\r\nstill running\r\n"), exit_code=7),
     "command_failed", "NON_ZERO_EXIT_CODE"),
    (TelnetReply(output=b"success\r\n", complete=False), "timeout", None),
    (TelnetReply(output=b"success\r\n", disconnect=True), "disconnected", None),
    (TelnetReply(output=b"success\r\n", omit_marker=True), "protocol_error", None),
])
def test_firmware_requires_command_completion_and_zero_exit_before_success_check(
    environment_factory, capsys, reply, expected_status, expected_error,
):
    env = environment_factory()
    env.console.replies[FLASH_COMMAND] = reply
    exit_code, result, _params, _log = _run(env, "UDK_V100_Firmware", capsys, command_timeout=0.2)
    assert exit_code == 1 and result["status"] == expected_status
    final = result["final_operation"]["result"]
    assert final["command"] == FLASH_COMMAND
    if expected_error:
        assert final["error_type"] == expected_error
    if reply.exit_code == 7:
        assert final["exit_code"] == 7
    if not reply.complete:
        assert final["exit_code"] is None and "success" in final["stdout"]
    assert env.console.commands.count(FLASH_COMMAND) == 1
    assert env.bmc.path("/tmp/OnChipAsic.bin").read_bytes() == IMAGE_BYTES


def test_firmware_prompt_only_success_does_not_claim_known_exit_status(environment_factory, capsys):
    env = environment_factory(shell_mode="prompt_only")
    env.console.replies[FLASH_COMMAND] = TelnetReply(output=b"success\r\n")
    exit_code, result, _params, _log = _run(env, "UDK_V100_Firmware", capsys, command_timeout=0.2)
    assert exit_code == 1 and result["status"] == "result_unknown"
    assert result["final_operation"]["result"]["exit_code"] is None
    assert env.console.commands.count(FLASH_COMMAND) == 1


def test_firmware_missing_exact_image_stops_before_upload_or_flash(environment_factory, capsys):
    env = environment_factory(firmware_files={"nested/OnChipAsic.bin.backup": IMAGE_BYTES})
    exit_code, result, _params, _log = _run(env, "UDK_V100_Firmware", capsys, command_timeout=0.2)
    assert exit_code == 1 and result["error_type"] == "FileNotFoundError"
    assert "OnChipAsic.bin" in result["error_message"]
    assert not env.bmc.transfers and FLASH_COMMAND not in env.console.commands


@pytest.mark.parametrize("script", ["UDK_V100", "UDK_V100_Firmware"])
def test_nonpositive_timeout_is_rejected_before_packages_and_business_commands(
    environment_factory, capsys, script,
):
    env = environment_factory()
    exit_code, result, _params, _log = _run(env, script, capsys, command_timeout=0)
    assert exit_code == 1 and result["error_type"] == "ValueError"
    assert not env.hdfs.open_calls and not env.dut.transfers and not env.bmc.transfers
    assert BOOT_COMMAND not in env.dut.commands and FLASH_COMMAND not in env.console.commands
