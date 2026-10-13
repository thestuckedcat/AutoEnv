"""Serial readiness and login compatibility over a real loopback TCP stream."""

from __future__ import annotations

import pytest

from autoenv.errors import AutoEnvOperationError
from autoenv.remote_environment import create_remote
from autoenv.results import CommandStatus
from autoenv.script_session import ExecutionSession
from local_remote.telnet import LocalTelnetServer, TelnetReply


@pytest.fixture
def session(tmp_path):
    value = ExecutionSession(tmp_path / "project")
    value.runtime_path.mkdir()
    try:
        yield value
    finally:
        value.close()


def config(peer, **extra):
    return {"name": "console", "topology": "loopback", "type": "telnet",
            "host": peer.host, "port": peer.port, "timeout": 0.25,
            "shell_mode": "auto", **extra}


@pytest.mark.parametrize("shell_mode", ["auto", "posix", "prompt_only"])
def test_credentials_are_not_sent_to_an_already_logged_in_console(session, shell_mode):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only" if shell_mode == "prompt_only" else "posix",
                           replies={"inspect": TelnetReply(output=b"READY\r\n")}) as peer:
        remote = create_remote(session, config(peer, shell_mode=shell_mode,
                                              username="configured-user", password="configured-password"))
        assert remote.available, remote.self_check_result
        result = remote.execute("inspect")
        assert "READY" in result.stdout
        assert result.status is (CommandStatus.RESULT_UNKNOWN if shell_mode == "prompt_only" else CommandStatus.SUCCESS)
        assert not peer.login_attempts
        assert "configured-user" not in peer.commands
        assert "configured-password" not in peer.commands


def test_last_login_banner_does_not_trigger_a_login_exchange(session):
    with LocalTelnetServer(passive=True, banner=b"Last login: Thu Jan 6 04:36:49 2000\r\n") as peer:
        remote = create_remote(session, config(peer, shell_mode="posix",
                                              username="configured-user", password="configured-password"))
        assert remote.available, remote.self_check_result
        assert "configured-user" not in peer.commands and not peer.login_attempts


@pytest.mark.parametrize("shell_mode", ["auto", "posix", "prompt_only"])
def test_real_login_after_a_last_login_banner_consumes_the_active_prompt(session, shell_mode):
    with LocalTelnetServer(passive=True, login_required=True,
                           shell_mode="prompt_only" if shell_mode == "prompt_only" else "posix",
                           banner=b"Last login: Thu Jan 6 04:36:49 2000\r\n") as peer:
        remote = create_remote(session, config(peer, shell_mode=shell_mode,
                                              username=peer.username, password=peer.password))
        assert remote.available, remote.self_check_result
        assert peer.login_attempts == [(peer.username, peer.password)]


def test_password_banner_does_not_leave_the_active_challenge_unconsumed(session):
    class PasswordBannerPeer(LocalTelnetServer):
        def _send(self, connection, data):
            if data == (self.password_prompt + " ").encode():
                super()._send(connection, b"Notice Password: challenge will follow\r\n")
            super()._send(connection, data)

    with PasswordBannerPeer(passive=True, login_required=True) as peer:
        remote = create_remote(session, config(peer, shell_mode="posix",
                                              username=peer.username, password=peer.password))
        assert remote.available, remote.self_check_result
        assert peer.login_attempts == [(peer.username, peer.password)]
        assert "Password:" not in remote.read()


@pytest.mark.parametrize("shell_mode", ["auto", "posix", "prompt_only"])
@pytest.mark.parametrize("password", ["", "unused-configured-password"])
def test_username_only_login_does_not_send_a_password_to_the_shell(session, shell_mode, password):
    # This gateway requests only a username, then enters its shell directly.
    with LocalTelnetServer(passive=True, prompt="login:",
                           shell_mode="prompt_only" if shell_mode == "prompt_only" else "posix",
                           replies={"root": TelnetReply(prompt="root@loopback:~#"),
                                    "inspect": TelnetReply(output=b"READY\r\n")}) as peer:
        remote = create_remote(session, config(peer, shell_mode=shell_mode, username="root", password=password))
        assert remote.available, remote.self_check_result
        result = remote.execute("inspect")
        assert "READY" in result.stdout
        assert result.status is (CommandStatus.RESULT_UNKNOWN if shell_mode == "prompt_only" else CommandStatus.SUCCESS)
        assert peer.commands[0] == "root"
        assert "unused-configured-password" not in peer.commands


@pytest.mark.parametrize("reply", [TelnetReply(prompt="login:"),
                                   TelnetReply(output=b"login pending\r\n", complete=False)])
