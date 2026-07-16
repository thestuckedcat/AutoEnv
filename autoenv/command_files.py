from __future__ import annotations

from pathlib import Path, PurePath, PurePosixPath
from threading import RLock


class UploadedFileRegistry:
    """Resolve command placeholders from files uploaded in the current run."""

    def __init__(self) -> None:
        self._files: dict[str, dict[str | None, str]] = {}
        self._lock = RLock()

    def record(
        self,
        selector: str,
        remote_file: str,
        *,
        target_name: str | None = None,
    ) -> None:
        selector = _required_text(selector, "uploaded file selector")
        remote_file = _required_text(remote_file, "uploaded remote file")
        target = _target_name(target_name)
        actual_name = PurePosixPath(remote_file.replace("\\", "/")).name
        if not actual_name:
            raise ValueError("uploaded remote file must contain a filename")

        with self._lock:
            targets = self._files.setdefault(selector, {})
            previous = targets.get(target)
            if previous is not None and previous != actual_name:
                raise ValueError(
                    f"uploaded file selector {selector!r} is ambiguous on target "
                    f"{_target_label(target)}: "
                    f"{previous!r} and {actual_name!r}"
                )
            targets[target] = actual_name

    def resolve(self, command: str, *, target_name: str | None = None) -> str:
        command = _required_text(command, "command")
        return self._resolve_placeholders(
            command,
            target_name=target_name,
            require_single_target=target_name is None,
        )

    def resolve_script(self, script: str) -> str:
        if not isinstance(script, str):
            raise TypeError("shell script must be a string")
        if not script.strip():
            raise ValueError("shell script must not be empty")
        return self._resolve_placeholders(
            script,
            target_name=None,
            require_single_target=False,
        )

    def _resolve_placeholders(
        self,
        value: str,
        *,
        target_name: str | None,
        require_single_target: bool,
    ) -> str:
        with self._lock:
            files = {selector: dict(targets) for selector, targets in self._files.items()}

        resolved = value
        referenced = [
            selector
            for selector in sorted(files, key=len, reverse=True)
            if f"S{{{selector}}}" in resolved
        ]
        replacements = self._replacements_for(
            referenced,
            files,
            target_name=target_name,
            require_single_target=require_single_target,
        )
        for selector, actual_name in replacements.items():
            resolved = resolved.replace(f"S{{{selector}}}", actual_name)

        placeholder_start = resolved.find("S{")
        if placeholder_start >= 0:
            placeholder_end = resolved.find("}", placeholder_start + 2)
            if placeholder_end < 0 or placeholder_end == placeholder_start + 2:
                raise ValueError(
                    f"text contains a malformed S{{file_name}} placeholder: {value!r}"
                )
            selector = resolved[placeholder_start + 2 : placeholder_end]
            if "{" in selector or "}" in selector:
                raise ValueError(
                    f"text contains a malformed S{{file_name}} placeholder: {value!r}"
                )
            raise ValueError(
                f"placeholder S{{{selector}}} does not reference a "
                "successfully uploaded file"
            )
        return resolved

    @staticmethod
    def _replacements_for(
        referenced: list[str],
        files: dict[str, dict[str | None, str]],
        *,
        target_name: str | None,
        require_single_target: bool,
    ) -> dict[str, str]:
        if not referenced:
            return {}
        target = _target_name(target_name)
        if target is not None:
            replacements: dict[str, str] = {}
            for selector in referenced:
                targets = files[selector]
                if target not in targets:
                    raise ValueError(
                        f"placeholder S{{{selector}}} does not reference a file "
                        f"successfully uploaded to target {target!r}"
                    )
                replacements[selector] = targets[target]
            return replacements

        common_targets: set[str | None] | None = None
        for selector in referenced:
            selector_targets = set(files[selector])
            common_targets = (
                selector_targets
                if common_targets is None
                else common_targets & selector_targets
            )
        if not common_targets:
            names = ", ".join(f"S{{{selector}}}" for selector in referenced)
            raise ValueError(
                f"placeholders {names} were not successfully uploaded to a common target"
            )
        if require_single_target and len(common_targets) != 1:
            choices = ", ".join(
                _target_label(item) for item in sorted(common_targets, key=lambda item: item or "")
            )
            raise ValueError(
                "command placeholder upload target is ambiguous; configure the source "
                f"SSH host explicitly (candidates: {choices})"
            )

        replacements = {}
        for selector in referenced:
            actual_names = {files[selector][item] for item in common_targets}
            if len(actual_names) != 1:
                raise ValueError(
                    f"placeholder S{{{selector}}} resolves to different filenames across "
                    "the common upload targets"
                )
            replacements[selector] = actual_names.pop()
        return replacements


def generate_sh_file(
    file_name: str,
    script: str,
    *,
    output_dir: str | Path = ".",
    uploaded_files: UploadedFileRegistry | None = None,
) -> Path:
    """Write a complete shell script after resolving uploaded-file placeholders."""

    file_name = _root_file_name(file_name)
    if not file_name.endswith(".sh"):
        raise ValueError("generated shell filename must end with '.sh'")
    if uploaded_files is not None and not isinstance(uploaded_files, UploadedFileRegistry):
        raise TypeError("uploaded_files must be UploadedFileRegistry")

    resolver = uploaded_files or UploadedFileRegistry()
    resolved_script = resolver.resolve_script(script)

    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / file_name
    destination.write_text(
        resolved_script,
        encoding="utf-8",
        newline="",
    )
    try:
        destination.chmod(destination.stat().st_mode | 0o111)
    except OSError:
        # Windows filesystems may not expose executable mode bits.
        pass
    return destination


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _target_name(value: str | None) -> str | None:
    if value is None:
        return None
    return _required_text(value, "upload target name")


def _target_label(value: str | None) -> str:
    return "<unscoped>" if value is None else repr(value)


def _root_file_name(value: str) -> str:
    normalized = _required_text(value, "shell filename")
    path = PurePath(normalized)
    if path.is_absolute() or ":" in normalized:
        raise ValueError("shell filename must not be an absolute path")
    if ".." in path.parts or len(path.parts) != 1 or path.name != normalized:
        raise ValueError("shell filename must be a filename in the output directory")
    return normalized
