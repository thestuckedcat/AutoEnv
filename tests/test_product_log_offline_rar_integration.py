"""Run the generated HTML's real Worker on ZIP LZMA -> RAR -> product logs."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

import pytest

from product_log.index import iter_export
from product_log.workflow import run_analysis


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required to execute the generated offline Worker")


def _zip(files: dict[str, bytes], method: int) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=method) as archive:
        for path, payload in files.items():
            archive.writestr(path, payload)
    return output.getvalue()


@pytest.fixture(scope="module")
def generated_worker(tmp_path_factory) -> str:
    spec = importlib.util.spec_from_file_location("offline_rar_integration_builder", ROOT / "tools/build_product_log_page.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    output = tmp_path_factory.mktemp("offline-rar-page") / "product-log-offline.html"
    builder.build_page(output)
    html = output.read_text(encoding="utf-8")
    encoded = re.search(r'<script id="offline-worker" type="application/octet-stream">([^<]+)</script>', html)
    assert encoded is not None
    return base64.b64decode(encoded.group(1)).decode("utf-8")


@pytest.mark.parametrize("version", [4, 5])
def test_generated_html_rar_worker_matches_python_full_export(version, generated_worker, tmp_path):
    rar = ROOT / "tests/fixtures/product_log_archives" / f"compressed-rar{version}.rar"
    kernel = b"[Thu Jan 6 03:51:37 2000] <LpoThread_u10_p:25170:28546> [UDK kernel integration]\n"
    inner = _zip({"payload/diag/driver.rar": rar.read_bytes(),
                  "payload/varlog/cpdt_dmesg_current.log.1": kernel}, zipfile.ZIP_LZMA)
    archive = tmp_path / "product.zip"
    archive.write_bytes(_zip({"middle.zip": inner}, zipfile.ZIP_DEFLATED))
    before = hashlib.sha256(archive.read_bytes()).hexdigest()

    harness = r"""
const fs=require('node:fs'),vm=require('node:vm'),crypto=require('node:crypto');
const request=JSON.parse(fs.readFileSync(0,'utf8')),messages=[];
const blocked=()=>{throw new Error('Generated offline HTML attempted network access');};
const context=vm.createContext({Uint8Array,Uint32Array,ArrayBuffer,DataView,TextDecoder,
  Blob,Response,ReadableStream,WebAssembly,atob,btoa,setTimeout,clearTimeout,performance,
  WorkerGlobalScope:function(){},location:{href:'blob:generated-offline-worker'},
  fetch:blocked,XMLHttpRequest:blocked,importScripts:blocked,WebSocket:blocked,
  postMessage:message=>messages.push(message)});
context.self=context;
vm.runInContext(request.code,context,{filename:'generated-rar-html-worker.js'});
let next=0;
async function call(action,payload={}) {
  const id=++next;
  await context.onmessage({data:{id,action,payload}});
  const reply=messages.find(message=>message.id===id);
  if(!reply) throw new Error('Missing Worker response');
  if(reply.error) throw new Error(reply.error);
  return reply.result;
}
(async()=>{
  const bytes=Uint8Array.from(Buffer.from(request.archive,'base64'));
  const before=crypto.createHash('sha256').update(bytes).digest('hex');
  const start=await call('start',{files:[{path:'product.zip',bytes:bytes.buffer}],
    upload_kind:'archive',rules:{collection_mode:'product'}});
  const all=await call('query',{kind:'all',limit:250});
  const mappings=await call('mappings');
  const exported=await call('export',{kind:'all',mode:'concise'});
  process.stdout.write(JSON.stringify({start,total:all.total,records:all.records,
    mappings:mappings.modules,export_sha256:crypto.createHash('sha256').update(exported.text).digest('hex'),
    export_lines:exported.text.split('\n').length-1,
    sorted:all.records.every((row,index)=>row.time_value!=null &&
      (index===0||BigInt(all.records[index-1].time_value)<=BigInt(row.time_value))),
    input_unchanged:crypto.createHash('sha256').update(bytes).digest('hex')===before,
    stages:messages.filter(message=>message.progress).map(message=>message.progress.stage)}));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
"""
    request = {"code": generated_worker, "archive": base64.b64encode(archive.read_bytes()).decode("ascii")}
    completed = subprocess.run([NODE, "-e", harness], input=json.dumps(request), text=True,
                               encoding="utf-8", capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    counts = result["start"]["counts"]
    assert result["start"]["status"] == "ready"
    assert (counts["files"], counts["user_records"], counts["kernel_records"], counts["firmware_records"]) == (2, 64, 1, 0)
    assert [item["format"] for item in result["start"]["archives"]] == ["zip", "zip", "rar"]
    assert result["total"] == len(result["records"]) == result["export_lines"] == 65
    assert result["sorted"] and result["input_unchanged"]
    assert [row["driver_domain"] for row in result["records"]] == ["user"] * 64 + ["kernel"]
    assert result["records"][-1]["time"] == "2000-01-06 03:51:37"
    udk = next(row for row in result["mappings"] if row["module"] == "udk")
    assert (udk["user_count"], udk["kernel_count"]) == (64, 1)
    assert "extracting" in result["stages"] and "parsing" in result["stages"]

    batch = tmp_path / "python-comparison"
    run_analysis(archive, batch, collection_mode="product")
    exported = "".join(iter_export(batch, kind="all", mode="concise"))
    assert result["export_sha256"] == hashlib.sha256(exported.encode("utf-8")).hexdigest()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == before
