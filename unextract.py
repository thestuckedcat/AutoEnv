from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence


def find_git_sh() -> str:
    """查找 Git for Windows 的 sh.exe；Linux/macOS 环境回退到 PATH 中的 sh。"""
    env_path = os.environ.get("GIT_SH")
    candidates = [
        env_path,
        shutil.which("sh.exe"),
        shutil.which("sh"),
        r"C:\Program Files\Git\bin\sh.exe",
        r"C:\Program Files\Git\usr\bin\sh.exe",
        r"C:\Program Files (x86)\Git\bin\sh.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise FileNotFoundError("找不到 Git sh.exe 或 PATH 中的 sh，请安装 Git 或设置 GIT_SH")


def _quote(path: str | os.PathLike[str]) -> str:
    return "'" + str(path).replace("'", "'\\''") + "'"


def _run_shell(command: str, *, cwd: str | os.PathLike[str] | None = None) -> subprocess.CompletedProcess[str]:
    sh = find_git_sh()
    return subprocess.run(
        [sh, "-c", command],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def unextract_run(run_file: str, runtime_dir: str, *, tmp_name: str = "run_tmp") -> str:
    """执行 `sh xxx.run --noexec --extract=<runtime>/run_tmp` 并返回解压目录。"""
    runtime = Path(runtime_dir)
    extract_dir = runtime / tmp_name
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    command = f"sh {_quote(Path(run_file).resolve())} --noexec --extract={_quote(extract_dir.resolve())}"
    _run_shell(command)
    return str(extract_dir)


def unextract_tar_gz(tar_gz_file: str, runtime_dir: str, *, tmp_name: str = "tar_tmp") -> str:
    """通过 Git sh 解压 tar.gz 到 `<runtime>/tar_tmp` 并返回解压目录。"""
    runtime = Path(runtime_dir)
    extract_dir = runtime / tmp_name
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    command = f"tar -xzf {_quote(Path(tar_gz_file).resolve())} -C {_quote(extract_dir.resolve())}"
    _run_shell(command)
    return str(extract_dir)


def _normalize_targets(target_files: str | Sequence[str]) -> list[str]:
    if isinstance(target_files, str):
        return [target_files]
    return [str(item) for item in target_files]


def extract_target_files(package_path: str, runtime_dir: str, target_files: str | Sequence[str]) -> list[str]:
    """解压包并提取指定文件到 runtime 目录，最后删除临时目录。"""
    package = Path(package_path)
    targets = _normalize_targets(target_files)
    if not targets:
        return []

    if package.name.endswith(".run"):
        tmp_dir = Path(unextract_run(str(package), runtime_dir))
    elif package.name.endswith(".tar.gz") or package.name.endswith(".tgz"):
        tmp_dir = Path(unextract_tar_gz(str(package), runtime_dir))
    else:
        raise ValueError(f"不支持的解压包类型: {package.name}")

    copied: list[str] = []
    runtime = Path(runtime_dir)
    try:
        for target in targets:
            direct_match = tmp_dir / target
            if direct_match.exists():
                matches = [direct_match]
            else:
                matches = list(tmp_dir.rglob(target))
            if not matches:
                raise FileNotFoundError(f"在 {tmp_dir} 中找不到目标文件: {target}")
            for match in matches:
                destination = runtime / match.name
                if match.is_dir():
                    if destination.exists():
                        shutil.rmtree(destination)
                    shutil.copytree(match, destination)
                else:
                    shutil.copy2(match, destination)
                copied.append(str(destination))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return copied
