"""Loopback HTTP coverage using synthetic logs; no device/session traffic."""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest


def _driver(time_value: str, message: str, slot: int = 62) -> str:
    return (f"2000-01-06 03:51:{time_value} C01-A06-sfu2 %%01UBFWD/7/UBSDK_DIAG(D):"
            f"Service=fwmd0;UB Forward SDK module. (SlotId={slot}, CpuId=0, ModuleName=FWD_SDK, Info=<28028:28041> {message})\n")


def _segment(time_value: str, seg: int, message: str) -> str:
    header = _driver(time_value, f"[INFO][HAL_UB]Unit[4] firmware logType[1] seg[{seg}]:")
    return header[:-2] + f"\n[87527.625][0-0][IMP-EVENT] {message}\n"


@pytest.fixture
def product_http(tmp_path, monkeypatch):
    from product_log import web
    from webPage import server

    root = tmp_path / "project"
    (root / "webPage").mkdir(parents=True)
    monkeypatch.setattr(server, "ROOT_DIR", root)
    monkeypatch.setattr(server, "WEB_DIR", root / "webPage")
    session = server.ScriptProcessSession()
    monkeypatch.setattr(server, "AUTOENV_SESSION", session)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{http.server_address[1]}"

    def request(path, data=None, *, raw=False):
        if isinstance(data, bytes):
            payload, content_type = data, "application/octet-stream"
        else:
            payload = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        with urlopen(Request(base + path, data=payload, headers={"Content-Type": content_type}), timeout=10) as response:
            return response.read().decode("utf-8") if raw else json.load(response)

    yield root, request, web.manager(root), session
    with web.manager(root).lock:
        for task in web.manager(root).tasks.values():
            task.cancel.set()
    session.stop()
    http.shutdown()
    http.server_close()
    thread.join(timeout=5)


def _completed(request, batch_id):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        value = request("/api/product-logs/events?" + urlencode({"batch_id": batch_id, "cursor": 0}))
        if value["status"] in {"ready", "failed", "cancelled"}:
            return value
        time.sleep(0.02)
    pytest.fail("synthetic local product-log task did not finish")


def _zip_logs() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diag/slot62/a.log", _segment("36.383", 0, "跨文件 alpha") + _driver("38.123", "较晚驱动"))
        archive.writestr("diag/slot62/b.log", _segment("37.383", 1, "跨文件 beta") + _driver("35.123", "较早驱动"))
        archive.writestr("other/slot62/ignore.log", _driver("30.000", "其他目录"))
    return output.getvalue()


def test_http_default_kernel_directory_prefers_varlog_to_legacy_var(product_http):
    root, request, _manager, _session = product_http
    source = root / "input"
    for directory, message in (("varlog", "correct directory"), ("var", "legacy directory")):
        target = source / directory / "cpdt_dmesg_current.log"
        target.parent.mkdir(parents=True)
        target.write_text(f"[Thu Jan 6 04:36:49 2000] <thread:1:2> udk {message}\n", encoding="utf-8")
    started = request("/api/product-logs/start", {"input_path": str(source)})
    completed = _completed(request, started["batch_id"])
    assert completed["status"] == "ready", completed
    response = request("/api/product-logs/query?" + urlencode({
        "batch_id": started["batch_id"], "kind": "driver", "log_domain": "kernel"}))
    assert len(response["records"]) == 1
    assert "correct directory" in response["records"][0]["text"]


