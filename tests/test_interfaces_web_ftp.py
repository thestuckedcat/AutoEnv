from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import types
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from autoenv.ftp_host import FTPConnectionInfo, FTPHost
from autoenv.interface import LaunchRequest, bind_environments, merge_parameters
from autoenv.resources import (
    RESOURCE_LABELS_PATH,
    describe_resource_labels,
    load_resource_labels,
    validate_resource_label,
)
from autoenv.results import RemoteDownloadResult
from autoenv.selectors import extra_file, resolve_local_file
from autoenv.web_tools import describe_tools, run_web_tool


class Recorder:
    def __init__(self): self.index=0; self.results=[]
    def next_operation_id(self): self.index+=1; return f"{self.index:04d}"
    def record_result(self, name, result): self.results.append((name,result))


class FakeFTP:
    def __init__(self): self.files={}; self.dirs=set(); self.passive=None
    def connect(self,*args,**kwargs): self.connect_args=(args,kwargs)
    def login(self,*args): self.login_args=args
    def set_pasv(self,value): self.passive=value
    def mkd(self,path): self.dirs.add(path)
    def size(self,path):
        if path not in self.files: raise __import__("ftplib").error_perm("550 missing")
        return len(self.files[path])
    def storbinary(self,command,handle): self.files[command.split(" ",1)[1]]=handle.read()
    def quit(self): pass


def test_remote_download_result_is_directly_uploadable_selector(tmp_path: Path):
    package_dir=tmp_path/"packages";package_dir.mkdir();local=package_dir/"matched.zip";local.write_bytes(b"zip")
    from datetime import datetime, timezone
    now=datetime.now(timezone.utc)
    result=RemoteDownloadResult("run","1","sftp","dut","/logs",None,r".*zip",True,"success",True,now,now,0,"/logs/matched.zip",3,str(local),3,False,"abc",True)
    resolved=resolve_local_file(result,package_dir,lambda _:"")
    assert resolved.path==local.resolve() and resolved.selector_type=="remote_download"


def test_plain_ftp_upload_uses_independent_connection_and_size_check(tmp_path: Path):
    package_dir=tmp_path/"packages";package_dir.mkdir();(package_dir/"data.bin").write_bytes(b"data")
    fake=FakeFTP(); recorder=Recorder()
    host=FTPHost(name="ftp",info=FTPConnectionInfo("127.0.0.1"),run_id="run",package_dir=package_dir,recorder=recorder,image_pattern_for=lambda _:"",ftp_factory=lambda:fake)
    result=host.upload(extra_file("data.bin"),"/incoming")
    assert result.success and result.protocol=="ftp" and result.size_verified
    assert fake.files["/incoming/data.bin"]==b"data" and fake.passive is True


def test_launch_request_and_environment_override_merge():
    request=LaunchRequest.from_dict({"script":"demo","environment":"lab","parameters":{"ssh_hosts":{"dut":{"host":"new"}}}})
    merged=merge_parameters({"ssh_hosts":{"dut":{"host":"old","port":22}}},request.parameters or {})
    assert merged["ssh_hosts"]=={"dut":{"host":"new","port":22}}
    with pytest.raises(ValueError): LaunchRequest.from_dict({"script":"demo","mode":"bad"})


def test_multi_environment_resource_bindings_are_independent_from_packages(tmp_path: Path):
    environments = tmp_path / "environments"
    environments.mkdir()
    (environments / "rack-a.json").write_text(
        '{"ssh_hosts":{"host":{"host":"192.0.2.10","port":22,"resource_label":"1260网口"}}}',
        encoding="utf-8",
    )
    (environments / "rack-b.json").write_text(
        '{"telnet_connections":{"console":{"host":"192.0.2.20","port":23,"resource_label":"1712串口"}}}',
        encoding="utf-8",
    )
    resources = (
        {"name": "primary", "label": "1260网口", "protocol": "ssh"},
        {"name": "secondary_console", "label": "1712串口", "protocol": "telnet"},
    )
    bound = bind_environments(
        tmp_path,
        {
            "primary": {"environment": "rack-a"},
            "secondary_console": {"environment": "rack-b"},
        },
        resources,
    )
    merged = merge_parameters(
        bound,
        {
            "packages": {
                "A1": {"path_override": "/hdfs/build/a1"},
                "A2": {"path_override": "/hdfs/build/a2"},
            }
        },
    )
    assert merged["ssh_hosts"]["primary"]["host"] == "192.0.2.10"
    assert merged["telnet_connections"]["secondary_console"]["host"] == "192.0.2.20"
    assert set(merged["packages"]) == {"A1", "A2"}


