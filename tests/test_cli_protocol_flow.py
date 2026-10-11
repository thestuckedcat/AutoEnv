"""Invoke the production CLI with real loopback protocol peers and guarded children."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

from autoenv.remote_catalog import save_connection
from local_remote.ftp import LocalFTPServer
from local_remote.ssh import CommandReply, LocalSSHServer
from local_remote.telnet import LocalTelnetServer, TelnetReply


REPO = Path(__file__).resolve().parents[1]
GUARD = '''import ipaddress
import socket
_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex
def _check(address):
    if isinstance(address, tuple):
        try:
            allowed = address[0] == "localhost" or ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            raise AssertionError("CLI child may connect only to loopback: " + repr(address))
def _guard_connect(sock, address):
    _check(address)
    return _connect(sock, address)
def _guard_connect_ex(sock, address):
    _check(address)
    return _connect_ex(sock, address)
socket.socket.connect = _guard_connect
socket.socket.connect_ex = _guard_connect_ex
'''


def invoke(project, source, *, bindings, script="flow"):
    scripts = project / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / f"{script}.py"
    path.write_text(source, encoding="utf-8")
    guard = project / "child_guard"
    guard.mkdir(exist_ok=True)
    (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(guard), str(REPO), str(REPO / ".test-deps")))
    command = [sys.executable, "-X", "utf8", "-m", "autoenv.cli", "run", script,
               "--non-interactive", "--topology", "loopback"]
    for value in bindings:
        command.extend(("--bind", value))
    result = subprocess.run(command, cwd=project, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
    assert result.stderr == "", result.stderr
    assert len(result.stdout.splitlines()) == 1, result.stdout
    log_path = Path(result.stdout.strip())
    assert log_path.is_absolute() and log_path.is_file()
    state = json.loads(log_path.with_name("result.json").read_text(encoding="utf-8"))
    return result, log_path, state, path


def test_cli_real_ssh_telnet_ftp_file_chain_and_handled_branch(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with ExitStack() as stack:
        ssh = stack.enter_context(LocalSSHServer(tmp_path / "remote-ssh"))
        telnet = stack.enter_context(LocalTelnetServer(replies={
            "ready": TelnetReply(output=b"READY\r\n"), "fail": TelnetReply(exit_code=3),
        }))
        ftp = stack.enter_context(LocalFTPServer(tmp_path / "remote-ftp"))
        ssh.replies.update({"fail": CommandReply(stdout=(b"failure evidence\n",), exit_code=7),
                            "recover": CommandReply(stdout=(b"RECOVERED\n",))})
        for method in ("scp", "sftp"):
            save_connection(project, "loopback", {
                "name": method, "type": "ssh", "host": ssh.host, "port": ssh.port,
                "username": ssh.username, "password": ssh.password, "upload_type": method,
                "connect_timeout": 2,
            })
        save_connection(project, "loopback", {
            "name": "telnet", "type": "telnet", "host": telnet.host, "port": telnet.port,
            "timeout": 1, "shell_mode": "posix",
        })
        for passive in (True, False):
            save_connection(project, "loopback", {
                "name": "passive" if passive else "active", "type": "ftp", "host": ftp.host,
                "port": ftp.port, "username": ftp.username, "password": ftp.password,
                "timeout": 2, "passive": passive,
            })
        source = '''from autoenv import CommandStatus, create_file, get_file, get_remote_info, init_script
def main():
    paths = init_script()
    scp = get_remote_info("ssh", [("loopback", "scp")])
    sftp = get_remote_info("ssh", [("loopback", "sftp")])
    console = get_remote_info("telnet", [("loopback", "telnet")])
    passive = get_remote_info("ftp", [("loopback", "passive")])
    active = get_remote_info("ftp", [("loopback", "active")])
    created = create_file(paths.runtime_path, "data.bin", bytes(range(256)) * 1025)
    exact = get_file(paths.runtime_path, "data.bin")
    scp.upload(exact, "/chain/scp")
    a = scp.download("/chain/scp", "data.bin")
    sftp.upload(a, "/chain/sftp")
    b = sftp.download("/chain/sftp", "data.bin")
    passive.upload(b, "/chain/passive")
    c = passive.download("/chain/passive", "data.bin")
    active.upload(c, "/chain/active")
    d = active.download("/chain/active", "data.bin")
    assert d.path.read_bytes() == created.path.read_bytes()
    failed = scp.execute("fail")
    if failed.status == CommandStatus.COMMAND_FAILED and failed.exit_code == 7:
        assert "RECOVERED" in scp.run("recover")
    else:
        return failed
    assert "READY" in console.run("ready")
    failed = console.execute("fail")
    assert failed.status == CommandStatus.COMMAND_FAILED and failed.exit_code == 3
    print("HANDLED_FAILURES_AND_BYTES_OK")
'''
        completed, log_path, state, script = invoke(project, source, bindings=(
            "scp=scp", "sftp=sftp", "console=telnet", "passive=passive", "active=active",
        ))
        assert completed.returncode == 0 and state["success"] is True
        assert state["final_operation"]["result"]["status"] == "command_failed"
        log = log_path.read_text(encoding="utf-8")
        assert "HANDLED_FAILURES_AND_BYTES_OK" in log and "failure evidence" in log
        assert "TELNET[loopback/telnet] RAW RECEIVE hex=" in log
        summary = log_path.with_name("output.log").read_text(encoding="utf-8")
        assert "command_failed exit=7" in summary and "command_failed exit=3" in summary
        assert "failure evidence" not in summary and "HANDLED_FAILURES" not in summary
        for operation in state["operations"]:
            assert Path(operation["flow"]["source"]["file"]) == script
            assert operation["flow"]["source"]["line"] > 0
        payload = bytes(range(256)) * 1025
        assert ssh.path("/chain/scp/data.bin").read_bytes() == payload
        assert ssh.path("/chain/sftp/data.bin").read_bytes() == payload
        assert ftp.read_bytes("/chain/passive/data.bin") == payload
        assert ftp.read_bytes("/chain/active/data.bin") == payload
        assert ftp.data_modes == ["passive", "passive", "active", "active"]
        assert ssh.commands.count("fail") == ssh.commands.count("recover") == 1
        assert telnet.commands.count("ready") == telnet.commands.count("fail") == 1
    assert not any(peer.errors for peer in (ssh, telnet, ftp))


def test_cli_unknown_console_keyword_is_explicit_business_success(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with LocalTelnetServer(passive=True, shell_mode="prompt_only", prompt="device>", replies={
        "status": TelnetReply(output=b"READY\r\n"),
    }) as server:
        save_connection(project, "loopback", {
            "name": "console", "type": "telnet", "host": server.host, "port": server.port,
            "timeout": 1, "shell_mode": "prompt_only", "probe_command": "status", "probe_keyword": "READY",
        })
        source = '''from autoenv import CommandStatus, get_remote_info, init_script
def main():
    paths = init_script()
    console = get_remote_info("telnet", [("loopback", "console")])
    result = console.execute("status")
    if result.status == CommandStatus.RESULT_UNKNOWN and "READY" in result.output:
        print("BUSINESS_READY")
    else:
        return result.with_failure("missing READY")
'''
        completed, log_path, state, _ = invoke(project, source, bindings=("console=console",))
        assert completed.returncode == 0 and state["success"] is True
        assert state["final_operation"]["result"]["status"] == "result_unknown"
        assert state["final_operation"]["result"]["exit_code"] is None
        assert "BUSINESS_READY" in log_path.read_text(encoding="utf-8")


def test_cli_unrecovered_selfcheck_is_final_failure_even_after_passive_read(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    with LocalTelnetServer(passive=True, shell_mode="prompt_only") as server:
        save_connection(project, "loopback", {
            "name": "console", "type": "telnet", "host": server.host, "port": server.port,
            "timeout": 1, "shell_mode": "prompt_only",
        })
        source = '''from autoenv import get_remote_info, init_script
def main():
    paths = init_script()
    console = get_remote_info("telnet", [("loopback", "console")])
    print(console.read())
'''
        completed, log_path, state, _ = invoke(project, source, bindings=("console=console",))
        assert completed.returncode == 1 and state["status"] == "self_check_failed"
        assert state["final_operation"]["result"]["status"] == "unavailable"
        assert state["final_operation"]["result"]["success"] is False
        assert "unavailable" in log_path.with_name("output.log").read_text(encoding="utf-8")
        assert server.commands == []


def test_cli_child_loopback_guard_blocks_external_target(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    save_connection(project, "loopback", {
        "name": "blocked", "type": "ssh", "host": "192.0.2.1", "port": 22, "connect_timeout": 0.1,
    })
    source = '''from autoenv import get_remote_info, init_script
def main():
    paths = init_script()
    dut = get_remote_info("ssh", [("loopback", "blocked")])
'''
    completed, log_path, state, _ = invoke(project, source, bindings=("dut=blocked",))
    assert completed.returncode == 1 and state["status"] == "self_check_failed"
    assert "CLI child may connect only to loopback" in log_path.read_text(encoding="utf-8")