def test_chunked_archive_real_workflow_query_source_export_and_isolation(product_http):
    root, request, manager, script_session = product_http
    original_events = list(script_session.events)
    payload = _zip_logs()
    created = request("/api/product-logs/uploads/create", {"kind": "archive", "files": [{"path": "样例.zip", "size": len(payload)}]})
    upload_id = created["upload_id"]
    # Intentionally tiny HTTP chunks exercise boundaries and Unicode filenames.
    for offset in range(0, len(payload), 31):
        result = request("/api/product-logs/uploads/chunk?" + urlencode({"upload_id": upload_id, "path": "样例.zip", "offset": offset}), payload[offset:offset + 31])
        assert result["offset"] == min(offset + 31, len(payload))
    assert request("/api/product-logs/uploads/finish", {"upload_id": upload_id})["ok"]
    started = request("/api/product-logs/start", {"upload_id": upload_id, "directory_pattern": "diag/slot62", "file_patterns": ["*.log"], "workers": 2})
    batch = started["batch_id"]
    completion = _completed(request, batch)
    assert completion["status"] == "ready", completion
    manifest = completion["manifest"]
    assert manifest["counts"]["files"] == 2
    assert manifest["counts"]["firmware_records"] == 2
    assert manifest["counts"]["driver_records"] == 2
    assert Path(manifest["product_log_path"]).is_absolute()
    group = request("/api/product-logs/groups?" + urlencode({"batch_id": batch, "kind": "firmware"}))["groups"][0]
    assert group["slot_id"] == "62"
    assert group["dump_count"] == 1
    query = {"batch_id": batch, "kind": "firmware", "group_id": group["group_id"], "view": "stitched", "limit": 1}
    first = request("/api/product-logs/query?" + urlencode(query))
    assert first["total"] == 2 and first["has_more"]
    assert "[2000-01-06 03:51:36.383][87527.625]" in first["records"][0]["text"]
    second = request("/api/product-logs/query?" + urlencode({**query, "offset": 1}))
    assert "beta" in second["records"][0]["text"]
    assert first["records"][0]["dump_id"] == second["records"][0]["dump_id"]
    drivers = request("/api/product-logs/query?" + urlencode({"batch_id": batch, "kind": "driver"}))
    assert [row["time"] for row in drivers["records"]] == sorted(row["time"] for row in drivers["records"])
    assert "SlotId" not in drivers["records"][0]["text"]
    original = request("/api/product-logs/query?" + urlencode({"batch_id": batch, "kind": "driver", "mode": "raw"}))
    assert "SlotId=62" in original["records"][0]["text"]
    filtered = request("/api/product-logs/query?" + urlencode({"batch_id": batch, "kind": "driver", "keyword": "较早"}))
    assert filtered["total"] == 1
    source = request("/api/product-logs/source?" + urlencode({"batch_id": batch, "source_id": filtered["records"][0]["source_id"], "limit": 3}))
    assert "跨文件 beta" in "\n".join(line["text"] for line in source["lines"])
    export = request("/api/product-logs/export?" + urlencode({"batch_id": batch, "kind": "driver", "keyword": "较早", "mode": "raw"}), raw=True)
    assert "SlotId=62" in export and "较晚" not in export
    assert script_session.events == original_events and script_session.process is None
    # Completed batches survive manager recreation and do not share IDs/data.
    history = type(manager)(manager.root)
    assert history.task(batch, ready=True).manifest["counts"]["files"] == 2
    with pytest.raises(HTTPError) as reused:
        request("/api/product-logs/start", {"upload_id": upload_id})
    assert reused.value.code == 400


def test_local_directory_import_preserves_source_and_independent_batches(product_http, tmp_path):
    _, request, _, _ = product_http
    folder = tmp_path / "original"
    folder.mkdir()
    original = _driver("36.383", "原始输入")
    (folder / "a.log").write_text(original, encoding="utf-8")
    batches = []
    for _ in range(2):
        result = request("/api/product-logs/start", {"input_path": str(folder), "file_patterns": "*.log"})
        batches.append(result["batch_id"])
        assert _completed(request, result["batch_id"])["status"] == "ready"
    assert batches[0] != batches[1]
    assert (folder / "a.log").read_text(encoding="utf-8") == original
    assert len(request("/api/product-logs/batches")["batches"]) == 2


