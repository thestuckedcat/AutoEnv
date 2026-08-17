from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path


TEXT_SUFFIXES = {"", ".err", ".log", ".out", ".text", ".trace", ".txt"}
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "latin-1")


def _directory_candidate(path: Path) -> bool:
    name = path.name.lower()
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    return name.endswith(".log.gz") or name.endswith(".txt.gz") or name.endswith(".trace.gz")


def _iter_files(inputs: list[Path]) -> tuple[list[Path], list[dict[str, str]]]:
    files: dict[Path, None] = {}
    skipped: list[dict[str, str]] = []
    for value in inputs:
        path = value.expanduser().resolve()
        if not path.exists():
            skipped.append({"path": str(path), "reason": "not_found"})
        elif path.is_file():
            files[path] = None
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if (
                    candidate.is_file()
                    and not any(part.startswith(".") for part in candidate.relative_to(path).parts)
                    and _directory_candidate(candidate)
                ):
                    files[candidate.resolve()] = None
        else:
            skipped.append({"path": str(path), "reason": "not_a_regular_file_or_directory"})
    ordered = sorted(files, key=lambda item: (str(item).casefold(), str(item)))
    return ordered, skipped


def _read_limited(path: Path, max_bytes: int) -> tuple[bytes, str | None]:
    try:
        if path.name.lower().endswith(".gz"):
            with gzip.open(path, "rb") as handle:
                payload = handle.read(max_bytes + 1)
        else:
            with path.open("rb") as handle:
                payload = handle.read(max_bytes + 1)
    except (OSError, EOFError) as exc:
        return b"", f"read_failed: {type(exc).__name__}: {exc}"
    if len(payload) > max_bytes:
        return b"", f"decoded_content_exceeds_{max_bytes}_bytes"
    return payload, None


def _decode(payload: bytes) -> tuple[str, str]:
    for encoding in ENCODINGS:
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decoding must always succeed")


def extract(
    inputs: list[Path],
    *,
    tag: str = "[ERROR]",
    context: int = 4,
    ignore_case: bool = False,
    max_bytes: int = 256 * 1024 * 1024,
) -> dict[str, object]:
    if not tag:
        raise ValueError("tag must not be empty")
    if context < 0 or context > 100:
        raise ValueError("context must be between 0 and 100")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    files, skipped = _iter_files(inputs)
    matches: list[dict[str, object]] = []
    wanted = tag.casefold() if ignore_case else tag
    scanned = 0
    for path in files:
        payload, error = _read_limited(path, max_bytes)
        if error:
            skipped.append({"path": str(path), "reason": error})
            continue
        text, encoding = _decode(payload)
        lines = text.splitlines()
        scanned += 1
        for index, line in enumerate(lines):
            candidate = line.casefold() if ignore_case else line
            if wanted not in candidate:
                continue
            start = max(0, index - context)
            end = min(len(lines), index + context + 1)
            matches.append(
                {
                    "path": str(path),
                    "line": index + 1,
                    "text": line,
                    "encoding": encoding,
                    "before": [
                        {"line": line_index + 1, "text": lines[line_index]}
                        for line_index in range(start, index)
                    ],
                    "after": [
                        {"line": line_index + 1, "text": lines[line_index]}
                        for line_index in range(index + 1, end)
                    ],
                }
            )
    return {
        "tag": tag,
        "ignore_case": ignore_case,
        "context": context,
        "files_scanned": scanned,
        "match_count": len(matches),
        "matches": matches,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract [ERROR] lines and surrounding context from local logs."
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--tag", default="[ERROR]")
    parser.add_argument("--context", type=int, default=4)
    parser.add_argument("--ignore-case", action="store_true")
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = extract(
            args.paths,
            tag=args.tag,
            context=args.context,
            ignore_case=args.ignore_case,
            max_bytes=args.max_bytes,
        )
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(output)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
