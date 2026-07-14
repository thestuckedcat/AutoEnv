from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoenv import cli
from autoenv.registry import ScriptDefinition
from autoenv.runtime import RunMode


def _definition(name: str, description: str) -> ScriptDefinition:
    return ScriptDefinition(
        name=name,
        description=description,
        body=lambda _ctx: None,
        entrypoint=lambda: None,  # type: ignore[arg-type]
    )


def _result(*, success: bool, status: str = "success") -> SimpleNamespace:
    return SimpleNamespace(
        success=success,
        status=status,
        script_name="demo",
        run_dir="temporary-run",
        error_message=None if success else "offline failure",
    )


def test_default_cli_menu_selects_script_and_uses_run_mode(tmp_path, monkeypatch, capsys):
    scripts = [_definition("alpha", "first"), _definition("beta", "second")]
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "list_scripts", lambda *, root_dir: scripts)
    monkeypatch.setattr("builtins.input", lambda _: "2")

    def fake_run_script(name: str, **kwargs: object):
        captured.update(name=name, **kwargs)
        return _result(success=True)

    monkeypatch.setattr(cli, "run_script", fake_run_script)
    code = cli.main(["run"], root_dir=tmp_path)

    output = capsys.readouterr()
    assert code == 0
    assert "Available AutoEnv scripts:" in output.out
    assert "1. alpha" in output.out and "2. beta" in output.out
    assert "AutoEnv script completed: demo" in output.out
    assert captured == {
        "name": "beta",
        "mode": RunMode.RUN,
        "root_dir": tmp_path.resolve(),
    }


def test_cli_without_subcommand_uses_menu(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "list_scripts", lambda *, root_dir: [_definition("alpha", "first")]
    )
    monkeypatch.setattr("builtins.input", lambda _: "1")
    monkeypatch.setattr(cli, "run_script", lambda *args, **kwargs: _result(success=True))

    assert cli.main([], root_dir=tmp_path) == 0
    assert "Available AutoEnv scripts:" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("answer", "message"),
    [("not-a-number", "must be a number"), ("0", "out of range"), ("2", "out of range")],
)
def test_cli_menu_rejects_invalid_selection(
    tmp_path, monkeypatch, capsys, answer: str, message: str
):
    monkeypatch.setattr(
        cli, "list_scripts", lambda *, root_dir: [_definition("only", "one")]
    )
    monkeypatch.setattr("builtins.input", lambda _: answer)
    monkeypatch.setattr(
        cli,
        "run_script",
        lambda *args, **kwargs: pytest.fail("invalid menu selection executed a script"),
    )
    assert cli.main(["run"], root_dir=tmp_path) == 1
    assert message in capsys.readouterr().err


def test_cli_menu_reports_an_empty_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "list_scripts", lambda *, root_dir: [])
    assert cli.main(["run"], root_dir=tmp_path) == 1
    assert "No registered AutoEnv scripts" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("command", "success", "expected_code"),
    [
        ("run", True, 0),
        ("rerun", True, 0),
        ("run", False, 1),
        ("rerun", False, 1),
    ],
)
def test_explicit_run_and_rerun_exit_codes(
    tmp_path, monkeypatch, capsys, command: str, success: bool, expected_code: int
):
    captured: dict[str, object] = {}

    def fake_run_script(name: str, **kwargs: object):
        captured.update(name=name, **kwargs)
        return _result(success=success, status="success" if success else "command_failed")

    monkeypatch.setattr(cli, "run_script", fake_run_script)
    assert cli.main([command, "demo"], root_dir=tmp_path) == expected_code
    assert captured["name"] == "demo"
    assert captured["mode"] == RunMode(command)
    assert captured["root_dir"] == tmp_path.resolve()
    output = capsys.readouterr()
    if success:
        assert "completed" in output.out
        assert output.err == ""
    else:
        assert "status=command_failed" in output.err


@pytest.mark.parametrize("error", [KeyError("unknown"), ValueError("bad"), RuntimeError("broken")])
def test_cli_maps_known_run_errors_to_exit_code_two(tmp_path, monkeypatch, capsys, error):
    def raise_error(*_args: object, **_kwargs: object):
        raise error

    monkeypatch.setattr(cli, "run_script", raise_error)
    assert cli.main(["run", "demo"], root_dir=tmp_path) == 2
    assert "AutoEnv error:" in capsys.readouterr().err