def test_browser_directory_upload_preserves_nested_relative_paths(product_http):
    _, request, _, _ = product_http
    files = {"selected/diag/slot62/a.log": _driver("36.383", "目录上传").encode("utf-8"), "selected/diag/slot62/empty.txt": b""}
    upload = request("/api/product-logs/uploads/create", {"kind": "directory", "files": [{"path": name, "size": len(data)} for name, data in files.items()]})["upload_id"]
    for name, data in files.items():
        if data:
            request("/api/product-logs/uploads/chunk?" + urlencode({"upload_id": upload, "path": name, "offset": 0}), data)
    request("/api/product-logs/uploads/finish", {"upload_id": upload})
    batch = request("/api/product-logs/start", {"upload_id": upload, "directory_pattern": "diag/slot62", "file_patterns": "*.log"})["batch_id"]
    result = _completed(request, batch)
    assert result["status"] == "ready"
    assert result["manifest"]["counts"]["driver_records"] == 1
    assert Path(result["manifest"]["input_root"]).joinpath("selected/diag/slot62/empty.txt").exists()


def test_upload_validation_ids_limits_traversal_and_static_mapping(product_http):
    _, request, manager, _ = product_http
    for unsafe in ["../outside.log", "/root.log", "H:/outside.log", "x\\bad.log", "x/./bad.log", "x//bad.log", "CON.log"]:
        with pytest.raises(HTTPError) as caught:
            request("/api/product-logs/uploads/create", {"kind": "directory", "files": [{"path": unsafe, "size": 1}]})
        assert caught.value.code == 400
    for files in [[{"path": "a.log", "size": 1}, {"path": "A.log", "size": 1}], [{"path": "x", "size": 1}, {"path": "x/a.log", "size": 1}]]:
        with pytest.raises(HTTPError):
            request("/api/product-logs/uploads/create", {"kind": "directory", "files": files})
    created = request("/api/product-logs/uploads/create", {"files": [{"path": "a.zip", "size": 3}]})
    upload = created["upload_id"]
    with pytest.raises(HTTPError):
        request("/api/product-logs/uploads/chunk?" + urlencode({"upload_id": upload, "path": "a.zip", "offset": 1}), b"x")
    with pytest.raises(HTTPError):
        request("/api/product-logs/uploads/finish", {"upload_id": upload})
    assert request("/api/product-logs/uploads/cancel", {"upload_id": upload})["ok"]
    assert not (manager.root / "uploads" / upload).exists()
    assert "日志浏览" in request("/product-logs/", raw=True)
    assert "PAGE_SIZE" in request("/product-logs/app.js", raw=True)
    for path in ["/product-logs/web.py", "/product-logs/../web.py", "/.runtime/product-logs/", "/%2eruntime/product-logs/", "/api/product-logs/events?batch_id=../x", "/api/product-logs/query?batch_id=" + "0" * 32]:
        with pytest.raises(HTTPError):
            request(path)


def test_busy_and_cooperative_cancel_do_not_stop_environment_script(product_http, tmp_path, monkeypatch):
    from product_log import workflow

    _, request, _, session = product_http
    input_dir = tmp_path / "logs"
    input_dir.mkdir()
    entered = threading.Event()
    def controlled(input_path, batch_dir, **kwargs):
        entered.set()
        deadline = time.monotonic() + 5
        while not kwargs["cancelled"]():
            if time.monotonic() > deadline:
                raise RuntimeError("test cancellation did not arrive")
            time.sleep(.01)
        raise RuntimeError("cooperative cancellation")

    monkeypatch.setattr(workflow, "run_analysis", controlled)
    first = request("/api/product-logs/start", {"input_path": str(input_dir)})
    assert entered.wait(2)
    with pytest.raises(HTTPError) as busy:
        request("/api/product-logs/start", {"input_path": str(input_dir)})
    assert busy.value.code == 409
    with pytest.raises(HTTPError):
        request("/api/product-logs/query?" + urlencode({"batch_id": first["batch_id"], "kind": "driver"}))
    assert request("/api/product-logs/stop", {"batch_id": first["batch_id"]})["ok"]
    assert _completed(request, first["batch_id"])["status"] == "cancelled"
    assert session.events == [] and session.generation == 0
    second = request("/api/product-logs/start", {"input_path": str(input_dir)})
    assert second["batch_id"] != first["batch_id"]
    request("/api/product-logs/stop", {"batch_id": second["batch_id"]})
    assert _completed(request, second["batch_id"])["status"] == "cancelled"


