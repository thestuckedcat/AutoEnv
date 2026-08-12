from __future__ import annotations

import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_FILES = 2000
MAX_BYTES = 128 * 1024 * 1024
DENIED = {".exe", ".dll", ".pyd", ".so", ".key", ".pem", ".env", ".pyc"}

def main() -> int:
    source, destination = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(); destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES or sum(item.file_size for item in members) > MAX_BYTES: raise ValueError("archive expansion exceeds safe limits")
        for item in members:
            p = PurePosixPath(item.filename.replace("\\", "/")); w = PureWindowsPath(item.filename); mode = (item.external_attr >> 16) & 0xFFFF
            if not item.filename or p.is_absolute() or w.is_absolute() or w.drive or ".." in p.parts: raise ValueError(f"unsafe archive path: {item.filename!r}")
            if mode and stat.S_ISLNK(mode): raise ValueError(f"archive link is not allowed: {item.filename!r}")
            if Path(item.filename).suffix.lower() in DENIED: raise ValueError(f"denied file type: {item.filename!r}")
            destination.joinpath(*p.parts).resolve().relative_to(destination)
        archive.extractall(destination)
    for path in sorted(destination.rglob("*")):
        if path.is_file(): print(path.relative_to(destination).as_posix())
    return 0

if __name__ == "__main__": raise SystemExit(main())
