from __future__ import annotations

import filecmp
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import Callable, Sequence, TypeAlias


@dataclass(frozen=True)
class PackageSelector:
    config_name: str


@dataclass(frozen=True)
class ExtraFileSelector:
    filename: str


@dataclass(frozen=True)
class MatchSelector:
    pattern: str
    search_path: Path | None = None


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


MatchChooser: TypeAlias = Callable[[MatchSelector, Path, Sequence[Path]], Path]


def match(
    pattern: str,
    search_path: str | Path | None = None,
) -> MatchSelector:
    normalized = _required_text(pattern, "match pattern")
    re.compile(normalized)
    normalized_search_path = (
        None
        if search_path is None
        else Path(_required_path_text(search_path, "match search_path"))
    )
    return MatchSelector(normalized, normalized_search_path)


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
    *,
    match_chooser: MatchChooser | None = None,
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
        search_root = (
            package_root
            if selector.search_path is None
            else selector.search_path.expanduser().resolve()
        )
        if not search_root.is_dir():
            raise SelectorResolutionError(
                "MATCH_SEARCH_PATH_NOT_FOUND",
                f"match search path does not exist or is not a directory: {search_root}",
            )
        matches = _matching_files(search_root, regex)
        if not matches:
            raise SelectorResolutionError(
                "LOCAL_FILE_NOT_FOUND",
                f"no file in {search_root} matched regular expression {selector.pattern!r}",
            )
        selected = matches[0]
        if len(matches) > 1:
            if match_chooser is None:
                names = ", ".join(item.name for item in matches)
                raise SelectorResolutionError(
                    "AMBIGUOUS_LOCAL_FILE",
                    f"multiple files matched regular expression {selector.pattern!r}: {names}",
                )
            selected = match_chooser(selector, search_root, matches).resolve()
            if selected not in matches:
                raise ValueError("match chooser returned a file outside the candidate list")
        if search_root != package_root:
            selected = _copy_to_package_root(selected, package_root)
        return ResolvedLocalFile(
            selected, "match", selector.pattern, pattern=selector.pattern
        )

    raise TypeError(f"unsupported local file selector: {type(selector)!r}")


def validate_archive_target(value: str, label: str) -> str:
    normalized = _required_text(value, label).replace("\\", "/")
    path = PurePath(normalized)
    windows_path = PureWindowsPath(normalized)
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError(f"{label} must be relative to the archive root")
    if ".." in path.parts:
        raise ValueError(f"{label} must not contain '..'")
    return normalized.strip("/")


def _validate_root_filename(filename: str) -> None:
    path = PurePath(filename)
    windows_path = PureWindowsPath(filename)
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ValueError("extra_file() does not accept absolute paths")
    if ".." in path.parts or ".." in windows_path.parts:
        raise ValueError("extra_file() does not allow '..'")
    if (
        len(path.parts) != 1
        or len(windows_path.parts) != 1
        or path.name != filename
        or windows_path.name != filename
    ):
        raise ValueError("extra_file() accepts a filename in the packages root only")


def _required_path_text(value: str | Path, label: str) -> str:
    if isinstance(value, Path):
        value = str(value)
    return _required_text(value, label)


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
                f"matched file resolves outside the search directory: {item}",
            ) from exc
    return sorted(matches, key=lambda item: (item.name.lower(), item.name))


def _copy_to_package_root(source: Path, package_root: Path) -> Path:
    destination = _inside_package_root(package_root, package_root / source.name)
    if destination.exists() or destination.is_symlink():
        if not destination.is_file():
            raise SelectorResolutionError(
                "LOCAL_FILE_COPY_CONFLICT",
                f"match destination is not a regular file: {destination}",
            )
        if filecmp.cmp(source, destination, shallow=False):
            return destination
        raise SelectorResolutionError(
            "LOCAL_FILE_COPY_CONFLICT",
            f"a different file already exists in the packages directory: {destination}",
        )

    temporary = destination.with_name(destination.name + ".match.part")
    try:
        temporary.unlink(missing_ok=True)
        shutil.copy2(source, temporary)
        temporary.replace(destination)
    except OSError as exc:
        raise SelectorResolutionError(
            "LOCAL_FILE_COPY_FAILED",
            f"failed to copy matched file {source} to {destination}: {exc}",
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return destination