def test_unknown_or_rejected_username_response_remains_unavailable(session, reply):
    with LocalTelnetServer(passive=True, prompt="login:", replies={"root": reply}) as peer:
        remote = create_remote(session, config(peer, shell_mode="posix", username="root", password="unused-password"))
        assert not remote.available
        with pytest.raises(AutoEnvOperationError):
            remote.execute("inspect")
        assert peer.commands == ["root"]


@pytest.mark.parametrize("passive", [False, True])
def test_auto_appliance_readiness_does_not_run_a_posix_selfcheck(session, passive):
    with LocalTelnetServer(passive=passive, shell_mode="prompt_only", prompt="router>",
                           replies={"inspect": TelnetReply(output=b"READY\r\n")}) as peer:
        remote = create_remote(session, config(peer))
        assert remote.available, remote.self_check_result
        assert remote.self_check_result.check_scope == "prompt"
        assert remote.self_check_result.exit_status_available is False
        assert peer.commands == []
        result = remote.execute("inspect")
        assert result.status is CommandStatus.RESULT_UNKNOWN and result.exit_code is None
        assert "READY" in result.stdout and peer.commands == ["inspect"]


def test_explicit_prompt_readiness_preserves_banner_without_sending_commands(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only", prompt="loader>") as peer:
        remote = create_remote(session, config(peer, shell_mode="prompt_only"))
        assert remote.available and remote.transport_ready
        assert remote.self_check_result.check_scope == "prompt"
        assert remote.self_check_result.exit_status_available is False
        assert "Local Telnet test peer" in remote.read()
        assert peer.commands == [] and peer.probes == [] and peer.raw_inputs == []


def test_auto_appliance_uses_an_explicit_business_probe(session):
    with LocalTelnetServer(shell_mode="prompt_only", prompt="router>", replies={
        "status": TelnetReply(output=b"APPLIANCE_READY\r\n"),
        "inspect": TelnetReply(output=b"READY\r\n"),
    }) as peer:
        remote = create_remote(session, config(peer, probe_command="status", probe_keyword="APPLIANCE_READY"))
        assert remote.available, remote.self_check_result
        assert remote.self_check_result.check_scope == "keyword"
        assert remote.self_check_result.exit_status_available is False
        assert peer.commands == ["status"]
        result = remote.execute("inspect")
        assert result.status is CommandStatus.RESULT_UNKNOWN and "READY" in result.stdout
        assert peer.commands == ["status", "inspect"]


@pytest.mark.parametrize("extra", [{"probe_command": "status"}, {"probe_keyword": "READY"}])
def test_partial_business_probe_configuration_fails_without_sending_a_command(session, extra):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only") as peer:
        remote = create_remote(session, config(peer, shell_mode="prompt_only", **extra))
        assert not remote.available
        assert "probe_command" in remote.self_check_result.error_message
        assert "probe_keyword" in remote.self_check_result.error_message
        assert peer.commands == []


def test_posix_readiness_proves_unique_output_and_zero_exit_status(session):
    with LocalTelnetServer() as peer:
        remote = create_remote(session, config(peer, shell_mode="posix"))
        assert remote.available
        assert remote.self_check_result.check_scope == "command"
        assert remote.self_check_result.exit_status_available is True
        assert len(peer.commands) == 1 and "__AUTOENV_SELF_CHECK_" in peer.commands[0]


def test_wrong_credentials_do_not_become_prompt_readiness(session):
    with LocalTelnetServer(passive=True, login_required=True) as peer:
        remote = create_remote(session, config(peer, username=peer.username, password="incorrect"))
        assert not remote.available and remote.self_check_result.error_type == "AUTH_FAILED"
        with pytest.raises(AutoEnvOperationError):
            remote.execute("inspect")
        assert peer.login_attempts == [(peer.username, "incorrect")] and peer.commands == []


def test_unrecognized_console_output_does_not_count_as_ready(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only", prompt="Booting, please wait") as peer:
        remote = create_remote(session, config(peer, shell_mode="prompt_only"))
        assert not remote.available and remote.self_check_result.error_type == "_PromptNotFound"
        assert peer.commands == []


def test_auto_posix_detection_waits_past_an_intermediate_prompt(session):
    class IntermediateProbePrompt(LocalTelnetServer):
        def _send(self, connection, data):
            if isinstance(data, bytes) and "__AUTOENV_PROBE_" in data.decode("utf-8", errors="ignore"):
                super()._send(connection, ("\r\n" + self.prompt + " ").encode())
                self._stop.wait(0.03)
            super()._send(connection, data)

    with IntermediateProbePrompt() as peer:
        remote = create_remote(session, config(peer))
        assert remote.available, remote.self_check_result
        assert remote.self_check_result.exit_status_available is True
        result = remote.execute("inspect")
        assert result.status is CommandStatus.SUCCESS and result.exit_code == 0
