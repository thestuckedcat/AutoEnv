"""Run the public CLI against disk-backed loopback protocol services.

No HDFS service or download is used. Received shell commands are recorded and
answered, never executed on the local host. SCP/SFTP/FTP clients are untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from local_remote.ftp import LocalFTPServer
from local_remote.ssh import CommandReply, LocalSSHServer
from local_remote.telnet import LocalTelnetServer, TelnetReply


WORKFLOW = '''"""Offline protocol workflow; arbitrary remote commands are only recorded."""
import os
import subprocess
import sys
from autoenv import CommandStatus, create_file, get_file, get_remote_info, init_script

def main():
    paths = init_script()
    scp = get_remote_info("ssh", [("loopback", "ssh-scp")])
    sftp = get_remote_info("ssh", [("loopback", "ssh-sftp")])
    posix = get_remote_info("telnet", [("loopback", "telnet-posix")])
    console = get_remote_info("telnet", [("loopback", "telnet-console")])
    passive = get_remote_info("ftp", [("loopback", "ftp-passive")])
    active = get_remote_info("ftp", [("loopback", "ftp-active")])
    binary = create_file(paths.runtime_path, "payload.bin", bytes(range(256)) * 8)
    empty = create_file(paths.runtime_path, "empty.bin", b"")
    text = create_file(paths.runtime_path, "sample.sh", "#!/bin/sh\\necho READY\\n")
    exact = get_file(paths.runtime_path, "payload.bin")
    scp.upload(exact, "/incoming/scp")
    copied_scp = scp.download("/incoming/scp", "payload.bin")
    sftp.upload(copied_scp, "/incoming/sftp")
    copied_sftp = sftp.download("/incoming/sftp", "payload.bin")
    passive.upload(copied_sftp, "/incoming/passive")
    copied_passive = passive.download("/incoming/passive", "payload.bin")
    active.upload(copied_passive, "/incoming/active")
    copied_active = active.download("/incoming/active", "payload.bin")
    passive.upload(empty, "/incoming/passive")
    copied_empty = passive.download("/incoming/passive", "empty.bin")
    sftp.upload(text, "/incoming/sftp")
    assert copied_active.path.read_bytes() == binary.path.read_bytes()
    assert copied_empty.path.read_bytes() == b""
    result = scp.execute("demo fail")
    if result.status == CommandStatus.COMMAND_FAILED and result.exit_code == 7:
        result = scp.execute("demo recover")
    if result.status != CommandStatus.SUCCESS:
        return result
    print(scp.run("demo ready"))
    print(posix.run("demo ready"))
    known = posix.execute("demo fail")
    assert known.status == CommandStatus.COMMAND_FAILED and known.exit_code == 3
    unknown = console.execute("status")
    assert unknown.status == CommandStatus.RESULT_UNKNOWN
    if "READY" not in unknown.output:
        return unknown.with_failure("console did not return READY")
    print("BUSINESS_READY: protocol exit status stays unknown")
    console.mark_output()
    reboot = scp.execute("demo reboot", expect_disconnect=True)
    if reboot.status != CommandStatus.SUCCESS:
        return reboot
    print(console.telnet_wait_exec("Press Ctrl+B", b"\\x02"))
    print(console.wait_reboot("BOOT READY"))
    print(console.read())
    scp.retry(0.02, 3)
    passive.retry(0.02, 2)
    print(scp.run("demo after reboot"))
    print("SCRIPT_STDOUT_MARKER")
    print("SCRIPT_STDERR_MARKER", file=sys.stderr)
    os.write(1, b"FD_STDOUT_MARKER\\n")
    subprocess.run([sys.executable, "-c", "print('CHILD_STDOUT_MARKER')"], check=True)
    print("BINARY_ROUNDTRIP_OK")
    console.close()
    active.close()
    passive.close()
    posix.close()
    sftp.close()
    scp.close()
'''

GUARD = '''import ipaddress
import socket
_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex
def _check(address):
    if isinstance(address, tuple):
        host = address[0]
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise AssertionError("local simulation may connect only to loopback")
def _safe_connect(sock, address):
    _check(address)
    return _connect(sock, address)
def _safe_connect_ex(sock, address):
    _check(address)
    return _connect_ex(sock, address)
socket.socket.connect = _safe_connect
socket.socket.connect_ex = _safe_connect_ex
'''


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run(project: Path, environment: dict, arguments: list[str], expected: int) -> dict:
    command = [sys.executable, "-X", "utf8", "-m", "autoenv.cli", *arguments]
    completed = subprocess.run(command, cwd=project, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               encoding="utf-8", timeout=45)
    lines = completed.stdout.splitlines()
    if completed.returncode != expected or len(lines) != 1 or completed.stderr:
        raise AssertionError(f"CLI failed: {completed.returncode=} {completed.stdout=} {completed.stderr=}")
    log_path = Path(lines[0])
    if not log_path.is_absolute() or not log_path.is_file():
        raise AssertionError(f"CLI did not return one existing absolute log: {lines!r}")
    result = json.loads(log_path.with_name("result.json").read_text(encoding="utf-8"))
    record = {"command": subprocess.list2cmdline(command), "exit_code": completed.returncode,
              "stdout": completed.stdout.strip(), "stderr": completed.stderr,
              "status": result["status"], "log_path": str(log_path)}
    print(f"[{expected}] {record['command']}")
    print(f"    {log_path}")
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New simulation directory; defaults to repository logs/")
    parser.add_argument("--serve", action="store_true", help="Keep peers alive for manual CLI use after validation")
    args = parser.parse_args(argv)
    destination = args.output or ROOT / "logs" / ("local-simulation-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    project = destination / "project"
    project.mkdir()
    _write(project / "scripts" / "loopback_workflow.py", WORKFLOW)
    _write(project / "scripts" / "all_interfaces.py", (ROOT / "scripts" / "all_interfaces.py").read_text(encoding="utf-8"))
    _write(project / "config.json", json.dumps([{"name": "A1", "link": "/not-downloaded", "image_name": "^unused$"}]))
    _write(project / "inputs" / "input.bin", "local input\n")
    _write(project / "scripts" / "runtime_failure.py", '''from autoenv import init_script, get_remote_info
def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("loopback", "ssh-scp")])
    print("BEFORE_RUNTIME_FAILURE")
    dut.run("demo fail")
''')
    guard_dir = destination / "guard"
    _write(guard_dir / "sitecustomize.py", GUARD)
    environment = dict(os.environ)
    additions = [str(guard_dir), str(ROOT)]
    if (ROOT / ".test-deps").exists():
        additions.append(str(ROOT / ".test-deps"))
    environment["PYTHONPATH"] = os.pathsep.join(additions + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else []))
    payload = bytes(range(256)) * 8
    report: dict = {"scope": "loopback only; no HDFS; real SCP/SFTP/FTP bytes", "project": str(project)}
    stop = threading.Event()
    with ExitStack() as stack:
        ssh = stack.enter_context(LocalSSHServer(destination / "remote-ssh"))
        ssh.replies.update({"demo ready": CommandReply(stdout=(b"READY\n",)),
                            "demo fail": CommandReply(stderr=(b"EXPECTED FAILURE\n",), exit_code=7),
                            "demo recover": CommandReply(stdout=(b"RECOVERED\n",)),
                            "demo reboot": CommandReply(disconnect=True),
                            "reboot": CommandReply(disconnect=True),
                            "sh /tmp/autoenv/start.sh": CommandReply(stdout=(b"READY\n",)),
                            "printf 'CHECKED\\n'": CommandReply(stdout=(b"CHECKED\n",)),
                            "demo after reboot": CommandReply(stdout=(b"AFTER_REBOOT\n",))})
        posix = stack.enter_context(LocalTelnetServer(replies={
            "demo ready": TelnetReply(output=b"READY\r\n"),
            "demo fail": TelnetReply(output=b"EXPECTED FAILURE\r\n", exit_code=3)}))
        console = stack.enter_context(LocalTelnetServer(shell_mode="prompt_only", prompt="device>", passive=True,
            replies={"status": TelnetReply(output=b"READY\r\n")}, raw_reply=b"\r\nBOOT READY\r\ndevice> "))
        ftp = stack.enter_context(LocalFTPServer(destination / "remote-ftp"))
        configs = [
            {"name": "ssh-scp", "type": "ssh", "host": ssh.host, "port": ssh.port,
             "username": ssh.username, "password": ssh.password, "connect_timeout": 3, "upload_type": "scp"},
            {"name": "ssh-sftp", "type": "ssh", "host": ssh.host, "port": ssh.port,
             "username": ssh.username, "password": ssh.password, "connect_timeout": 3, "upload_type": "sftp"},
            {"name": "telnet-posix", "type": "telnet", "host": posix.host, "port": posix.port,
             "timeout": 3, "shell_mode": "posix"},
            {"name": "telnet-console", "type": "telnet", "host": console.host, "port": console.port,
             "timeout": 3, "shell_mode": "prompt_only", "probe_command": "status", "probe_keyword": "READY"},
            {"name": "ftp-passive", "type": "ftp", "host": ftp.host, "port": ftp.port,
             "username": ftp.username, "password": ftp.password, "timeout": 3, "passive": True},
            {"name": "ftp-active", "type": "ftp", "host": ftp.host, "port": ftp.port,
             "username": ftp.username, "password": ftp.password, "timeout": 3, "passive": False},
        ]
        _write(project / "environments" / "topologies" / "loopback" / "connections.json", json.dumps(configs, indent=2))
        demo_configs = [{**configs[index], "name": name} for index, name in
                        ((0, "设备-ssh"), (3, "设备-串口"), (4, "文件-ftp"))]
        _write(project / "environments" / "topologies" / "示例组网" / "connections.json",
               json.dumps(demo_configs, ensure_ascii=False, indent=2))

        def boot_output():
            delivered = 0
            while not stop.wait(0.01):
                count = sum(command in {"demo reboot", "reboot"} for command in ssh.commands)
                if count > delivered:
                    console.push_output(b"\r\nPress Ctrl+B\r\n")
                    delivered = count

        watcher = threading.Thread(target=boot_output, name="simulated-device-boot")
        watcher.start()
        try:
            bindings = ["scp=ssh-scp", "sftp=ssh-sftp", "posix=telnet-posix", "console=telnet-console",
                        "passive=ftp-passive", "active=ftp-active"]
            command = ["run", "loopback_workflow", "--non-interactive", "--topology", "loopback"]
            for binding in bindings:
                command.extend(["--bind", binding])
            successes = _run(project, environment, command, 0)
            log = Path(successes["log_path"]).read_text(encoding="utf-8")
            for marker in ("SCRIPT_STDOUT_MARKER", "SCRIPT_STDERR_MARKER", "FD_STDOUT_MARKER", "CHILD_STDOUT_MARKER",
                           "BINARY_ROUNDTRIP_OK", "FLOW START", "loopback_workflow.py", "command_failed", "result_unknown"):
                assert marker in log, f"missing log evidence: {marker}"
            assert ssh.path("/incoming/scp/payload.bin").read_bytes() == payload
            assert ssh.path("/incoming/sftp/payload.bin").read_bytes() == payload
            assert ftp.read_bytes("/incoming/passive/payload.bin") == payload
            assert ftp.read_bytes("/incoming/active/payload.bin") == payload
            assert {"active", "passive"}.issubset(set(ftp.data_modes))
            assert console.wait_for_raw(b"\x02")
            assert ssh.commands.count("demo reboot") == 1
            assert "demo after reboot" in ssh.commands
            interface_example = _run(project, environment, ["run", "all_interfaces", "--non-interactive",
                "--topology", "示例组网", "--bind", "dut=设备-ssh", "--bind", "console=设备-串口",
                "--bind", "ftp=文件-ftp", "--arg", "local_dir=inputs", "--arg", "filename=input.bin",
                "--arg", "include_hdfs=false"], 0)
            count = len(ssh.auth_attempts)
            preparation = _run(project, environment, ["run", "runtime_failure", "--non-interactive",
                "--topology", "loopback", "--bind", "dut=missing"], 2)
            assert len(ssh.auth_attempts) == count, "invalid request connected before precheck"
            failure = _run(project, environment, ["run", "runtime_failure", "--non-interactive",
                "--topology", "loopback", "--bind", "dut=ssh-scp"], 1)
            assert "BEFORE_RUNTIME_FAILURE" in Path(failure["log_path"]).read_text(encoding="utf-8")
            assert not any(peer.errors for peer in (ssh, posix, console, ftp))
            report.update({"runs": [successes, interface_example, preparation, failure], "verified": {
                "payload_bytes": len(payload), "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "scp_and_sftp": True, "ftp_active_and_passive": True, "empty_file": True,
                "telnet_posix_exit_codes": [0, 3], "ssh_exit_codes": [0, 7],
                "telnet_prompt_status": "result_unknown", "ctrl_b_hex": "02",
                "reboot_sent_count": ssh.commands.count("demo reboot"),
                "after_retry_command_delivered": True, "invalid_precheck_connections": 0,
                "repository_all_interfaces_example": True, "hdfs_download_performed": False,
                "stdout_stderr_fd_child_output_in_log": True},
                "remote_commands": {"ssh": ssh.commands, "telnet_posix": posix.commands,
                                    "telnet_console": console.commands, "ftp": ftp.commands},
                "ftp_data_modes": ftp.data_modes,
                "ssh_transfers": ssh.transfers, "ftp_uploads": [path for path, _ in ftp.transfers],
                "ftp_downloads": [path for path, _ in ftp.downloads]})
            _write(destination / "report.json", json.dumps(report, ensure_ascii=False, indent=2))
            print("PASS: real protocol bytes, command delivery, branches, Ctrl+B, recovery, CLI and logs")
            print("REPORT: " + str(destination / "report.json"))
            if args.serve:
                print("Peers remain active; run the recorded commands from " + str(project) + "; Ctrl+C stops them.")
                while not stop.wait(1):
                    pass
        finally:
            stop.set()
            watcher.join(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
