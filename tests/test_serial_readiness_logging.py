"""The script summary distinguishes serial readiness and shows failed probes."""

from autoenv.remote_environment import create_remote
from test_serial_compatibility import config, session
from local_remote.telnet import LocalTelnetServer, TelnetReply


def test_prompt_readiness_summary_does_not_claim_a_known_exit_status(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only") as peer:
        remote = create_remote(session, config(peer, shell_mode="prompt_only"))
        assert remote.available
        summary = session.output_path.read_text(encoding="utf-8")
        assert "check_scope=prompt" in summary
        assert "exit_status=unknown" in summary
        assert "exit=0" not in summary


def test_failed_business_probe_summary_includes_classified_cause_without_raw_output(session):
    with LocalTelnetServer(passive=True, shell_mode="prompt_only",
                           replies={"status": TelnetReply(output=b"PRIVATE_RAW_DEVICE_LINE\r\n")}) as peer:
        remote = create_remote(session, config(peer, shell_mode="prompt_only",
                                              probe_command="status", probe_keyword="READY"))
        assert not remote.available
        summary = session.output_path.read_text(encoding="utf-8")
        assert "unavailable" in summary
        assert "read timed out" in summary
        assert "PRIVATE_RAW_DEVICE_LINE" not in summary