def test_resource_labels_are_loaded_from_json_and_protocol_checked():
    labels = {item["label"] for item in describe_resource_labels()}
    assert labels == {"1260网口", "1260串口", "1712网口", "1712串口", "udie1网口", "udie1串口"}
    assert RESOURCE_LABELS_PATH.name == "resource_labels.json"
    assert validate_resource_label("1260网口", protocol="ssh") == "1260网口"
    with pytest.raises(ValueError, match="not valid for telnet"):
        validate_resource_label("1260网口", protocol="telnet")


@pytest.mark.parametrize(
    ("catalog", "message"),
    [
        ({"schema_version": 2, "labels": [{"label": "dut", "kind": "network"}]}, "schema_version"),
        ({"schema_version": 1, "labels": [{"label": "dut", "kind": "other"}]}, "network or serial"),
        (
            {
                "schema_version": 1,
                "labels": [
                    {"label": "dut", "kind": "network"},
                    {"label": "dut", "kind": "serial"},
                ],
            },
            "duplicate label",
        ),
    ],
)
def test_resource_label_catalog_rejects_invalid_data(
    tmp_path: Path, catalog: dict[str, object], message: str
):
    path = tmp_path / "resource_labels.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_resource_labels(path)


def test_resource_label_api_returns_json_catalog():
    from webPage.server import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/api/resource-labels",
            timeout=5,
        ) as response:
            status = response.status
            payload = json.load(response)
        assert status == 200
        assert len(payload["resource_labels"]) == 6
        assert {item["kind"] for item in payload["resource_labels"]} == {
            "network",
            "serial",
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_start_web_script_api_returns_registered_scripts():
    from webPage.server import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_address[1]}/api/scripts",
            timeout=5,
        ) as response:
            payload = json.load(response)
        names = {item["name"] for item in payload["scripts"]}
        assert "example_host_environment" in names
        assert "template_combined" in names
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_web_environment_validation_requires_unique_known_labels():
    from webPage.server import _validate_environment

    valid = _validate_environment({
        "name": "lab-a",
        "ssh_hosts": {
            "dut": {"host": "192.0.2.1", "resource_label": "1260网口"}
        },
        "telnet_connections": {
            "console": {"host": "192.0.2.2", "resource_label": "1260串口"}
        },
    })
    assert valid["ssh_hosts"]["dut"]["resource_label"] == "1260网口"
    with pytest.raises(ValueError, match="must be unique"):
        _validate_environment({
            "name": "lab-a",
            "ssh_hosts": {
                "first": {"host": "192.0.2.1", "resource_label": "1260网口"},
                "second": {"host": "192.0.2.2", "resource_label": "1260网口"},
            },
        })


def test_script_api_describes_connection_and_hdfs_prompts():
    from webPage.server import _describe_scripts

    scripts = _describe_scripts()
    example = next(item for item in scripts if item["name"] == "example_host_environment")
    assert example["resources"] == [{
        "name": "example_host",
        "alias": "1260 管理网口",
        "description": "用于上传安装包、执行安装命令并检查 READY 状态。",
        "label": "1260网口",
        "protocol": "ssh",
    }]
    assert example["package_inputs"] == [{
        "name": "A1",
        "alias": "A1 主安装包",
        "description": "从 HDFS 下载并上传到 1260 主机的安装包。",
    }]


def test_template_script_describes_all_web_input_types():
    from webPage.server import _describe_scripts

    scripts = _describe_scripts()
    template = next(item for item in scripts if item["name"] == "template_host_and_transfer")
    assert template["package_inputs"] == [{
        "name": "A1",
        "alias": "A1 主安装包",
        "description": "示例主安装包；留空链接时使用 config.json 的 link/base_link。",
    }]
    assert template["parameters"] == [{
        "name": "release_channel",
        "type": "string",
        "label": "发布通道",
        "placeholder": "例如 debug 或 release",
        "required": True,
    }]
    assert [(item["name"], item["label"], item["protocol"]) for item in template["resources"]] == [
        ("template_ssh", "1260网口", "ssh"),
        ("template_ftp", "1712网口", "ftp"),
    ]

    combined = next(item for item in scripts if item["name"] == "template_combined")
    assert combined["package_inputs"] == template["package_inputs"]
    assert combined["parameters"] == template["parameters"]
    assert [item["name"] for item in combined["resources"]] == [
        "template_ssh",
        "template_ftp",
        "template_console_ssh",
        "template_console",
    ]


