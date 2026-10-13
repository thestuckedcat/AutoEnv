"""Local A/B workflow built on AutoEnv file handlers, with no remote sessions."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PureWindowsPath
from typing import Callable, Iterable

from autoenv import get_file, get_handler
from autoenv.file_handles import _matches, _walk_paths
from autoenv.local_archives import MAX_EXPANDED_BYTES, MAX_EXPANDED_FILES, detect_archive

from ._storage import INDEX_SCHEMA_VERSION, write_manifest
from .index import build_index
from .parser import AnalysisCancelled, check_cancelled, parse_file


def _patterns(values: Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    result = list(dict.fromkeys(values))
    for pattern in result:
        if not isinstance(pattern, str) or any(char in pattern for char in ("/", "\\", "\x00")):
            raise ValueError("File patterns must be basename keywords/globs")
    return result


def _identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _source_key(source: dict) -> str:
    """Keep archive boundaries structured; literal '!/' in a name is valid."""
    return json.dumps(source["provenance_parts"], ensure_ascii=False, separators=(",", ":"))


def run_analysis(input_path: str | Path, batch_dir: str | Path, *,
                 directory_pattern: str = "", archive_patterns: Iterable[str] | None = None,
                 file_patterns: Iterable[str] | None = None, recursive: bool = True,
                 collection_mode: str = "auto", diag_directory_pattern: str = "diag",
                 var_directory_pattern: str = "varlog",
                 copy_patterns: Iterable[str] | None = None, rename_map: dict[str, str] | None = None,
                 workers: int = 4, progress: Callable[[dict], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> dict:
    """Prepare, parse and atomically publish a single isolated batch.

    Every supported archive is expanded before unique directory selection,
    including all nested layers outside the selected directories. The deprecated
    archive_patterns argument is retained for callers but no longer limits this
    preparation. Auto detects product directories, retaining explicit generic
    directory selection. A file copied into ``selected`` replaces
    its original candidate, while independent files with identical bytes remain.
    Errors/cancellation preserve a non-ready manifest and never publish queries.
    """
    input_path = Path(input_path).absolute()
    batch_dir = Path(batch_dir).absolute()
    if input_path.is_symlink() or batch_dir.is_symlink():
        raise ValueError("Symbolic link inputs/output roots are not supported")
    input_path, batch_dir = input_path.resolve(), batch_dir.resolve()
    archive_patterns, file_patterns, copy_patterns = map(_patterns, (archive_patterns, file_patterns, copy_patterns))
    rename_map = dict(rename_map or {})
    workers = int(workers)
    if collection_mode not in {"auto", "product", "generic"}:
        raise ValueError("collection_mode must be auto, product or generic")
    if not 1 <= workers <= 32:
        raise ValueError("workers must be 1..32")
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if not input_path.is_file() and not input_path.is_dir():
        raise ValueError("Input must be a regular directory or archive")
    if input_path == batch_dir or input_path.is_relative_to(batch_dir / "product_log"):
        raise ValueError("Input must not be the output directory or a prior generated product_log tree")
    batch_dir.mkdir(parents=True, exist_ok=True)
    for existing in ("manifest.json", "index.sqlite3", "product_log", ".shards"):
        if (batch_dir / existing).exists():
            raise FileExistsError(f"Batch output already exists: {batch_dir / existing}")
    started = time.monotonic()
    work = batch_dir / "product_log"
    manifest = {
        "index_schema_version": INDEX_SCHEMA_VERSION, "time_unit": "microseconds",
        "batch_id": batch_dir.name, "batch_dir": str(batch_dir), "input_path": str(input_path),
        "input_root": "", "product_log_path": str(work), "status": "preparing",
        "counts": {"files": 0, "driver_records": 0, "firmware_records": 0, "dumps": 0, "diagnostics": 0},
        "duration_seconds": 0.0, "archives": [], "sources": [],
        "rules": {"directory_pattern": directory_pattern, "archive_patterns": archive_patterns,
                  "file_patterns": file_patterns, "copy_patterns": copy_patterns,
                  "rename_map": rename_map, "recursive": bool(recursive),
                  "collection_mode": collection_mode, "diag_directory_pattern": diag_directory_pattern,
                  "var_directory_pattern": var_directory_pattern},
    }

    def emit(stage: str, count: int, message: str) -> None:
        check_cancelled(cancelled)
        changed = manifest["status"] != stage
        manifest["status"] = stage
        # Source mappings can be large; persist on stage changes, not per file.
        if changed or not (batch_dir / "manifest.json").exists():
            write_manifest(batch_dir / "manifest.json", manifest)
        if progress:
            progress({"stage": stage, "count": count, "message": message})

    budget_files = budget_bytes = 0

    def account(root: Path) -> None:
        nonlocal budget_files, budget_bytes
        for path in get_handler(root, True).paths:
            check_cancelled(cancelled)
            budget_files += 1
            budget_bytes += path.stat().st_size
        if budget_files > MAX_EXPANDED_FILES or budget_bytes > MAX_EXPANDED_BYTES:
            raise ValueError("Task cumulative archive expansion budget exceeded")

    try:
        emit("preparing", 0, "Preparing isolated local analysis")
        for name in ("input", "expanded", "selected"):
            (work / name).mkdir(parents=True)
        top_archive = ""
        if input_path.is_dir():
            input_root = input_path
            manifest["input_type"] = "directory"
        else:
            detected = detect_archive(input_path)
            if detected is None:
                raise ValueError("File input must be a supported ZIP/TAR/GZIP/RAR archive")
            emit("extracting", 0, "Extracting top-level input archive")
            input_root = get_file(input_path.parent, input_path.name).unzip(
                work / "input", max_files=MAX_EXPANDED_FILES, max_bytes=MAX_EXPANDED_BYTES,
                check_cancelled=lambda: check_cancelled(cancelled),
            )
            check_cancelled(cancelled)
            account(input_root)
            top_archive = str(input_path)
            manifest["input_type"] = detected
            manifest["archives"].append({"path": str(input_path), "origin_path": str(input_path),
                                          "destination": str(input_root), "format": detected, "level": 0})
        manifest["input_root"] = str(input_root)
        excluded = (batch_dir,) if input_path.is_dir() else ()
        # Each root keeps its physical isolation and its archive ancestry. A
        # logical directory prefix skips archive filenames so partial paths can
        # span a packing boundary without exposing task-generated hash folders.
        forest: list[dict] = [{"root": input_root, "parent": None, "archive": None,
                              "prefix": (input_root.name,), "files": [], "level": 0}]
        archives: list[tuple[Path, str, dict, int]] = []
        archive_entries: dict[Path, dict] = {}

        def source_for(path: Path, node: dict) -> dict:
            member = path.relative_to(node["root"]).as_posix()
            original = node["archive"]
            relative = original["relative_path"] + "!/" + member if original else member
            return {
                "path": str(path), "relative_path": relative,
                "provenance_parts": [*original["provenance_parts"], member] if original else [member],
                "origin_path": original["origin_path"] + "!/" + member if original else (
                    f"{top_archive}!/{member}" if top_archive else str(path)),
                "archive_path": original["path"] if original else top_archive,
                "member_path": member if original else (member if top_archive else ""),
                "original_name": path.name,
            }

        cursor = 0
        while cursor < len(forest):
            node = forest[cursor]
            cursor += 1
            files = sorted(_walk_paths(node["root"], True, exclude_paths=excluded if node["parent"] is None else ()),
                           key=lambda path: (path.name, path.relative_to(node["root"]).as_posix()))
            for path in files:
                check_cancelled(cancelled)
                kind = detect_archive(path)
                node["files"].append((path, kind))
                if kind is None:
                    continue
                original = source_for(path, node)
                level = node["level"] + 1
                archives.append((path, kind, original, level))
                destination = work / "expanded" / _identity(_source_key(original))
                emit("extracting", len(archives), f"Extracting {original['relative_path']}")
                if budget_files >= MAX_EXPANDED_FILES or budget_bytes >= MAX_EXPANDED_BYTES:
                    raise ValueError("Task cumulative archive expansion budget exhausted")
                expanded = get_file(path.parent, path.name).unzip(
                    destination, max_files=MAX_EXPANDED_FILES - budget_files,
                    max_bytes=MAX_EXPANDED_BYTES - budget_bytes,
                    check_cancelled=lambda: check_cancelled(cancelled),
                )
                check_cancelled(cancelled)
                account(expanded)
                entry = {"path": str(path), "origin_path": original["origin_path"],
                         "destination": str(expanded), "format": kind, "level": level,
                         "provenance_parts": original["provenance_parts"]}
                manifest["archives"].append(entry)
                archive_entries[path] = entry
                forest.append({"root": expanded, "parent": node, "archive": original,
                               "prefix": (*node["prefix"], *path.relative_to(node["root"]).parent.parts),
                               "files": [], "level": level})

        emit("collecting", 0, "Locating unique target directories after all archive layers are expanded")

        def find_prepared_directory(pattern: str, recurse: bool = True) -> tuple[Path, dict]:
            if not isinstance(pattern, str):
                raise TypeError("partial_path must be a string")
            normalized = pattern.replace("\\", "/")
            components = normalized.split("/")
            if (not normalized or normalized.startswith("/") or PureWindowsPath(pattern).drive
                    or ":" in normalized or "\x00" in normalized
                    or any(not part or part in {".", ".."} or "**" in part for part in components)):
                raise ValueError("partial_path must contain safe relative directory components")
            matches: list[tuple[Path, dict]] = []
            for node in forest:
                check_cancelled(cancelled)
                directories = list(_walk_paths(node["root"], True, directories=True,
                                               exclude_paths=excluded if node["parent"] is None else ()))
                if node["parent"] is None:
                    directories.insert(0, node["root"])
                for candidate in directories:
                    logical = (*node["prefix"], *candidate.relative_to(node["root"]).parts)
                    if not recurse and len(logical) > 2:
                        continue
                    found = (_matches(logical[-1], components[0], fuzzy=True) if len(components) == 1 else
                             len(logical) >= len(components) and all(
                                 _matches(name, rule, fuzzy=False)
                                 for name, rule in zip(logical[-len(components):], components)))
                    if found:
                        matches.append((candidate, node))
            if not matches:
                raise FileNotFoundError(f"no directory matches {pattern!r} in prepared input")
            if len(matches) > 1:
                raise ValueError(f"ambiguous directory {pattern!r}: " + ", ".join(
                    source_for(path, node)["origin_path"] for path, node in matches))
            return matches[0]

        targets: dict[str, Path] = {}
        target_nodes: dict[str, dict] = {}
        effective_mode = collection_mode
        if collection_mode != "generic" and not (collection_mode == "auto" and directory_pattern):
            for domain, pattern in (("user", diag_directory_pattern), ("kernel", var_directory_pattern)):
                directory_rule = {"diag": "[d]iag", "varlog": "[v]arlog", "var": "[v]ar"}.get(pattern, pattern)
                try:
                    targets[domain], target_nodes[domain] = find_prepared_directory(directory_rule)
                except FileNotFoundError:
                    if domain == "kernel" and pattern == "varlog":
                        # Compatibility is only an absence fallback. An ambiguous
                        # varlog tree must still fail instead of selecting var.
                        try:
                            targets[domain], target_nodes[domain] = find_prepared_directory("[v]ar")
                        except FileNotFoundError:
                            pass
            if not targets and collection_mode == "product":
                raise FileNotFoundError("No diag or varlog directory matches the product collection rules")
            effective_mode = "product" if targets else "generic"
        else:
            effective_mode = "generic"
        if effective_mode == "generic":
            targets["user"], target_nodes["user"] = (find_prepared_directory(directory_pattern, bool(recursive))
                                                      if directory_pattern else (input_root, forest[0]))
        elif targets.get("user") == targets.get("kernel"):
            raise ValueError("diag and varlog directory rules must select different directories")
        manifest["collection_mode"] = effective_mode
        manifest["target_paths"] = {domain: str(target) for domain, target in targets.items()}
        manifest["target_path"] = str(targets.get("user", next(iter(targets.values()))))
        candidates: dict[Path, dict] = {}
        copy_candidates: dict[Path, dict] = {}
        kernel_name = re.compile(r"^cpdt_dmesg_current\.log(?:\.(\d+))?$")

        def belongs(path: Path, node: dict, domain: str, recurse: bool = True) -> bool:
            directory_depth = 0
            while True:
                if node is target_nodes[domain]:
                    if not path.is_relative_to(targets[domain]):
                        return False
                    return recurse or directory_depth + len(path.relative_to(targets[domain]).parent.parts) == 0
                directory_depth += len(path.relative_to(node["root"]).parent.parts)
                if node["parent"] is None:
                    return False
                path, node = Path(node["archive"]["path"]), node["parent"]

        def owning_domain(path: Path, node: dict) -> str | None:
            matches = [domain for domain in targets if belongs(path, node, domain)]
            if not matches:
                return None
            # A selected child domain owns its full subtree across packing
            # boundaries. Each source is collected once, even for nested domains.
            return max(matches, key=lambda domain: (
                target_nodes[domain]["level"], len(targets[domain].relative_to(target_nodes[domain]["root"]).parts)))

        for node in forest:
            for path, kind in node["files"]:
                check_cancelled(cancelled)
                domain = owning_domain(path, node)
                if domain is None:
                    continue
                if kind:
                    archive_entries[path]["log_domain"] = domain
                    continue
                is_kernel = effective_mode == "product" and domain == "kernel"
                if not belongs(path, node, domain, True if is_kernel else bool(recursive)):
                    continue
                selected_patterns = file_patterns or (["diaglog_*.log", "diag.log"] if effective_mode == "product" else [])
                if is_kernel:
                    selected = bool(kernel_name.fullmatch(path.name))
                elif effective_mode == "product" and not file_patterns:
                    selected = path.name == "diag.log" or (path.name.startswith("diaglog_") and path.name.endswith(".log"))
                else:
                    selected = not selected_patterns or any(_matches(path.name, pattern or "*", fuzzy=True)
                                                             for pattern in selected_patterns)
                copy = not is_kernel and any(_matches(path.name, pattern or "*", fuzzy=True) for pattern in copy_patterns)
                if not selected and not copy:
                    continue
                source = source_for(path, node)
                source.update(log_domain=domain,
                              device_source="target:" + source_for(targets[domain], target_nodes[domain])["relative_path"])
                if selected:
                    candidates[path] = source
                if copy:
                    copy_candidates[path] = source
        emit("collecting", len(candidates), "Collecting prepared log sources")
        for key in rename_map:
            if "/" in key:
                matching = {_source_key(source) for source in {**candidates, **copy_candidates}.values()
                            if source["relative_path"] == key}
                if len(matching) > 1:
                    raise ValueError(f"Ambiguous relative rename source: {key}")
        # rename only operates on a task-local copy; a rename rule implicitly selects that source.
        for path, source in list(candidates.items()):
            if path.name in rename_map or source["relative_path"] in rename_map:
                copy_candidates[path] = source
        applied_renames = set()
        for path, source in sorted(copy_candidates.items(), key=lambda pair: (pair[0].name, pair[1]["relative_path"])):
            check_cancelled(cancelled)
            directory = work / "selected" / _identity(_source_key(source))
            copied = get_file(path.parent, path.name).move_to(directory)
            rename_key = source["relative_path"] if source["relative_path"] in rename_map else path.name
            if rename_key in rename_map:
                copied = copied.rename(rename_map[rename_key])
                applied_renames.add(rename_key)
            updated = dict(source, path=str(copied.path))
            candidates.pop(path, None)
            candidates[copied.path] = updated
        if set(rename_map) - applied_renames:
            raise ValueError("Rename rules did not match selected files: " + ", ".join(sorted(set(rename_map) - applied_renames)))
        def order_key(pair: tuple[Path, dict]) -> tuple:
            path, source = pair
            stable = (source["relative_path"], _source_key(source))
            if effective_mode == "generic":
                return (path.name, *stable)
            name = source["original_name"]
            if source["log_domain"] == "kernel":
                match = kernel_name.fullmatch(name)
                return (1, 1 if match.group(1) is None else 0,
                        0 if match.group(1) is None else int(match.group(1)), *stable)
            return (0, 1 if name == "diag.log" else 0, name, *stable)

        ordered = sorted(candidates.items(), key=order_key)
        sources = []
        for rank, (path, source) in enumerate(ordered):
            source = dict(source, source_rank=rank, source_id="source-" + _identity(_source_key(source)))
            sources.append(source)
        manifest["sources"] = sources
        manifest["matched_files"] = len(sources)
        manifest["matched_archives"] = len(archives)
        manifest["expanded_files"] = budget_files
        manifest["expanded_bytes"] = budget_bytes
        shard_dir = batch_dir / ".shards"
        shard_dir.mkdir()
        shards = [shard_dir / f"{rank:08d}.sqlite3" for rank in range(len(sources))]
        emit("parsing", 0, f"Parsing {len(sources)} files with up to {workers} workers")
        if len(sources) <= 1 or workers == 1:
            for rank, (source, shard) in enumerate(zip(sources, shards), 1):
                parse_file(Path(source["path"]), shard, source, cancelled)
                emit("parsing", rank, "Parsed source file")
        else:
            stop_workers = threading.Event()

            def worker_cancelled() -> bool:
                return stop_workers.is_set() or bool(cancelled and cancelled())

            with ThreadPoolExecutor(max_workers=min(workers, len(sources)), thread_name_prefix="product-log") as executor:
                futures = [executor.submit(parse_file, Path(source["path"]), shard, source, worker_cancelled)
                           for source, shard in zip(sources, shards)]
                try:
                    for rank, future in enumerate(as_completed(futures), 1):
                        future.result()
                        emit("parsing", rank, "Parsed source file")
                except BaseException:
                    stop_workers.set()
                    for future in futures:
                        future.cancel()
                    raise
        emit("indexing", 0, "Assembling firmware dumps in file-name order")
        manifest["counts"] = build_index(batch_dir, shards, cancelled=cancelled, progress=progress)
        check_cancelled(cancelled)
        shutil.rmtree(shard_dir)
        manifest["duration_seconds"] = round(time.monotonic() - started, 3)
        manifest["status"] = "ready"
        write_manifest(batch_dir / "manifest.json", manifest)
        if progress:
            progress({"stage": "ready", "count": len(sources), "message": "Product-log analysis ready"})
        return manifest
    except BaseException as error:
        manifest["status"] = "cancelled" if isinstance(error, AnalysisCancelled) else "failed"
        manifest["error"] = str(error)
        manifest["duration_seconds"] = round(time.monotonic() - started, 3)
        write_manifest(batch_dir / "manifest.json", manifest)
        if progress:
            progress({"stage": manifest["status"], "count": 0, "message": str(error)})
        raise
