"""Exercise storage retention on real local trees, without gigabyte fixtures."""
from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from product_log._storage import INDEX_SCHEMA_VERSION
from product_log.web import TaskManager


def _identifier(number: int) -> str:
    return f"{number:032x}"


def _batch(root: Path, number: int, *, status: str = "ready", size: int = 100,
           input_path: Path | None = None, upload_id: str | None = None) -> Path:
    folder = root / "batches" / _identifier(number)
    folder.mkdir(parents=True)
    manifest = {
        "batch_id": folder.name, "batch_dir": str(folder), "status": status,
        "index_schema_version": INDEX_SCHEMA_VERSION, "time_unit": "microseconds",
        "managed_created_at_ns": number * 1_000_000_000,
        "input_root": str(input_path or root.parent / "original"),
        "input_path": str(input_path or root.parent / "original"),
        "counts": {},
    }
    if upload_id is not None:
        manifest["upload_id"] = upload_id
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "index.sqlite3").write_bytes(b"i" * size)
    os.utime(folder, ns=(number * 1_000_000_000, number * 1_000_000_000))
    return folder


def _upload(root: Path, number: int, *, size: int = 100) -> Path:
    folder = root / "uploads" / _identifier(number)
    folder.mkdir(parents=True)
    (folder / "source.log").write_bytes(b"u" * size)
    os.utime(folder, ns=(number * 1_000_000_000, number * 1_000_000_000))
    return folder


def _bytes(root: Path) -> int:
    """Expected logical file size, used only with ordinary fixture trees."""
    return sum(path.stat().st_size for kind in ("batches", "uploads")
               for path in (root / kind).rglob("*") if path.is_file())


