"""The real CLI runs undecorated files; stdout is one existing log address."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autoenv import create_file, get_file
from autoenv.remote_catalog import save_connection
from autoenv.script_files import inspect_script


REPO = Path(__file__).resolve().parents[1]


def invoke(root, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(REPO), str(REPO / ".test-deps")))
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "autoenv.cli", *args],
        cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=20,
    )


def write_script(root, source):
    directory = root / "scripts"
    directory.mkdir(exist_ok=True)
    path = directory / "demo.py"
    path.write_text(source, encoding="utf-8")
    return path


def log_of(completed, code):
    assert completed.returncode == code, completed.stderr + completed.stdout
    assert completed.stderr == ""
    assert len(completed.stdout.splitlines()) == 1
    path = Path(completed.stdout.strip())
    assert path.is_absolute() and path.is_file()
    return path, path.read_text(encoding="utf-8")


def test_plain_main_captures_python_descriptor_and_child_output(tmp_path):
    source = """from autoenv import init_script, create_file, get_file
import os, subprocess, sys
SCRIPT_ARGUMENTS = [{"name":"label","type":"str","required":True}]
def main():
    paths = init_script()
    handle = create_file(paths.runtime_path, "a.bin", b'\\x00\\xff')
    assert get_file(paths.runtime_path, "a.bin").sha256 == handle.sha256
    print("BODY:" + paths.arguments["label"])
    print("ERR", file=sys.stderr)
    os.write(1, b"FD-ONE\\n")
    subprocess.run([sys.executable, "-c", "print('CHILD')"], check=True)
    return "FINAL-TEXT"
