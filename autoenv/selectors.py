from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Callable, TypeAlias


@dataclass(frozen=True)
class PackageSelector:
    config_name: str


@dataclass(frozen=True)
class ExtraFileSelector:
    filename: str


@dataclass(frozen=True)
class MatchSelector:
    pattern: str


LocalFileSelector: TypeAlias = PackageSelector | ExtraFileSelector | MatchSelector


@dataclass(frozen=True)
class ResolvedLocalFile:
    path: Path
    selector_type: str
    selector: str
    pattern: str | None = None


class SelectorResolutionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def package(name: str) -> PackageSelector:
    return PackageSelector(_required_text(name, "package name"))


def extra_file(filename: str) -> ExtraFileSelector:
    normalized = _required_text(filename, "extra filename")
    _validate_root_filename(normalized)
    return ExtraFileSelector(normalized)


def match(pattern: str) -> MatchSelector:
    normalized = _required_text(pattern, "match pattern")
    re.compile(normalized)
    return MatchSelector(normalized)


def describe_selector(selector: LocalFileSelector) -> tuple[str, str]:
    if isinstance(selector, PackageSelector):
        return "package", selector.config_name
    if isinstance(selector, ExtraFileSelector):
        return "extra_file", selector.filename
    if isinstance(selector, MatchSelector):
        return "match", selector.pattern
    raise TypeError(f"unsupported local file selector: {type(selector)!r}")


def resolve_local_file(
    selector: LocalFileSelector,
    package_dir: Path,
    image_pattern_for: Callable[[str], str],
) -> ResolvedLocalFile:
    package_root = package_dir.resolve()
    if not package_root.is_dir():
        raise SelectorResolutionError(
            "PACKAGE_DIR_NOT_FOUND", f"package directory does not exist: {package_root}"
        )

    if isinstance(selector, ExtraFileSelector):
        _validate_root_filename(selector.filename)
        candidate = _inside_package_root(package_root, package_root / selector.filename)
        if not candidate.is_file():
            raise SelectorResolutionError(
                "LOCAL_FILE_NOT_FOUND",
                f"local file {selector.filename!r} was not found in {package_root}",
            )
        return ResolvedLocalFile(candidate, "extra_file", selector.filename)

    if isinstance(selector, PackageSelector):
        pattern = image_pattern_for(selector.config_name)
        regex = re.compile(pattern)
        matches = _matching_files(package_root, regex)
        if not matches:
            raise SelectorResolutionError(
                "LOCAL_FILE_NOT_FOUND",
                f"no file in {package_root} matched package {selector.config_name!r} ({pattern})",
            )
        if len(matches) > 1:
            names = ", ".join(item.name for item in matches)
            raise SelectorResolutionError(
                "AMBIGUOUS_LOCAL_FILE",
                f"multiple files matched package {selector.config_name!r}: {names}",
            )
        return ResolvedLocalFile(
            matches[0], "package", selector.config_name, pattern=pattern
        )

    if isinstance(selector, MatchSelector):
        regex = re.compile(selector.pattern)
        matches = _matching_files(package_root, regex)
        if not matches:
            raise SelectorResolutionError(
                "LOCAL_FILE_NOT_FOUND",
                f"no file in {package_root} matched regular expression {selector.pattern!r}",
            )
        return ResolvedLocalFile(
            matches[0], "match", selector.pattern, pattern=selector.pattern
        )

    raise TypeError(f"unsupported local file selector: {type(selector)!r}")


def validate_archive_target(value: str, label: str) -> str:
    normalized = _required_text(value, label).replace("\\", "/")
    path = PurePath(normalized)
    if path.is_absolute() or normalized.startswith("/"):
        raise ValueError(f"{label} must be relative to the archive root")
    if ".." in path.parts:
        raise ValueError(f"{label} must not contain '..'")
    return normalized.strip("/")


def _validate_root_filename(filename: str) -> None:
    path = PurePath(filename)
    if path.is_absolute() or ":" in filename:
        raise ValueError("extra_file() does not accept absolute paths")
    if ".." in path.parts:
        raise ValueError("extra_file() does not allow '..'")
    if len(path.parts) != 1 or path.name != filename:
        raise ValueError("extra_file() accepts a filename in the packages root only")


def _inside_package_root(package_root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    try:
        resolved.relative_to(package_root)
    except ValueError as exc:
        raise ValueError("local file selector escapes the packages directory") from exc
    return resolved


def _matching_files(package_root: Path, regex: re.Pattern[str]) -> list[Path]:
    matches: list[Path] = []
    for item in package_root.iterdir():
        if not item.is_file() or item.name.endswith(".part") or not regex.search(item.name):
            continue
        try:
            matches.append(_inside_package_root(package_root, item))
        except ValueError as exc:
            raise SelectorResolutionError(
                "LOCAL_FILE_OUTSIDE_PACKAGE_DIR",
                f"matched file resolves outside the packages directory: {item}",
            ) from exc
    return sorted(matches, key=lambda item: (item.name.lower(), item.name))