def _wait_done(manager: TaskManager, batch_id: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if manager.events(batch_id, 0)["status"] in {"ready", "failed", "cancelled"}:
            return
        time.sleep(0.01)
    pytest.fail("local retention task did not finish")


def test_default_budget_is_three_gibibytes(tmp_path):
    state = TaskManager(tmp_path / "runtime").storage()
    assert state["limit_bytes"] == 3 * 1024 ** 3
    assert state["usage_bytes"] == 0
    assert not state["over_limit"]


@pytest.mark.parametrize("headroom", [1, 0])
def test_threshold_includes_equality_and_history_updates(tmp_path, headroom):
    root = tmp_path / "runtime"
    oldest = _batch(root, 1, size=1000)
    newest = _batch(root, 2, size=1000)
    before = _bytes(root)
    manager = TaskManager(root, storage_limit_bytes=before + headroom)
    state = manager.storage()
    assert newest.exists()
    if headroom:
        assert oldest.exists()
        assert len(manager.batches()) == 2
        assert state["usage_bytes"] == before
    else:
        assert not oldest.exists()
        assert [item["batch_id"] for item in manager.batches()] == [newest.name]
        with pytest.raises(ValueError, match="unknown"):
            manager.task(oldest.name)
        assert state["last_cleanup"]["removed_batch_ids"] == [oldest.name]
        assert state["last_cleanup"]["reclaimed_bytes"] == before - _bytes(root)
    assert state["usage_bytes"] == _bytes(root)
    assert not state["over_limit"]


def test_delete_oldest_and_stop_once_under_budget(tmp_path):
    root = tmp_path / "runtime"
    first = _batch(root, 1, size=4000)
    second = _batch(root, 2, size=4000)
    newest = _batch(root, 3, size=4000)
    first_bytes = sum(path.stat().st_size for path in first.iterdir())
    limit = _bytes(root) - first_bytes + 1
    manager = TaskManager(root, storage_limit_bytes=limit)
    assert not first.exists()
    assert second.exists() and newest.exists()
    state = manager.storage()
    assert state["last_cleanup"]["removed_batch_ids"] == [first.name]
    assert state["usage_bytes"] < limit


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_and_cancelled_history_is_reclaimable(tmp_path, status):
    root = tmp_path / "runtime"
    old = _batch(root, 1, status=status, size=5000)
    newest = _batch(root, 2, size=100)
    manager = TaskManager(root, storage_limit_bytes=2000)
    assert not old.exists()
    assert newest.exists()
    assert [item["batch_id"] for item in manager.batches()] == [newest.name]


def test_keep_latest_ready_and_latest_terminal_when_space_cannot_be_reclaimed(tmp_path):
    root = tmp_path / "runtime"
    old = _batch(root, 1, size=4000)
    ready = _batch(root, 2, size=4000)
    failed = _batch(root, 3, status="failed", size=4000)
    manager = TaskManager(root, storage_limit_bytes=3000)
    state = manager.storage()
    assert not old.exists()
    assert ready.exists() and failed.exists()
    assert set(state["protected_batch_ids"]) >= {ready.name, failed.name}
    assert state["usage_bytes"] == _bytes(root)
    assert state["over_limit"]


def test_current_browser_batch_is_pinned_until_selection_is_released(tmp_path):
    root = tmp_path / "runtime"
    viewed = _batch(root, 1)
    other = _batch(root, 2)
    latest = _batch(root, 3)
    manager = TaskManager(root, storage_limit_bytes=3000)
    manager.storage(pin_batch_id=viewed.name)
    (viewed / "index.sqlite3").write_bytes(b"v" * 4000)
    (other / "index.sqlite3").write_bytes(b"o" * 4000)
    protected = manager.maintenance()
    assert viewed.exists() and latest.exists() and not other.exists()
    assert viewed.name in protected["protected_batch_ids"]
    assert protected["removed_batch_ids"] == [other.name]
    assert protected["over_limit"]
    released = manager.storage(pin_batch_id="")
    assert not viewed.exists()
    assert latest.exists()
    assert released["removed_batch_ids"] == [viewed.name]
    assert not released["over_limit"]


def test_active_task_tree_is_not_removed(tmp_path, monkeypatch):
    from product_log import workflow

    root = tmp_path / "runtime"
    old = _batch(root, 1)
    ready = _batch(root, 2)
    source = tmp_path / "source"
    source.mkdir()
    original = b"2000-01-06 03:51:36.383 host (SlotId=62, CpuId=0, ModuleName=UBE, Info=<1> local)\n"
    (source / "source.log").write_bytes(original)
    manager = TaskManager(root, storage_limit_bytes=_bytes(root) + 1000)
    entered, release = threading.Event(), threading.Event()
    run_analysis = workflow.run_analysis

    def paused(input_path, batch_dir, **kwargs):
        (batch_dir / "partial.bin").write_bytes(b"p" * 2000)
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test task was not released")
        return run_analysis(input_path, batch_dir, **kwargs)

    monkeypatch.setattr(workflow, "run_analysis", paused)
    started = manager.start({"input_path": str(source), "file_patterns": "*.log", "workers": 1})
    try:
        assert entered.wait(3)
        active = root / "batches" / started["batch_id"]
        (old / "index.sqlite3").write_bytes(b"o" * 3000)
        state = manager.storage()
        assert active.exists() and (active / "partial.bin").exists()
        assert not old.exists() and ready.exists()
        assert active.name in state["protected_batch_ids"]
        assert state["over_limit"]
    finally:
        release.set()
        _wait_done(manager, started["batch_id"])
    assert (source / "source.log").read_bytes() == original


def test_live_upload_is_protected_but_restart_orphan_can_be_reclaimed(tmp_path):
    root = tmp_path / "runtime"
    manager = TaskManager(root, storage_limit_bytes=1000)
    created = manager.create_upload({"kind": "directory", "files": [{"path": "source.log", "size": 2000}]})
    upload_id = created["upload_id"]
    manager.write_chunk(upload_id, "source.log", 0, 2000, io.BytesIO(b"u" * 2000))
    state = manager.storage()
    uploaded = root / "uploads" / upload_id
    assert uploaded.exists()
    assert upload_id in state["protected_upload_ids"]
    assert state["over_limit"]
    manager.finish_upload(upload_id)
    assert manager.storage()["over_limit"]
    restarted = TaskManager(root, storage_limit_bytes=1000)
    assert not uploaded.exists()
    assert restarted.storage()["usage_bytes"] == 0


def test_real_upload_task_restores_its_source_binding_after_restart(tmp_path):
    root = tmp_path / "runtime"
    manager = TaskManager(root, storage_limit_bytes=1000)
    content = ("2000-01-06 03:51:36.383 host %%01UBFWD/7/UBSDK_DIAG(D):"
               "Service=fwmd0;UB Forward SDK module. "
               "(SlotId=62, CpuId=0, ModuleName=UBE, Info=<1> retained upload)\n").encode()
    upload_id = manager.create_upload({"kind": "directory", "files": [{"path": "source.log", "size": len(content)}]})["upload_id"]
    manager.write_chunk(upload_id, "source.log", 0, len(content), io.BytesIO(content))
    manager.finish_upload(upload_id)
    started = manager.start({"upload_id": upload_id, "file_patterns": "*.log", "workers": 1})
    _wait_done(manager, started["batch_id"])
    task = manager.task(started["batch_id"], ready=True)
    assert task.manifest["counts"]["driver_records"] == 1
    assert task.manifest["upload_id"] == upload_id
    restarted = TaskManager(root, storage_limit_bytes=1000)
    assert restarted.task(task.batch_id, ready=True).manifest["upload_id"] == upload_id
    state = restarted.storage()
    assert upload_id in state["protected_upload_ids"]
    assert task.batch_id in state["protected_batch_ids"]
    assert state["over_limit"]
    assert (root / "uploads" / upload_id / "source.log").read_bytes() == content


@pytest.mark.parametrize("input_owner", ["batch", "upload"])
def test_local_managed_input_survives_start_cleanup_and_restart(tmp_path, input_owner):
    root = tmp_path / "runtime"
    if input_owner == "batch":
        owner = _batch(root, 1)
        source = owner / "input-logs"
        source.mkdir()
    else:
        owner = _upload(root, 100, size=0)
        source = owner
    content = ("2000-01-06 03:51:36.383 host %%01UBFWD/7/UBSDK_DIAG(D):"
               "Service=fwmd0;UB Forward SDK module. "
               "(SlotId=62, CpuId=0, ModuleName=UBE, Info=<1> managed local input)\n").encode()
    (source / "source.log").write_bytes(content)
    _batch(root, 2)
    manager = TaskManager(root, storage_limit_bytes=_bytes(root) + 500)
    # The supplied input would now be eligible for deletion without the start
    # operation temporarily protecting it before the new task is registered.
    (owner / "payload.bin").write_bytes(b"p" * 4000)
    started = manager.start({"input_path": str(source), "file_patterns": "*.log", "workers": 1})
    _wait_done(manager, started["batch_id"])
    task = manager.task(started["batch_id"], ready=True)
    assert task.manifest["counts"]["driver_records"] == 1
    assert (source / "source.log").read_bytes() == content
    restarted = TaskManager(root, storage_limit_bytes=1000)
    restored = restarted.task(task.batch_id, ready=True)
    assert restored.input_path == source
    state = restarted.storage()
    if input_owner == "batch":
        assert owner.name in state["protected_batch_ids"]
    else:
        assert restored.upload_id == owner.name
        assert owner.name in state["protected_upload_ids"]
    assert (source / "source.log").read_bytes() == content
    assert state["over_limit"]


def test_restart_recovers_upload_binding_from_interrupted_web_task_metadata(tmp_path):
    root = tmp_path / "runtime"
    uploaded = _upload(root, 100, size=3000)
    latest_ready = _batch(root, 2)
    interrupted = root / "batches" / _identifier(3)
    interrupted.mkdir()
    (interrupted / "partial.bin").write_bytes(b"p" * 500)
    (interrupted / "web-task.json").write_text(json.dumps({
        "batch_id": interrupted.name, "batch_dir": str(interrupted),
        "status": "preparing", "upload_id": uploaded.name,
        "input_path": str(uploaded), "input_root": str(uploaded),
        "managed_created_at_ns": 3 * 1_000_000_000,
    }), encoding="utf-8")
    manager = TaskManager(root, storage_limit_bytes=1000)
    task = manager.task(interrupted.name)
    assert task.status == "failed" and task.upload_id == uploaded.name
    assert latest_ready.exists() and interrupted.exists() and uploaded.exists()
    assert uploaded.name in manager.storage()["protected_upload_ids"]
    # A later completed task supersedes the interrupted latest result, so its
    # metadata ownership now permits reclaiming both batch and uploaded input.
    newest = _batch(root, 4)
    restarted = TaskManager(root, storage_limit_bytes=1000)
    assert newest.exists() and not interrupted.exists() and not uploaded.exists()
    assert interrupted.name not in {item["batch_id"] for item in restarted.batches()}


def test_shared_upload_remains_while_a_retained_batch_references_it(tmp_path):
    root = tmp_path / "runtime"
    uploaded = _upload(root, 100, size=3000)
    first = _batch(root, 1, input_path=uploaded / "source.log", upload_id=uploaded.name)
    viewed = _batch(root, 2, input_path=uploaded / "source.log", upload_id=uploaded.name)
    source = tmp_path / "external-original"
    source.mkdir()
    (source / "source.log").write_bytes(b"external source stays untouched")
    latest = _batch(root, 3, input_path=source)
    manager = TaskManager(root, storage_limit_bytes=_bytes(root) + 1000)
    manager.storage(pin_batch_id=viewed.name)
    (first / "index.sqlite3").write_bytes(b"i" * 2000)
    result = manager.storage()
    assert not first.exists()
    assert uploaded.exists() and viewed.exists()
    assert uploaded.name in result["protected_upload_ids"]
    (viewed / "index.sqlite3").write_bytes(b"i" * 2000)
    released = manager.storage(pin_batch_id="")
    assert not viewed.exists() and not uploaded.exists()
    assert latest.exists()
    assert uploaded.name in released["removed_upload_ids"]
    assert (source / "source.log").read_bytes() == b"external source stays untouched"


def test_usage_includes_index_and_query_cache_only_in_managed_trees(tmp_path):
    root = tmp_path / "runtime"
    source = tmp_path / "external-source"
    source.mkdir()
    (source / "huge-original.log").write_bytes(b"e" * 20000)
    batch = _batch(root, 1, input_path=source, size=5000)
    (batch / "query-cache.sqlite3").write_bytes(b"c" * 3000)
    (root / "unmanaged").mkdir()
    (root / "unmanaged" / "keep.bin").write_bytes(b"x" * 20000)
    manager = TaskManager(root, storage_limit_bytes=1000)
    state = manager.storage()
    assert state["usage_bytes"] == _bytes(root)
    assert state["usage_bytes"] >= 8000
    assert state["usage_bytes"] < 20000
    assert state["over_limit"]
    assert (root / "unmanaged" / "keep.bin").stat().st_size == 20000
    assert (source / "huge-original.log").stat().st_size == 20000


def test_restart_reclaims_unfinished_or_malformed_managed_batches(tmp_path):
    root = tmp_path / "runtime"
    orphan = root / "batches" / _identifier(1)
    orphan.mkdir(parents=True)
    (orphan / "unfinished.bin").write_bytes(b"o" * 3000)
    malformed = root / "batches" / _identifier(2)
    malformed.mkdir()
    (malformed / "manifest.json").write_text("{not-json", encoding="utf-8")
    (malformed / "partial.bin").write_bytes(b"m" * 3000)
    latest = _batch(root, 3)
    unmanaged = root / "batches" / "manual-scratch"
    unmanaged.mkdir()
    (unmanaged / "keep.txt").write_text("do not delete", encoding="utf-8")
    for number, folder in enumerate((orphan, malformed), 1):
        os.utime(folder, ns=(number * 1_000_000_000, number * 1_000_000_000))
    manager = TaskManager(root, storage_limit_bytes=2000)
    assert not orphan.exists() and not malformed.exists()
    assert latest.exists() and (unmanaged / "keep.txt").exists()
    assert [item["batch_id"] for item in manager.batches()] == [latest.name]


@pytest.mark.parametrize("partial", [False, True])
def test_delete_failure_keeps_history_and_reports_actual_usage(tmp_path, monkeypatch, partial):
    from product_log import retention

    root = tmp_path / "runtime"
    old = _batch(root, 1)
    next_candidate = _batch(root, 2)
    latest = _batch(root, 3)
    manager = TaskManager(root, storage_limit_bytes=_bytes(root) + 500)
    (old / "index.sqlite3").write_bytes(b"o" * 4000)
    before = _bytes(root)
    remove = retention.remove_managed_tree

    def deny_old(runtime_root, kind, identifier):
        if kind == "batches" and identifier == old.name:
            if partial:
                (old / "index.sqlite3").unlink()
            raise PermissionError("fixture access denied")
        return remove(runtime_root, kind, identifier)

    monkeypatch.setattr(retention, "remove_managed_tree", deny_old)
    state = manager.storage()
    assert old.exists() and latest.exists()
    assert manager.task(old.name).batch_dir == old
    assert state["removed_batch_ids"] == ([] if partial else [next_candidate.name])
    assert next_candidate.exists() is partial
    assert state["reclaimed_bytes"] == before - _bytes(root)
    assert state["usage_bytes"] == _bytes(root)
    assert state["over_limit"] is (not partial)
    assert any("fixture access denied" in error for error in state["errors"])


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if result.returncode:
            pytest.skip("Windows filesystem does not permit junction creation")
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("platform does not permit directory symlink creation")


@pytest.mark.parametrize("link_location", ["nested", "managed-root"])
def test_reparse_trees_do_not_count_or_delete_external_targets(tmp_path, link_location):
    root = tmp_path / "runtime"
    outside = tmp_path / "external"
    outside.mkdir()
    sentinel = outside / "sentinel.log"
    sentinel.write_bytes(b"outside" * 5000)
    newest = _batch(root, 2)
    if link_location == "nested":
        old = _batch(root, 1, size=4000)
        link = old / "external-link"
    else:
        old = root / "batches" / _identifier(1)
        link = old
        # Make the external target tempting as a history batch; it must be ignored.
        (outside / "manifest.json").write_text(json.dumps({
            "status": "ready", "index_schema_version": INDEX_SCHEMA_VERSION,
            "time_unit": "microseconds", "created_at_ns": 1,
        }), encoding="utf-8")
    _directory_link(link, outside)
    try:
        manager = TaskManager(root, storage_limit_bytes=2000)
        state = manager.storage()
        assert sentinel.read_bytes() == b"outside" * 5000
        assert newest.exists() and old.exists()
        assert state["usage_bytes"] < sentinel.stat().st_size
        assert state["errors"]
        if link_location == "nested":
            assert (old / "index.sqlite3").stat().st_size == 4000
            assert state["removed_batch_ids"] == []
            assert state["over_limit"]
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


@pytest.mark.parametrize("link_location", ["root", "ancestor"])
def test_managed_runtime_reparse_ancestors_cannot_authorize_external_deletion(tmp_path, link_location):
    from product_log.retention import remove_managed_tree

    outside = tmp_path / "external"
    outside.mkdir()
    target = outside if link_location == "root" else outside / "runtime"
    old = _batch(target, 1, size=4000)
    newest = _batch(target, 2)
    link = tmp_path / "linked-runtime"
    _directory_link(link, outside)
    runtime = link if link_location == "root" else link / "runtime"
    try:
        manager = TaskManager(runtime, storage_limit_bytes=1000)
        state = manager.storage()
        assert state["usage_bytes"] == 0
        assert state["errors"]
        assert manager.batches() == []
        assert old.exists() and newest.exists()
        with pytest.raises(ValueError, match="unsafe"):
            remove_managed_tree(runtime, "batches", old.name)
        assert (old / "index.sqlite3").stat().st_size == 4000
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()