def test_agent_terminal_uses_pty_chunks_and_preserves_control_sequences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from webPage.server import TerminalSession

    class FakePty:
        def __init__(self):
            self.chunks = iter(["loading 1%\rloading 100%", "\x1b[2Jready", None])
            self.writes = []
            self.exitstatus = 0

        def read(self, _size):
            value = next(self.chunks)
            if value is None:
                raise EOFError
            return value

        def wait(self): return 0
        def isalive(self): return True
        def write(self, value): self.writes.append(value)
        def setwinsize(self, rows, cols): self.size = (rows, cols)
        def terminate(self, force=False): self.terminated = force

    process = FakePty()
    class FakeProcessFactory:
        @staticmethod
        def spawn(command, **kwargs):
            process.command = command
            process.options = kwargs
            return process

    monkeypatch.setitem(sys.modules, "winpty", types.SimpleNamespace(PtyProcess=FakeProcessFactory))
    session = TerminalSession()
    generation = session.start(["agent.exe"], cwd=tmp_path, rows=40, cols=120)
    for _ in range(100):
        with session.lock:
            if any(event["type"] == "complete" for event in session.events):
                break
        time.sleep(0.01)
    with session.lock:
        terminal_data = "".join(
            str(event["data"]) for event in session.events if event["type"] == "terminal"
        )
    assert terminal_data == "loading 1%\rloading 100%\x1b[2Jready"
    assert generation == 1
    assert process.options["dimensions"] == (40, 120)
    assert process.options["cwd"] == str(tmp_path)
    session.input("hello\r")
    assert process.writes == ["hello\r"]


def test_agent_startup_directory_must_be_an_existing_directory(tmp_path: Path):
    from webPage.server import _resolve_agent_cwd

    assert _resolve_agent_cwd(tmp_path) == tmp_path.resolve()
    with pytest.raises(ValueError, match="startup directory does not exist"):
        _resolve_agent_cwd(tmp_path / "missing")


def test_agent_command_is_typed_into_cmd_instead_of_prevalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from webPage.server import _start_agent_shell

    class FakeSession:
        def start(self, command, **options):
            self.command = command
            self.options = options
            return 7

        def input(self, value):
            self.value = value

    session = FakeSession()
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    generation = _start_agent_shell(
        session, "missing-agent --verbose", cwd=tmp_path, rows=24, cols=115
    )

    assert session.command == [r"C:\Windows\System32\cmd.exe", "/d"]
    assert session.options == {"cwd": tmp_path, "rows": 24, "cols": 115}
    assert session.value == "missing-agent --verbose\r"
    assert generation == 7


def test_empty_agent_command_opens_cmd_without_typing(tmp_path: Path):
    from webPage.server import _start_agent_shell

    class FakeSession:
        def start(self, command, **options):
            self.command = command
            return 8

        def input(self, _value):
            raise AssertionError("an empty startup command must not write terminal input")

    session = FakeSession()
    _start_agent_shell(session, "", cwd=tmp_path, rows=30, cols=100)
    assert session.command[-1] == "/d"


def test_environment_reload_refreshes_existing_workflow_tool_choices():
    root = Path(__file__).resolve().parents[1]
    javascript = (root / "webPage" / "app.js").read_text(encoding="utf-8")

    assert 'loadEnvs(){state.envs=(await api("/api/environments")).environments;refreshEnvironmentConsumers()}' in javascript
    assert "function refreshToolResourceChoices()" in javascript
    assert 'element.dataset.toolResource===resource.name' in javascript
    assert 'choices.some(choice=>choice.environment===selected)' in javascript


def test_log_query_uses_css_selectors_and_ignores_replaced_panels():
    root = Path(__file__).resolve().parents[1]
    javascript = (root / "webPage" / "app.js").read_text(encoding="utf-8")

    assert 'document.querySelector(`[data-prev-page="${index}"]`)' in javascript
    assert 'document.querySelector(`[data-next-page="${index}"]`)' in javascript
    assert '$(`[data-prev-page="${index}"]`)' not in javascript
    assert '$(`[data-next-page="${index}"]`)' not in javascript
    assert "if(!container||!pageLabel||!previous||!next)return" in javascript
    assert "state.logQueryTokens[index]===token&&container" in javascript


