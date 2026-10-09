from __future__ import annotations

import json
import os
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


@pytest.fixture
def web_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from webPage import server

    (tmp_path / "scripts").mkdir()
    (tmp_path / "webPage").mkdir()
    (tmp_path / "scripts" / "demo.py").write_text(
        '"""Local file workflow."""\n'
        'from autoenv import init_script, create_file\n'
        'SCRIPT_ARGUMENTS = [{"name": "message", "type": "str", "required": True}]\n'
        'def main():\n'
        '    paths = init_script()\n'
        '    create_file(paths.runtime_path, "hello.txt", paths.arguments["message"])\n'
        '    print("LOG_ONLY 原始用户输出", flush=True)\n',
        encoding="utf-8",
    )
    real_root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(real_root), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.setattr(server, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(server, "WEB_DIR", tmp_path / "webPage")
    session = server.ScriptProcessSession()
    monkeypatch.setattr(server, "AUTOENV_SESSION", session)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{http.server_address[1]}"

    def request(path, data=None):
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
        req = Request(base + path, data=body, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=5) as response:
            return json.load(response)

    yield tmp_path, request, session
    session.stop()
    http.shutdown()
    http.server_close()
    thread.join(timeout=5)


def _wait_complete(request):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = request("/api/run/events?cursor=0")
        complete = next((item for item in value["events"] if item["type"] == "complete"), None)
        if complete:
            return value, complete
        time.sleep(0.05)
    pytest.fail("CLI subprocess did not finish")


def test_web_catalog_saves_two_level_connections_and_ssh_transfer_type(web_project):
    root, request, _ = web_project
    for config in [
        {"name": "主机", "type": "ssh", "host": "127.0.0.1", "username": "root", "upload_type": "sftp"},
        {"name": "控制台", "type": "telnet", "host": "127.0.0.1", "shell_mode": "prompt_only", "probe_command": "version", "probe_keyword": "VERSION"},
        {"name": "文件服务", "type": "ftp", "host": "127.0.0.1", "username": "user", "passive": False},
    ]:
        assert request("/api/environments", {"topology": "双机", "connection": config})["ok"]
    catalog = request("/api/environments")["topologies"]
    assert catalog["双机"]["主机"]["upload_type"] == "sftp"
    assert catalog["双机"]["控制台"]["probe_keyword"] == "VERSION"
    assert catalog["双机"]["文件服务"]["passive"] is False
    stored = json.loads((root / "environments" / "topologies" / "双机" / "connections.json").read_text(encoding="utf-8"))
    assert {item["name"] for item in stored} == {"主机", "控制台", "文件服务"}
    with pytest.raises(HTTPError) as error:
        request("/api/environments", {"topology": "../escape", "connection": {"name": "bad", "type": "ssh", "host": "127.0.0.1"}})
    assert error.value.code == 400


def test_web_discovers_plain_scripts_without_import_or_decorator(web_project):
    root, request, _ = web_project
    (root / "scripts" / "unconfigured.py").write_text(
        'from autoenv import init_script, get_remote_info\n'
        'def main():\n'
        '    paths = init_script()\n'
        '    dut = get_remote_info("ssh", [("missing", "dut")])\n'
        '    raise AssertionError("must not execute discovery")\n', encoding="utf-8",
    )
    scripts = {item["script"]: item for item in request("/api/scripts")["scripts"]}
    assert scripts["demo"]["arguments"][0]["name"] == "message"
    assert scripts["demo"]["slots"] == []
    assert "unknown allowed environment" in scripts["unconfigured"]["error"]
    assert scripts["unconfigured"]["slots"][0]["role"] == "dut"


def test_web_runs_real_cli_and_streams_operation_output_separately_from_log(web_project):
    root, request, _ = web_project
    data = {"script": "demo", "arguments": {"message": "中文 = hello"}, "bindings": {}, "packages": {}}
    preview = request("/api/run/command", data)["command"]
    assert "autoenv.cli" in preview and "--non-interactive" in preview
    assert "message=中文 = hello" in preview and "adapt_interface" not in preview
    started = request("/api/run/start", data)
    value, complete = _wait_complete(request)
    assert value["generation"] == started["generation"]
    assert complete["exit_code"] == 0 and complete["success"]
    log = Path(complete["log_path"])
    output = Path(complete["output_path"])
    assert log.is_file() and output.is_file() and log.is_absolute()
    assert "LOG_ONLY 原始用户输出" in log.read_text(encoding="utf-8")
    concise = "\n".join(item["text"] for item in value["events"] if item["type"] == "output")
    assert "create_file" in concise and "demo.py:6" in concise
    assert "LOG_ONLY" not in concise
    assert (log.parent / "runtime" / "hello.txt").read_text(encoding="utf-8") == "中文 = hello"
    assert request(f'/api/run/events?cursor={value["next"]}')["events"] == []
    assert log.parent.is_relative_to(root / "logs")


def test_web_cli_preparation_failure_returns_real_log_and_exit_two(web_project):
    _, request, _ = web_project
    request("/api/run/start", {"script": "demo", "arguments": {}})
    _, complete = _wait_complete(request)
    assert complete["exit_code"] == 2 and not complete["success"]
    log = Path(complete["log_path"])
    assert log.is_file()
    assert "message" in log.read_text(encoding="utf-8")
    assert not (log.parent / "runtime").exists()


def test_web_cli_runtime_failure_retains_partial_operation_trace(web_project):
    root, request, _ = web_project
    file = root / "scripts" / "demo.py"
    file.write_text(file.read_text(encoding="utf-8") + '    raise RuntimeError("failed after create")\n', encoding="utf-8")
    request("/api/run/start", {"script": "demo", "arguments": {"message": "retained"}})
    value, complete = _wait_complete(request)
    assert complete["exit_code"] == 1 and not complete["success"]
    log = Path(complete["log_path"])
    assert "failed after create" in log.read_text(encoding="utf-8")
    assert any("create_file" in event.get("text", "") for event in value["events"])


def test_web_complex_arguments_use_canonical_request_without_mixed_flags(web_project):
    root, request, _ = web_project
    file = root / "scripts" / "demo.py"
    file.write_text(file.read_text(encoding="utf-8").replace('"type": "str"', '"type": "list"').replace('paths.arguments["message"])', 'str(paths.arguments["message"]))'), encoding="utf-8")
    started = request("/api/run/start", {"script": "demo", "selected_topology": "", "bindings": {}, "packages": {}, "arguments": {"message": ["中文", 2]}})
    assert "--request" in started["command"] and "--arg" not in started["command"]
    _, complete = _wait_complete(request)
    assert complete["exit_code"] == 0


def test_web_main_page_contains_only_registration_and_script_run():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webPage" / "index.html").read_text(encoding="utf-8")
    assert 'data-page="run"' in html and 'data-page="environments"' in html
    assert 'data-page="tools"' not in html and 'data-page="agent"' not in html
    assert 'name="upload_type"' in html and 'name="passive"' in html
    assert 'id="command"' in html and 'id="output"' in html and 'id="logPath"' in html