"""
    script = write_script(tmp_path, source)
    event = tmp_path / "event.json"
    completed = invoke(tmp_path, "run", "demo", "--non-interactive", "--arg", "label=hello=world", "--event-file", str(event))
    path, log = log_of(completed, 0)
    for text in ("BODY:hello=world", "ERR", "FD-ONE", "CHILD", "FINAL-TEXT"):
        assert log.count(text) == 1
    assert json.dumps(str(script))[1:-1] in log and '"line": 6' in log
    paths = json.loads(event.read_text(encoding="utf-8"))
    assert paths["log_path"] == str(path)
    summary = Path(paths["output_path"]).read_text(encoding="utf-8")
    assert "create_file" in summary and "get_file" in summary
    assert "BODY:" not in summary and "CHILD" not in summary
    result = json.loads(path.with_name("result.json").read_text(encoding="utf-8"))
    assert result["success"] is True and len(result["operations"]) == 2


@pytest.mark.parametrize("arguments", [
    ["--arg", "count=no"], ["--arg", "unknown=1"],
    ["--arg", "count=1", "--arg", "count=2"], ["--bad"],
])
def test_preparation_failures_have_log_and_no_runtime(tmp_path, arguments):
    write_script(tmp_path, 'from autoenv import init_script\nSCRIPT_ARGUMENTS=[{"name":"count","type":"int","required":True}]\ndef main():\n    init_script()\n    raise AssertionError("body must not run")\n')
    path, log = log_of(invoke(tmp_path, "run", "demo", "--non-interactive", *arguments), 2)
    assert not (path.parent / "runtime").exists()
    assert "preparation_failed" in log and "EXCEPTION" in log
    assert "AssertionError: body" not in log


def test_runtime_exception_keeps_traceback_and_exit_one(tmp_path):
    write_script(tmp_path, 'from autoenv import init_script\ndef main():\n    init_script()\n    print("before-failure")\n    raise RuntimeError("intentional")\n')
    path, log = log_of(invoke(tmp_path, "run", "demo", "--non-interactive"), 1)
    assert "before-failure" in log and "RuntimeError: intentional" in log
    assert (path.parent / "runtime").is_dir()


def test_list_and_describe_read_ast_without_import(tmp_path):
    write_script(tmp_path, 'from autoenv import init_script\nraise AssertionError("do not import")\ndef main():\n    init_script()\n')
    listing = invoke(tmp_path, "list", "--json")
    assert listing.returncode == 0
    assert json.loads(listing.stdout)[0]["script"] == "demo"
    description = invoke(tmp_path, "describe", "demo", "--json")
    assert description.returncode == 0
    assert json.loads(description.stdout)["entry"] == "main"
    assert not (tmp_path / "logs").exists()


def test_request_matches_flags_and_rejects_script_conflict(tmp_path):
    write_script(tmp_path, 'from autoenv import init_script\nSCRIPT_ARGUMENTS=[{"name":"flag","type":"bool","required":True}]\ndef main():\n    paths=init_script()\n    print(paths.arguments["flag"])\n')
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version":2, "script":"demo", "arguments":{"flag":True}}), encoding="utf-8")
    _, log = log_of(invoke(tmp_path, "run", "--request", str(request)), 0)
    assert "\nTrue\n" in log
    log_of(invoke(tmp_path, "run", "other", "--request", str(request)), 2)
    log_of(invoke(tmp_path, "run", "--request", str(request), "--arg", "flag=true"), 2)


def test_whitelist_and_missing_binding_are_rejected_before_connection(tmp_path):
    save_connection(tmp_path, "lab", {"name":"ssh", "type":"ssh", "host":"127.0.0.1", "port":1})
    write_script(tmp_path, 'from autoenv import init_script, get_remote_info\ndef main():\n    init_script()\n    dut=get_remote_info("ssh", [("lab","ssh")])\n')
    for flags in ([], ["--topology","lab"], ["--topology","lab","--bind","dut=other"]):
        _, log = log_of(invoke(tmp_path, "run", "demo", "--non-interactive", *flags), 2)
        assert "REMOTE SELF CHECK" not in log


def test_file_handles_are_precise_mutation_checked_and_standalone(tmp_path):
    handle = create_file(tmp_path, "b.bin", b"original")
    assert get_file(tmp_path, "b.bin").path == handle.path
    handle.path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        handle.verify()
    with pytest.raises(ValueError):
        get_file(tmp_path, "../b.bin")
    with pytest.raises(FileNotFoundError):
        get_file(tmp_path, "*.bin")


def test_dynamic_environment_layout_is_rejected(tmp_path):
    path = write_script(tmp_path, 'from autoenv import init_script, get_remote_info\ndef main():\n    init_script()\n    if True:\n        dut=get_remote_info("ssh", [("lab","ssh")])\n')
    with pytest.raises(ValueError, match="unconditional"):
        inspect_script(path)


def test_top_level_python_exit_zero_uses_the_same_finalization(tmp_path):
    write_script(tmp_path, 'from autoenv import init_script\ninit_script()\nprint("TOP LEVEL")\nraise SystemExit(0)\n')
    path, log = log_of(invoke(tmp_path, "run", "demo", "--non-interactive"), 0)
    assert "TOP LEVEL" in log
    assert json.loads(path.with_name("result.json").read_text(encoding="utf-8"))["success"] is True


def test_interactive_parameter_prompt_is_visible_and_does_not_pollute_stdout(tmp_path):
    write_script(tmp_path, 'from autoenv import init_script\nSCRIPT_ARGUMENTS=[{"name":"value","type":"str","required":True}]\ndef main():\n    paths=init_script()\n    print("VALUE:"+paths.arguments["value"])\n')
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(REPO), str(REPO / ".test-deps")))
    completed = subprocess.run([sys.executable,"-X","utf8","-m","autoenv.cli","run","demo"], cwd=tmp_path,
                               input="answer\n", capture_output=True, text=True, encoding="utf-8", env=env, timeout=10)
    assert completed.returncode == 0
    assert "参数 value" in completed.stderr
    assert len(completed.stdout.splitlines()) == 1
    log = Path(completed.stdout.strip()).read_text(encoding="utf-8")
    assert "VALUE:answer" in log


def test_event_location_write_failure_still_returns_real_preparation_log(tmp_path):
    write_script(tmp_path, 'from autoenv import init_script\ndef main():\n    init_script()\n')
    path, log = log_of(invoke(tmp_path, "run", "demo", "--non-interactive", "--event-file", str(tmp_path)), 2)
    assert "EVENT LOCATION FAILED" in log
    assert not (path.parent / "runtime").exists()


def test_with_init_script_closes_body_and_preserves_hosted_result(tmp_path):
    write_script(tmp_path, 'from autoenv import init_script, create_file\ndef main():\n    with init_script() as paths:\n        create_file(paths.runtime_path, "within.txt", "DONE")\n        print("WITH-COMPLETE")\n')
    path, log = log_of(invoke(tmp_path, "run", "demo", "--non-interactive"), 0)
    assert "WITH-COMPLETE" in log
    assert (path.parent / "runtime" / "within.txt").read_text(encoding="utf-8") == "DONE"