def test_agent_page_types_and_drops_files_directly_in_terminal():
    root = Path(__file__).resolve().parents[1]
    html = (root / "webPage" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "webPage" / "app.js").read_text(encoding="utf-8")

    assert 'id="agentInput"' not in html
    assert 'id="agentInputForm"' not in html
    assert 'id="agentConsole" tabindex="0" role="textbox"' in html
    assert "app.js?v=20260817-log-find" in html
    assert "terminal.onpaste" in javascript
    assert 'terminal.addEventListener("drop"' in javascript
    assert "sendAgentInput(value)" in javascript
    assert 'cwd:$("agentCwd").value' in javascript
    assert "agentPollGeneration" in javascript
    assert "agentSessionGeneration" in javascript
    assert "refreshResourceLabelSelects" in javascript
    assert 'await loadResourceLabels();blankEnv();await Promise.all' in javascript
    assert 'f.elements.namedItem("name")' in javascript
    assert 'f.elements.namedItem("title")' in javascript
    assert '保存失败：${error.message}' in javascript
    assert 'post("/api/tools/workflow/start"' in javascript
    assert 'api("/api/log-batches")' in javascript
    assert "distance<=300" in javascript
    assert "data-pane-find" in javascript
    assert "markKeyword" in javascript
    assert "keyword:state.logFinds[index]" in javascript
    server = (root / "webPage" / "server.py").read_text(encoding="utf-8")
    assert "shutil.which" not in server
    assert 'self.send_header("Cache-Control", "no-store")' in server
    assert 'parsed.path == "/api/log-batches/query"' in server
    assert 'self.path == "/api/tools/workflow/stop"' in server
    assert "class ExclusiveThreadingHTTPServer" in server


def test_web_has_one_launcher_fixed_endpoint_and_no_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    root = Path(__file__).resolve().parents[1]
    start_source = (root / "startWeb.py").read_text(encoding="utf-8")
    server_source = (root / "webPage" / "server.py").read_text(encoding="utf-8")

    assert not (root / "frontend").exists()
    assert "from webPage.server import WEB_URL, main" in start_source
    assert "len(sys.argv) != 1" in start_source
    assert "--host" not in server_source
    assert "--port" not in server_source
    assert "--no-browser" not in server_source
    assert 'if __name__ == "__main__"' not in server_source
    assert server_source.count("8765") == 1

    server_implementations = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if relative.parts[0] in {".git", ".agents", "build", "tests"}:
            continue
        if "serve_forever(" in path.read_text(encoding="utf-8", errors="ignore"):
            server_implementations.append(relative.as_posix())
    assert server_implementations == ["webPage/server.py"]

    rejected = subprocess.run(
        [sys.executable, "-X", "utf8", str(root / "startWeb.py"), "--port", "9999"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert rejected.returncode == 2
    assert "one fixed startup endpoint" in rejected.stderr

    from webPage import server

    observed: dict[str, object] = {}

    class FakeServer:
        def __init__(self, address, handler):
            observed["address"] = address
            observed["handler"] = handler

        def serve_forever(self):
            observed["served"] = True

        def server_close(self):
            observed["closed"] = True

    class FakeTimer:
        def __init__(self, delay, callback):
            observed["timer_delay"] = delay
            observed["browser_callback"] = callback

        def start(self):
            observed["timer_started"] = True

    monkeypatch.setattr(server, "ExclusiveThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(server.threading, "Timer", FakeTimer)

    assert server.main() == 0
    assert observed["address"] == ("127.0.0.1", 8765)
    assert observed["served"] is True
    assert observed["closed"] is True
    assert observed["timer_started"] is True

    attempts: list[tuple[str, int]] = []

    def fail_to_bind(address, _handler):
        attempts.append(address)
        raise OSError("address already in use")

    monkeypatch.setattr(server, "ExclusiveThreadingHTTPServer", fail_to_bind)
    assert server.main() == 2
    assert attempts == [("127.0.0.1", 8765)]
    assert "fixed endpoint" in capsys.readouterr().err


def test_web_tool_discovery_and_execution():
    root=Path(__file__).resolve().parents[1]
    tools=describe_tools(root)
    assert any(item["name"]=="tool-contract-preview" for item in tools)
    result=run_web_tool(root,"tool-contract-preview",{"value":"0x1"})
    assert result["status"]=="contract_ready"


def test_import_skill_safe_extract_rejects_traversal(tmp_path: Path):
    archive=tmp_path/"bad.zip"
    with zipfile.ZipFile(archive,"w") as value: value.writestr("../bad.py","x=1")
    script=Path(__file__).resolve().parents[1]/".agents/skills/import-python-web-tool/scripts/safe_extract.py"
    import subprocess, sys
    completed=subprocess.run([sys.executable,str(script),str(archive),str(tmp_path/"out")],capture_output=True,text=True)
    assert completed.returncode != 0 and "unsafe archive path" in completed.stderr
