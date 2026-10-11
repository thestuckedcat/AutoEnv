"""Size-based retention for the Web's own product-log uploads and batches.

Only opaque-ID children of ``uploads/`` and ``batches/`` are managed. Paths are
checked without resolving away links; a tree containing any link/reparse point
is counted where safe and never removed. CLI outputs and external inputs are
outside this module's scope.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DEFAULT_STORAGE_LIMIT_BYTES = 3 * 1024 ** 3
_ID = re.compile(r"^[0-9a-f]{32}$")
_KINDS = {"batches", "uploads"}


@dataclass(frozen=True)
class ManagedTree:
    kind: str
    identifier: str
    path: Path
    size_bytes: int
    created_at_ns: int
    safe: bool


def managed_root(root: Path) -> Path:
    """Keep the absolute spelling so junctions/symlinks remain detectable."""
    return Path(os.path.abspath(os.fspath(root)))


def _linked(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _validate_directory(path: Path) -> None:
    # Check existing parents too: resolve() alone would accept an external link.
    for item in reversed((path, *path.parents)):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if _linked(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"unsafe managed directory: {item}")


def _inspect_tree(path: Path) -> tuple[int, list[str]]:
    total = 0
    errors: list[str] = []
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            info = current.lstat()
            if _linked(info):
                errors.append(f"link/reparse point is not managed: {current}")
            elif stat.S_ISDIR(info.st_mode):
                pending.extend(current.iterdir())
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
            else:
                errors.append(f"special file is not managed: {current}")
        except OSError as exc:
            errors.append(f"cannot inspect {current}: {exc}")
    return total, errors


def scan_managed_storage(root: Path) -> tuple[list[ManagedTree], list[str]]:
    root = managed_root(root)
    trees: list[ManagedTree] = []
    errors: list[str] = []
    try:
        _validate_directory(root)
    except (OSError, ValueError) as exc:
        return trees, [str(exc)]
    for kind in sorted(_KINDS):
        folder = root / kind
        try:
            _validate_directory(folder)
            if not folder.exists():
                continue
            children = list(folder.iterdir())
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
            continue
        for child in children:
            if not _ID.fullmatch(child.name):
                continue
            try:
                info = child.lstat()
                if _linked(info) or not stat.S_ISDIR(info.st_mode):
                    errors.append(f"unsafe managed child: {child}")
                    continue
                size, problems = _inspect_tree(child)
                errors.extend(problems)
                trees.append(ManagedTree(kind, child.name, child, size, info.st_mtime_ns, not problems))
            except OSError as exc:
                errors.append(f"cannot inspect {child}: {exc}")
    return trees, errors


def remove_managed_tree(root: Path, kind: str, identifier: str) -> int:
    """Recheck an exact managed target immediately before recursive removal."""
    if kind not in _KINDS or not _ID.fullmatch(identifier):
        raise ValueError("invalid managed storage identifier")
    root = managed_root(root)
    target = root / kind / identifier
    _validate_directory(target)
    if not target.exists():
        return 0
    resolved_root, resolved_target = root.resolve(), target.resolve()
    if not resolved_target.is_relative_to(resolved_root) or resolved_target.parent != resolved_root / kind:
        raise ValueError("cleanup target escapes the managed workspace")
    size, errors = _inspect_tree(target)
    if errors:
        raise ValueError("; ".join(errors))
    shutil.rmtree(target)
    return size


def enforce_retention(
    root: Path,
    *,
    limit_bytes: int = DEFAULT_STORAGE_LIMIT_BYTES,
    protected_batch_ids: set[str] | None = None,
    protected_upload_ids: set[str] | None = None,
    batch_uploads: Mapping[str, str] | None = None,
    batch_created_at_ns: Mapping[str, int] | None = None,
) -> dict:
    """Remove oldest unprotected groups until usage is strictly below limit."""
    if not isinstance(limit_bytes, int) or isinstance(limit_bytes, bool) or limit_bytes <= 0:
        raise ValueError("storage limit must be a positive byte count")
    protected_batches = set(protected_batch_ids or ())
    protected_uploads = set(protected_upload_ids or ())
    bindings = dict(batch_uploads or {})
    created = dict(batch_created_at_ns or {})
    trees, errors = scan_managed_storage(root)
    before = sum(item.size_bytes for item in trees)
    current = before
    uploads = {item.identifier: item for item in trees if item.kind == "uploads"}
    existing_batches = {item.identifier for item in trees if item.kind == "batches"}
    owners: dict[str, set[str]] = {}
    for batch, upload in bindings.items():
        if batch in existing_batches:
            owners.setdefault(upload, set()).add(batch)
    # Referenced uploads stay with their batch; an old upload must never be
    # removed first while a directory-import index still points into it.
    protected_uploads.update(upload for batch, upload in bindings.items() if batch in protected_batches)
    candidates = [item for item in trees if item.safe and (
        (item.kind == "batches" and item.identifier not in protected_batches)
        or (item.kind == "uploads" and item.identifier not in protected_uploads and not owners.get(item.identifier))
    )]
    candidates.sort(key=lambda item: (created.get(item.identifier, item.created_at_ns) if item.kind == "batches" else item.created_at_ns, item.kind, item.identifier))
    removed_batches: list[str] = []
    removed_uploads: list[str] = []
    for item in candidates:
        if current < limit_bytes:
            break
        try:
            released = remove_managed_tree(root, item.kind, item.identifier)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot clean {item.path}: {exc}")
            current = sum(tree.size_bytes for tree in scan_managed_storage(root)[0])
            continue
        current -= released
        if item.kind == "uploads":
            removed_uploads.append(item.identifier)
            continue
        removed_batches.append(item.identifier)
        upload_id = bindings.get(item.identifier)
        if upload_id is not None:
            owners.get(upload_id, set()).discard(item.identifier)
            upload = uploads.get(upload_id)
            if upload is not None and upload.safe and not owners.get(upload_id) and upload_id not in protected_uploads:
                try:
                    current -= remove_managed_tree(root, "uploads", upload_id)
                    removed_uploads.append(upload_id)
                except (OSError, ValueError) as exc:
                    errors.append(f"cannot clean {upload.path}: {exc}")
                    current = sum(tree.size_bytes for tree in scan_managed_storage(root)[0])
    # Failed recursive deletion can have partially reclaimed a directory, and
    # active uploads can grow meanwhile. Report the actual remaining files.
    after_trees, after_errors = scan_managed_storage(root)
    after = sum(item.size_bytes for item in after_trees)
    return {
        "usage_bytes": after,
        "limit_bytes": limit_bytes,
        "threshold_reached": before >= limit_bytes,
        "over_limit": after >= limit_bytes,
        "removed_batch_ids": removed_batches,
        "removed_upload_ids": removed_uploads,
        "reclaimed_bytes": max(0, before - after),
        "errors": list(dict.fromkeys([*errors, *after_errors])),
        "protected_batch_ids": sorted(protected_batches),
        "protected_upload_ids": sorted(protected_uploads),
    }
