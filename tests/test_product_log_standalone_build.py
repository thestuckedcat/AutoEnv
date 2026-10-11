"""Verify the generated single HTML, including its actual embedded worker."""
from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from product_log.index import iter_export
from product_log.workflow import run_analysis
from product_log_domain_sample import build_domain_archive


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "tools" / "build_product_log_page.py"
NODE = shutil.which("node")


def builder_module():
    spec = importlib.util.spec_from_file_location("offline_builder_test", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = []
        self.scripts = []
        self.external = []
        self.current = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag in {"script", "link", "img", "iframe", "audio", "video", "source", "object", "embed"}:
            self.external.extend((tag, key, attrs[key]) for key in ("src", "href", "data") if key in attrs)
        if tag == "script":
            self.current = {"attrs": attrs, "text": ""}
            self.scripts.append(self.current)

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    folder = tmp_path_factory.mktemp("offline-html")
    output = folder / "product-log-offline.html"
    module = builder_module()
    metadata = module.build_page(output)
    text = output.read_text(encoding="utf-8")
    return module, output, text, metadata, Page(text)


def test_build_is_deterministic_and_does_not_change_existing_code(generated, tmp_path):
    module, output, text, metadata, _ = generated
    originals = [ROOT / name for name in (
        "autoenv/file_handles.py", "autoenv/local_archives.py", "startWeb.py",
        "webPage/server.py", "product_log/parser.py", "product_log/workflow.py",
        "product_log/index.py", "product_log/web.py", "product_log/static/app.js")]
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in originals}
    second = tmp_path / "second.html"
    module.build_page(second)
    assert second.read_bytes() == output.read_bytes()
    assert before == {path: hashlib.sha256(path.read_bytes()).digest() for path in originals}
    assert metadata["bytes"] == len(text.encode("utf-8"))
    assert metadata["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_cli_build_works_with_no_site_packages(tmp_path):
    output = tmp_path / "目录 with spaces" / "standalone.html"
    result = subprocess.run([sys.executable, "-I", "-S", "-X", "utf8", str(BUILDER),
                             "--output", str(output)], cwd=tmp_path, capture_output=True,
                            text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert Path(metadata["output"]) == output
    assert metadata["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_every_runtime_asset_is_embedded_and_dom_references_exist(generated):
    module, _, text, _, page = generated
    assert not page.external
    assert len(page.ids) == len(set(page.ids))
    assert "<!--OFFLINE_" not in text
    assert len(page.scripts) == 2
    worker = next(script for script in page.scripts if script["attrs"].get("id") == "offline-worker")
    assert worker["attrs"]["type"] == "application/octet-stream"
    code = base64.b64decode(worker["text"], validate=True).decode("utf-8")
    assert code == module.worker_source()
    assert "解析用户态、内核态与固件日志" in code
    application = next(script["text"] for script in page.scripts if "id" not in script["attrs"])
    assert set(re.findall(r"\$\(['\"]([^'\"]+)['\"]\)", application)) <= set(page.ids)
    for filename in ("app.js", "archive.js", "parser.js", "worker.js"):
        javascript = (module.SOURCE / filename).read_text(encoding="utf-8")
        assert not re.search(r"\b(?:fetch|importScripts)\s*\(|\b(?:XMLHttpRequest|WebSocket)\b", javascript)
    css = (module.SOURCE / "styles.css").read_text(encoding="utf-8")
    assert not re.search(r"@import|url\(", css)
    for license_name in ("fflate", "seek-bzip", "xz-decompress", "Steve Sanderson", "Igalia"):
        assert license_name in text


def test_builder_rejects_changed_vendor_asset(tmp_path, monkeypatch):
    module = builder_module()
    source = tmp_path / "source"
    shutil.copytree(module.SOURCE, source)
    monkeypatch.setattr(module, "SOURCE", source)
    asset = source / "vendor" / "fflate-0.8.3.js"
    asset.write_bytes(asset.read_bytes() + b"\nchanged")
    output = tmp_path / "out.html"
    with pytest.raises(ValueError, match="checksum differs"):
        module.build_page(output)
    assert not output.exists()


@pytest.mark.parametrize("filename", ["product_log/standalone/generated.html", "dist/../product_log/standalone/generated.html"])
def test_builder_rejects_output_over_source(generated, filename):
    module, *_ = generated
    with pytest.raises(ValueError, match="outside"):
        module.build_page(ROOT / filename)
    assert not (module.SOURCE / "generated.html").exists()


@pytest.mark.skipif(NODE is None, reason="Node is required to execute JavaScript highlighting")
def test_unicode_highlights_keep_original_characters_and_match_parser_folding():
    application = (ROOT / "product_log/standalone/app.js").read_text(encoding="utf-8")
    parser = (ROOT / "product_log/standalone/parser.js").read_text(encoding="utf-8")
    ui_table = json.loads(re.search(r"const HIGHLIGHT_FOLD = (\{[^\n]+\});", application).group(1))
    parser_table = json.loads(re.search(r"const FULL_FOLD = (\{[^\n]+\});", parser).group(1))
    assert ui_table == parser_table
    functions = application[application.index("const HIGHLIGHT_FOLD"):application.index("function highlighted(")]
    cases = [["Straße", "ss", False], ["Straße", "SS", True], ["ﬃ Straße", "ffi", False],
             ["İ", "i", False], ["😀Straße😀", "SS", False], ["Σςσ", "σ", False]]
    harness = "const fs=require('node:fs'),vm=require('node:vm');const input=JSON.parse(fs.readFileSync(0,'utf8'));" \
              "process.stdout.write(vm.runInNewContext(input.code + '\\nJSON.stringify(cases.map(([text,keyword,sensitive]) => highlightRanges(text,keyword,sensitive).map(range => text.slice(range.start,range.end))))', {cases:input.cases}));"
    result = subprocess.run([NODE, "-e", harness], input=json.dumps({"code": functions, "cases": cases}),
                            capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [["ß"], [], ["ﬃ"], ["İ"], ["ß"], ["Σςσ"]]


@pytest.fixture(scope="module")
def integrated(generated, tmp_path_factory):
    if NODE is None:
        pytest.skip("Node is required for executing the embedded browser worker")
    _, _, _, _, page = generated
    worker = next(script for script in page.scripts if script["attrs"].get("id") == "offline-worker")
    code = base64.b64decode(worker["text"]).decode("utf-8")
    folder = tmp_path_factory.mktemp("offline-integrated")
    archive = folder / "product.tar.gz"
    build_domain_archive(archive)
    harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const input=JSON.parse(fs.readFileSync(0,'utf8')),messages=[];
const blocked=()=>{throw new Error('Network access is forbidden in the offline test');};
const context=vm.createContext({Uint8Array,Uint32Array,ArrayBuffer,DataView,TextDecoder,
  Blob,Response,ReadableStream,WebAssembly,atob,btoa,setTimeout,clearTimeout,performance,
  fetch:blocked,XMLHttpRequest:blocked,importScripts:blocked,WebSocket:blocked,
  postMessage:message=>messages.push(message)});
vm.runInContext(input.code,context,{filename:'generated-html-worker.js'});
let id=0;
async function call(action,payload={}) {
  const current=++id;
  await context.onmessage({data:{id:current,action,payload}});
  const response=messages.find(message=>message.id===current);
  if (!response) throw new Error('Missing worker response');
  return response;
}
async function ok(action,payload) {
  const response=await call(action,payload);
  if(response.error) throw new Error(response.error);
  return response.result;
}
(async()=>{
  const before=await call('query',{kind:'all'});
  const bytes=Uint8Array.from(Buffer.from(input.archive,'base64'));
  const start=await ok('start',{files:[{path:'product.tar.gz',bytes:bytes.buffer}],
    upload_kind:'archive',rules:{collection_mode:'product',diag_directory_pattern:'diag/slot62',
    file_patterns:'',archive_patterns:'',copy_patterns:''}});
  const mappings=await ok('mappings'),groups=await ok('groups',{kind:'firmware'});
  const all=await ok('query',{kind:'all',limit:500});
  while(all.records.length<all.total) {
    const page=await ok('query',{kind:'all',offset:all.records.length,limit:500});
    all.records.push(...page.records);
  }
  const dated=all.records.filter(row=>row.time_value!=null);
  const unknown=all.records.filter(row=>row.time_value==null);
  const kernel=await ok('query',{kind:'driver',driver_domain:'kernel',module:'udk',limit:200});
  const source=await ok('source',{source_id:kernel.records[0].source_id,
    line_number:kernel.records[0].line_number,context_lines:2});
  const saved=await ok('mappings',{modules:[
    {module:'udk',user_module_name:'udk',kernel_keyword:'udk'},
    {module:'UBE',user_module_name:'UBE',kernel_keyword:'mami'},
    {module:'MEME',user_module_name:'MEME',kernel_keyword:'MEME'},
    {module:'missing',user_module_name:'absent',kernel_keyword:'mami'}]});
  const meme=await ok('query',{kind:'driver',module:'MEME'});
  const missing=await ok('query',{kind:'driver',module:'missing'});
  const exportAll=await ok('export',{kind:'all',mode:'concise'});
  const exportFiltered=await ok('export',{kind:'driver',module:'MEME',keyword:'seq=00'});
  const invalid=await call('not-supported');
  process.stdout.write(JSON.stringify({before,start,mappings:mappings.modules,groups:groups.groups,
    total:all.total,unknown:unknown.map(row=>({time:row.time,domain:row.driver_domain,text:row.text})),
    sorted:dated.every((row,index)=>index===0||BigInt(dated[index-1].time_value)<=BigInt(row.time_value)),
    unknown_at_end:all.records.slice(-unknown.length).every(row=>row.time_value==null),
    kernel_count:kernel.total,kernel:kernel.records[0],source,saved:saved.modules,meme:meme.total,
    missing:missing.total,export_hash:crypto.createHash('sha256').update(exportAll.text).digest('hex'),
    export_lines:exportAll.text.split('\n').length-1,filtered_text:exportFiltered.text,invalid,
    stages:messages.filter(row=>row.progress).map(row=>row.progress.stage)}));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
"""
    completed = subprocess.run([NODE, "-e", harness], input=json.dumps({"code": code,
        "archive": base64.b64encode(archive.read_bytes()).decode("ascii")}), text=True,
        encoding="utf-8", capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout), folder, archive


def test_generated_html_worker_parses_real_nested_sample_and_mapping(integrated):
    result, *_ = integrated
    assert result["before"]["error"] == "请先解析日志"
    assert result["start"]["status"] == "ready"
    counts = result["start"]["counts"]
    assert (counts["files"], counts["user_records"], counts["kernel_records"], counts["firmware_records"]) == (11, 2003, 408, 3600)
    assert len(result["start"]["archives"]) == 12
    assert result["total"] == result["export_lines"] == 6011
    assert result["sorted"] and result["unknown_at_end"]
    assert len(result["unknown"]) == 8
    assert all(row["domain"] == "kernel" and "时间未知" in row["text"] for row in result["unknown"])
    assert len(result["groups"]) == 30
    assert all(row["count"] == 120 for row in result["groups"])
    assert {row["log_type_name"] for row in result["groups"]} == {"bios", "IMP", "IMU", "HSM", "4"}
    initial = {row["module"]: row for row in result["mappings"]}
    assert (initial["udk"]["user_count"], initial["udk"]["kernel_count"]) == (500, 104)
    assert initial["UBE"]["kernel_count"] == 100
    assert result["kernel_count"] == 104
    assert not result["kernel"]["slot_id"] and not result["kernel"]["cpu_id"]
    assert "cpdt_dmesg_current.log" in result["source"]["source_path"]
    assert result["source"]["lines"] and "bytes" not in result["source"]["source"]
    saved = {row["module"]: row for row in result["saved"]}
    assert not saved["missing"]["user_exists"]
    assert result["meme"] == 600 and result["missing"] == 100
    assert "MEME" in result["filtered_text"] and "HBOOT" not in result["filtered_text"]
    assert "Unknown offline action" in result["invalid"]["error"]
    assert "extracting" in result["stages"] and "parsing" in result["stages"]


def test_generated_html_full_export_equals_existing_python(integrated):
    result, folder, archive = integrated
    batch = folder / "python-comparison"
    run_analysis(archive, batch, collection_mode="product", diag_directory_pattern="diag/slot62")
    export = "".join(iter_export(batch, kind="all", mode="concise"))
    assert result["export_hash"] == hashlib.sha256(export.encode("utf-8")).hexdigest()