def test_storage_api_threshold_and_viewed_batch_protection(product_http, tmp_path, monkeypatch):
    from product_log import web

    _, request, original_manager, _ = product_http
    initial = request("/api/product-logs/storage")
    assert initial["limit_bytes"] == 3 * 1024 ** 3
    assert initial["usage_bytes"] == 0
    assert request("/api/product-logs/batches")["storage"]["limit_bytes"] == initial["limit_bytes"]
    # A real SQLite index exceeds this small test threshold; retained data may
    # exceed the trigger without turning it into a hard upload/task quota.
    manager = web.TaskManager(original_manager.root, storage_limit_bytes=1024)
    monkeypatch.setitem(web._MANAGERS, str(manager.root), manager)
    source = tmp_path / "original"
    source.mkdir()
    text = _driver("36.383", "threshold input remains intact")
    original = source / "a.log"
    original.write_text(text, encoding="utf-8")
    first = request("/api/product-logs/start", {"input_path": str(source)})["batch_id"]
    assert _completed(request, first)["status"] == "ready"
    pinned = request("/api/product-logs/storage?" + urlencode({"batch_id": first}))
    assert first in pinned["protected_batch_ids"]
    second = request("/api/product-logs/start", {"input_path": str(source)})["batch_id"]
    assert _completed(request, second)["status"] == "ready"
    assert {item["batch_id"] for item in request("/api/product-logs/batches")["batches"]} == {first, second}
    cleaned = request("/api/product-logs/storage?batch_id=")
    assert cleaned["over_limit"]  # Latest result is protected, even if large.
    assert first in cleaned["last_cleanup"]["removed_batch_ids"]
    assert cleaned["last_cleanup"]["reclaimed_bytes"] > 0
    assert not (manager.root / "batches" / first).exists()
    assert (manager.root / "batches" / second / "index.sqlite3").is_file()
    assert [item["batch_id"] for item in request("/api/product-logs/batches")["batches"]] == [second]
    assert original.read_text(encoding="utf-8") == text
    with pytest.raises(HTTPError):
        request("/api/product-logs/groups?" + urlencode({"batch_id": first, "kind": "driver"}))
    assert "自动清理阈值" in request("/product-logs/app.js", raw=True)
    assert "检查占用" in request("/product-logs/", raw=True)


