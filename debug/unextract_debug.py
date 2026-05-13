from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from unextract import extract_target_files, unextract_run, unextract_tar_gz


def debug_unextract_run(run_file: str, runtime_dir: str, *, tmp_name: str = "run_tmp") -> str:
    """调试接口：解压 .run 包到 runtime 下的临时目录。"""
    return unextract_run(run_file, runtime_dir, tmp_name=tmp_name)


def debug_unextract_tar_gz(tar_gz_file: str, runtime_dir: str, *, tmp_name: str = "tar_tmp") -> str:
    """调试接口：解压 .tar.gz/.tgz 包到 runtime 下的临时目录。"""
    return unextract_tar_gz(tar_gz_file, runtime_dir, tmp_name=tmp_name)


def debug_extract_target_files(package_path: str, runtime_dir: str, target_files: Sequence[str]) -> list[str]:
    """调试接口：解包并提取目标文件到 runtime 目录，然后删除临时目录。"""
    return extract_target_files(package_path, runtime_dir, target_files)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="调试 .run/.tar.gz 解包与 target_file 提取")
    subparsers = parser.add_subparsers(dest="action", required=True)

    run_parser = subparsers.add_parser("run", help="解压 .run 包")
    run_parser.add_argument("package", help=".run 包路径")
    run_parser.add_argument("runtime_dir", help="runtime 目录")
    run_parser.add_argument("--tmp-name", default="run_tmp", help="临时目录名，默认 run_tmp")

    tar_parser = subparsers.add_parser("tar-gz", help="解压 .tar.gz/.tgz 包")
    tar_parser.add_argument("package", help=".tar.gz/.tgz 包路径")
    tar_parser.add_argument("runtime_dir", help="runtime 目录")
    tar_parser.add_argument("--tmp-name", default="tar_tmp", help="临时目录名，默认 tar_tmp")

    target_parser = subparsers.add_parser("target", help="解包并提取 target_file")
    target_parser.add_argument("package", help=".run/.tar.gz/.tgz 包路径")
    target_parser.add_argument("runtime_dir", help="runtime 目录")
    target_parser.add_argument("target_files", nargs="+", help="要提取的文件名或相对路径")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.action == "run":
        output = debug_unextract_run(args.package, args.runtime_dir, tmp_name=args.tmp_name)
        print(output)
    elif args.action == "tar-gz":
        output = debug_unextract_tar_gz(args.package, args.runtime_dir, tmp_name=args.tmp_name)
        print(output)
    elif args.action == "target":
        outputs = debug_extract_target_files(args.package, args.runtime_dir, args.target_files)
        for output in outputs:
            print(output)


if __name__ == "__main__":
    main()
