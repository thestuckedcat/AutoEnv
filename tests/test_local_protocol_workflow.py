"""Exercise a complete registered script through unmodified protocol clients."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from autoenv import (
    FTPDefaults, SSHDefaults, TelnetDefaults, TimestampPattern,
    extra_file, package, register_func, register_script,
)
from autoenv.package_manager import HDFSFileEntry
from autoenv.registry import clear_registry_for_tests, run_script
from local_remote.ftp import LocalFTPServer
from local_remote.ssh import LocalSSHServer
from local_remote.telnet import LocalTelnetServer, TelnetReply


@pytest.fixture(autouse=True)
def isolated_registry():
    clear_registry_for_tests()
    yield
    clear_registry_for_tests()


def archive_bytes(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.parametrize("upload_protocol", ["scp", "sftp"])
def test_registered_run_and_rerun_transfer_files_and_deliver_commands(
    tmp_path: Path, webhdfs_factory, upload_protocol: str,
):
    binary = bytes(range(256)) * 400 + b"\x00\r\n\xff"
    bundle = archive_bytes({"payload/image.bin": binary})
    remote = webhdfs_factory(
        {"/build": [HDFSFileEntry("firmware-1.zip", False, len(bundle), datetime(2026, 10, 9))]},
        {"/build/firmware-1.zip": bundle},
    )
    root = tmp_path / "project"
    root.mkdir()
    (root / "config.json").write_text(json.dumps([{
        "name": "firmware", "link": "/build", "image_name": r"^firmware-.*\.zip$",
    }]), encoding="utf-8")

    with LocalSSHServer(tmp_path / "ssh-remote") as ssh, \
            LocalFTPServer(tmp_path / "ftp-remote") as ftp, \
            LocalTelnetServer(replies={"reboot": TelnetReply(
                output=(b"Press Ctrl", b"+B"), expected_input=b"\x02",
            )}) as telnet:
        ssh.seed("/capture/device.bin", binary)
        ssh.seed("/logs/device.log", b"08:00:00 [READY] local peer\n", mtime=100)

        @register_script("local_protocol_workflow", description="本地协议完整流程")
        def provision(ctx):
            firmware = package("firmware")
            image = extra_file("image.bin")
            installer = extra_file("install.sh")
            host = ctx.register_ssh_host("dut", resource_label="1260网口", defaults=SSHDefaults(
                host=ssh.host, port=ssh.port, username=ssh.username, password=ssh.password,
            ))
            console = ctx.register_telnet("console", resource_label="1260串口",
                defaults=TelnetDefaults(host=telnet.host, port=telnet.port, shell_mode="posix"),
                uploaded_files_from="dut")
            ftp_host = ctx.register_ftp_host("archive", resource_label="1712网口", defaults=FTPDefaults(
                host=ftp.host, port=ftp.port, username=ftp.username, password=ftp.password,
            ))

            result = ctx.download_package(firmware)
            assert result.success, result
            result = ctx.extract_file_from(firmware, target_file="image.bin")
            assert result.success, result
            result = getattr(host, upload_protocol + "_upload")(image, "/deploy")
            assert result.success, result
            ctx.generate_sh_file("install.sh", "#!/bin/sh\ninstall S{image.bin}\n")
            other_protocol = "sftp" if upload_protocol == "scp" else "scp"
            result = getattr(host, other_protocol + "_upload")(installer, "/deploy")
            assert result.success, result
            result = host.execute("sh /deploy/S{install.sh} --image S{image.bin}")
            assert result.success, result
            result = console.execute("boot /deploy/S{image.bin}", timeout=2)
            assert result.success, result
            result = console.execute_on_output("reboot", keyword="Press Ctrl+B", send_data=b"\x02", timeout=2)
            assert result.success, result

            result = getattr(host, other_protocol + "_download")("/capture", remote_file="device.bin")
            assert result.success, result
            result = ftp_host.upload(result, "/archive")
            assert result.success, result

            logs = ctx.create_log_collection("本地日志")
            assert logs.download(host, remote_dir="/logs", glob="*.log").success
            assert logs.extract_all().success
            group = logs.group(glob="*.log", timestamp=TimestampPattern(
                r"(?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"))
            assert group.match_line(r"\[READY\]", "ready.log").success
            assert logs.finalize().success

            @register_func("check_status", description="验证相同连接仍可用")
            def status(_ctx):
                return host.execute("status")

            return result

        runs = []
        for mode in ("run", "rerun"):
            menu = iter(["1", "0"])
            result = run_script("local_protocol_workflow", root_dir=root, mode=mode,
                non_interactive=True, input_func=lambda _: next(menu),
                console=io.StringIO(), hdfs_client=remote.client)
            assert result.success, result
            runs.append(result)
            packages = Path(result.package_dir)
            assert (packages / "image.bin").read_bytes() == binary
            assert hashlib.sha256((packages / "device.bin").read_bytes()).digest() == hashlib.sha256(binary).digest()
            summary = json.loads((Path(result.run_dir) / "result.json").read_text("utf-8"))
            assert len(summary["func_runs"]) == 1
            assert summary["func_runs"][0]["success"] is True

        assert runs[0].run_dir != runs[1].run_dir
        assert remote.open_calls == ["/build/firmware-1.zip"] * 2
        assert ssh.path("/deploy/image.bin").read_bytes() == binary
        assert ssh.path("/deploy/install.sh").read_bytes() == b"#!/bin/sh\ninstall image.bin\n"
        assert ftp.read_bytes("/archive/device.bin") == binary
        assert ssh.commands.count("sh /deploy/install.sh --image image.bin") == 2
        assert ssh.commands.count("status") == 2
        assert telnet.commands.count("boot /deploy/image.bin") == 2
        assert telnet.wait_for_raw(b"\x02", count=2)
        assert telnet.raw_inputs.count(b"\x02") == 2
        assert ("scp", "download", "/logs/device.log") in ssh.transfers
        assert {item[0] for item in ssh.transfers} == {"scp", "sftp"}
        assert len(ftp.transfers) == 2
        assert not ssh.errors and not telnet.errors and not ftp.errors