def test_product_domains_nested_archives_mapping_and_unified_time_over_http(product_http, tmp_path):
    from product_log_domain_sample import build_domain_archive

    _, request, manager, _ = product_http
    archive = tmp_path / "product.tar.gz"
    expected = build_domain_archive(archive)
    original_bytes = archive.read_bytes()
    batch = request("/api/product-logs/start", {
        "input_path": str(archive), "collection_mode": "product",
        "diag_directory_pattern": "diag/slot62", "var_directory_pattern": "var", "workers": 3,
    })["batch_id"]
    completed = _completed(request, batch)
    assert completed["status"] == "ready", completed
    manifest = completed["manifest"]
    assert manifest["counts"]["files"] == expected["files"]
    assert manifest["counts"]["driver_records"] == expected["user_records"] + expected["kernel_records"]
    assert manifest["counts"]["firmware_records"] == expected["firmware_records"]
    assert archive.read_bytes() == original_bytes

    def query(**filters):
        return request("/api/product-logs/query?" + urlencode({"batch_id": batch, "limit": 500, **filters}))

    users = query(kind="driver", driver_domain="user", module="udk")
    assert users["total"] == 500
    assert {row["module_name"] for row in users["records"]} == {"udk"}
    assert {row["raw_module_name"] for row in users["records"]} == {"udk log", "udk upgrade"}
    kernels = query(kind="driver", driver_domain="kernel", module="udk", slot_id="999", cpu_id="999")
    assert kernels["total"] == 104
    assert all(row["driver_domain"] == "kernel" and not row["slot_id"] and not row["cpu_id"] for row in kernels["records"])
    unknown = [row for row in kernels["records"] if row["time_value"] is None]
    assert len(unknown) == 4 and all(not row["time"] and "时间未知" in row["text"] for row in unknown)
    assert all(row["time_value"] is None for row in kernels["records"][-4:])
    source = request("/api/product-logs/source?" + urlencode({"batch_id": batch, "source_id": kernels["records"][0]["source_id"]}))
    assert "uDk kernel" in "\n".join(line["text"] for line in source["lines"])

    unified = query(kind="all")
    assert unified["total"] == 6011
    known_values = [row["time_value"] for row in unified["records"]]
    assert all(value is not None for value in known_values)
    assert known_values == sorted(known_values)
    assert {row["kind"] for row in unified["records"]} == {"driver", "firmware"}
    assert {row["driver_domain"] for row in unified["records"] if row["kind"] == "driver"} == {"user", "kernel"}
    time_filtered = query(kind="all", start_time="2000-01-06 03:51:00", end_time="2000-01-06 03:57:39.999999")
    assert time_filtered["total"] == 6003
    assert query(kind="all", module="udk", slot_id="999", cpu_id="999")["total"] == 104
    firmware = request("/api/product-logs/groups?" + urlencode({"batch_id": batch, "kind": "firmware"}))["groups"]
    assert {row["log_type_name"] for row in firmware} == {"bios", "IMP", "IMU", "HSM", "4"}

    modules = request("/api/product-logs/modules?" + urlencode({"batch_id": batch}))["modules"]
    by_name = {row["module"]: row for row in modules}
    assert by_name["udk"]["user_count"] == 500 and by_name["udk"]["kernel_count"] == 104
    assert by_name["UBE"]["kernel_count"] == 100
    assert not by_name["MEME"]["kernel_configured"]
    rows = [{name: row[name] for name in ("module", "user_module_name", "kernel_keyword")} for row in modules]
    next(row for row in rows if row["module"] == "MEME")["kernel_keyword"] = "meme"
    rows.append({"module": "missing user", "user_module_name": "NO_USER", "kernel_keyword": "MAMI"})
    event_count = len(manager.task(batch).events)
    changed = request("/api/product-logs/modules", {"batch_id": batch, "modules": rows})["modules"]
    by_name = {row["module"]: row for row in changed}
    assert by_name["MEME"]["kernel_count"] == 100
    assert not by_name["missing user"]["user_exists"] and by_name["missing user"]["kernel_count"] == 100
    assert query(kind="driver", driver_domain="kernel", module="MEME")["total"] == 100
    assert query(kind="driver", driver_domain="kernel", module="missing user")["total"] == 100
    assert query(kind="all")["total"] == 6011  # overlapping mapping does not duplicate a record
    assert len(manager.task(batch).events) == event_count  # no extraction/parsing restarted
    restored = type(manager)(manager.root)
    assert restored.task(batch, ready=True).batch_dir.joinpath("module-map.json").exists()
    from product_log.module_map import list_module_mappings
    assert any(row["module"] == "missing user" for row in list_module_mappings(restored.task(batch).batch_dir))
    with pytest.raises(HTTPError) as invalid:
        request("/api/product-logs/modules", {"batch_id": batch, "modules": rows + [rows[0]]})
    assert invalid.value.code == 400
