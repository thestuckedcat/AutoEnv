"""Local A/B workflow built on AutoEnv file handlers, with no remote sessions."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

from autoenv import find_subdir, fuzz_search, get_file, get_handler
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


def _search(root: Path, patterns: list[str], recursive: bool) -> list[Path]:
    if not patterns:
        return list(get_handler(root, recursive).paths)
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(fuzz_search(root, pattern, recursive).paths)
    return sorted(paths, key=lambda path: (path.name, path.relative_to(root).as_posix()))


def _identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _source_key(source: dict) -> str:
    """Keep archive boundaries structured; literal '!/' in a name is valid."""
    return json.dumps(source["provenance_parts"], ensure_ascii=False, separators=(",", ":"))


def run_analysis(input_path: str | Path, batch_dir: str | Path, *,
                 directory_pattern: str = "", archive_patterns: Iterable[str] | None = None,
                 file_patterns: Iterable[str] | None = None, recursive: bool = True,
                 collection_mode: str = "auto", diag_directory_pattern: str = "diag",
                 var_directory_pattern: str = "var",
                 copy_patterns: Iterable[str] | None = None, rename_map: dict[str, str] | None = None,
                 workers: int = 4, progress: Callable[[dict], None] | None = None,
                 cancelled: Callable[[], bool] | None = None) -> dict:
    """Prepare, parse and atomically publish a single isolated batch.

    Generic/diag child archives are expanded once; product var expands every
    nested layer. Auto detects product directories, retaining explicit generic
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

    def external_generated(path: Path) -> bool:
        return input_path.is_dir() and path.is_relative_to(batch_dir)

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
                raise ValueError("File input must be a supported ZIP/TAR/GZIP archive")
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
        emit("collecting", 0, "Locating the unique target directory")
        excluded = (batch_dir,) if input_path.is_dir() else ()
        targets: dict[str, Path] = {}
        effective_mode = collection_mode
        if collection_mode != "generic" and not (collection_mode == "auto" and directory_pattern):
            for domain, pattern in (("user", diag_directory_pattern), ("kernel", var_directory_pattern)):
                # SDK single-component keywords intentionally use substring matching.
                # A character-class glob keeps the default product directory names
                # exact while retaining the SDK's safe traversal and unique-match rule.
                directory_rule = {"diag": "[d]iag", "var": "[v]ar"}.get(pattern, pattern)
                try:
                    targets[domain] = find_subdir(input_root, directory_rule, recursive=True, exclude_paths=excluded)
                except FileNotFoundError:
                    pass
            if not targets and collection_mode == "product":
                raise FileNotFoundError("No diag or var directory matches the product collection rules")
            effective_mode = "product" if targets else "generic"
        else:
            effective_mode = "generic"
        if effective_mode == "generic":
            targets = {"user": find_subdir(input_root, directory_pattern, recursive=bool(recursive),
                                           exclude_paths=excluded) if directory_pattern else input_root}
        elif targets.get("user") == targets.get("kernel"):
            raise ValueError("diag and var directory rules must select different directories")
        manifest["collection_mode"] = effective_mode
        manifest["target_paths"] = {domain: str(target) for domain, target in targets.items()}
        manifest["target_path"] = str(targets.get("user", next(iter(targets.values()))))

        candidates: dict[Path, dict] = {}
        copy_candidates: dict[Path, dict] = {}
        archives: list[tuple[Path, str, dict, int]] = []
        kernel_name = re.compile(r"^cpdt_dmesg_current\.log(?:\.(\d+))?$")

        def collect_target(target: Path, domain: str) -> None:
            is_kernel = effective_mode == "product" and domain == "kernel"
            walk_recursive = True if is_kernel else bool(recursive)
            selected_patterns = file_patterns or (["diaglog_*.log", "diag.log"] if effective_mode == "product" else [])
            device_source = "target:" + target.relative_to(input_root).as_posix()
            other_targets = tuple(other for other_domain, other in targets.items()
                                  if other_domain != domain and other.is_relative_to(target))

            def initial_excluded(path: Path) -> bool:
                # Both domains may be nested. The explicitly selected child
                # domain owns its entire subtree, including every archive.
                return external_generated(path) or any(path.is_relative_to(other) for other in other_targets)

            def source_for(path: Path, root: Path, original: dict | None = None) -> dict:
                member = path.relative_to(root).as_posix()
                relative = original["relative_path"] + "!/" + member if original else path.relative_to(input_root).as_posix()
                return {
                    "path": str(path), "relative_path": relative,
                    "provenance_parts": [*original["provenance_parts"], member] if original else [relative],
                    "origin_path": original["origin_path"] + "!/" + member if original else (
                        f"{top_archive}!/{relative}" if top_archive else str(path)),
                    "archive_path": original["path"] if original else top_archive,
                    "member_path": member if original else (relative if top_archive else ""),
                    "device_source": device_source, "log_domain": domain,
                    "original_name": path.name,
                }

            def scan(root: Path, original: dict | None, level: int, discover_archives: bool) -> None:
                detected_paths: dict[Path, str | None] = {}

                def kind_for(path: Path) -> str | None:
                    check_cancelled(cancelled)
                    if path not in detected_paths:
                        detected_paths[path] = detect_archive(path)
                    return detected_paths[path]

                if discover_archives:
                    # var must discover every nested archive, independent of diag pattern choices.
                    for path in _search(root, [] if is_kernel else archive_patterns, walk_recursive):
                        if original is None and initial_excluded(path):
                            continue
                        kind = kind_for(path)
                        if kind:
                            archives.append((path, kind, source_for(path, root, original), level))
                selected = _search(root, [] if is_kernel else selected_patterns, walk_recursive)
                if is_kernel:
                    selected = [path for path in selected if kernel_name.fullmatch(path.name)]
                elif effective_mode == "product" and not file_patterns:
                    selected = [path for path in selected if path.name == "diag.log" or (
                        path.name.startswith("diaglog_") and path.name.endswith(".log"))]
                copies = _search(root, copy_patterns, walk_recursive) if copy_patterns and not is_kernel else []
                for path in set(selected + copies):
                    if (original is None and initial_excluded(path)) or kind_for(path):
                        continue
                    source = source_for(path, root, original)
                    if path in selected:
                        candidates[path] = source
                    if path in copies:
                        copy_candidates[path] = source

            queue_start = len(archives)
            scan(target, None, 1, True)
            cursor = queue_start
            while cursor < len(archives):
                path, kind, original, level = archives[cursor]
                cursor += 1
                destination = work / "expanded" / _identity(_source_key(original))
                emit("extracting", cursor, f"Extracting {original['relative_path']}")
                if budget_files >= MAX_EXPANDED_FILES or budget_bytes >= MAX_EXPANDED_BYTES:
                    raise ValueError("Task cumulative archive expansion budget exhausted")
                expanded = get_file(path.parent, path.name).unzip(
                    destination, max_files=MAX_EXPANDED_FILES - budget_files,
                    max_bytes=MAX_EXPANDED_BYTES - budget_bytes,
                    check_cancelled=lambda: check_cancelled(cancelled),
                )
                check_cancelled(cancelled)
                account(expanded)
                manifest["archives"].append({"path": str(path), "origin_path": original["origin_path"],
                                              "destination": str(expanded), "format": kind, "level": level,
                                              "log_domain": domain,
                                              "provenance_parts": original["provenance_parts"]})
                scan(expanded, original, level + 1, is_kernel)

        for domain, target in targets.items():
            collect_target(target, domain)
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
