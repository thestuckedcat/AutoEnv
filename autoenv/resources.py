from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ResourceLabel:
    label: str
    kind: str


RESOURCE_LABELS_PATH = Path(__file__).with_name("resource_labels.json")
RESOURCE_LABELS_SCHEMA_VERSION = 1
RESOURCE_LABEL_KINDS = frozenset({"network", "serial"})


def load_resource_labels(path: Path | str = RESOURCE_LABELS_PATH) -> tuple[ResourceLabel, ...]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"resource label catalog does not exist: {source}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"resource label catalog is not valid JSON: {source}: {exc}") from exc

    if not isinstance(value, dict):
        raise ValueError("resource label catalog must be a JSON object")
    if value.get("schema_version") != RESOURCE_LABELS_SCHEMA_VERSION:
        raise ValueError(
            "resource label catalog schema_version must be "
            f"{RESOURCE_LABELS_SCHEMA_VERSION}"
        )
    raw_labels = value.get("labels")
    if not isinstance(raw_labels, list) or not raw_labels:
        raise ValueError("resource label catalog labels must be a non-empty array")

    labels: list[ResourceLabel] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_labels):
        if not isinstance(raw, dict):
            raise ValueError(f"resource label catalog labels[{index}] must be an object")
        label = raw.get("label")
        kind = raw.get("kind")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                f"resource label catalog labels[{index}].label must be a non-empty string"
            )
        normalized_label = label.strip()
        if not isinstance(kind, str) or kind not in RESOURCE_LABEL_KINDS:
            raise ValueError(
                f"resource label catalog labels[{index}].kind must be network or serial"
            )
        if normalized_label in seen:
            raise ValueError(
                f"resource label catalog contains duplicate label: {normalized_label}"
            )
        seen.add(normalized_label)
        labels.append(ResourceLabel(normalized_label, kind))
    return tuple(labels)



def get_resource_label(label: object) -> ResourceLabel:
    if not isinstance(label, str) or not label.strip():
        raise ValueError("resource_label must be a non-empty string")
    by_label = {item.label: item for item in load_resource_labels()}
    try:
        return by_label[label.strip()]
    except KeyError as exc:
        raise ValueError(f"unknown resource_label: {label!r}") from exc


def validate_resource_label(label: object, *, protocol: str) -> str:
    resource = get_resource_label(label)
    expected_kind = "serial" if protocol == "telnet" else "network"
    if resource.kind != expected_kind:
        raise ValueError(
            f"resource_label {resource.label!r} is {resource.kind}, "
            f"not valid for {protocol}"
        )
    return resource.label


def describe_resource_labels() -> list[dict[str, str]]:
    return [{"label": item.label, "kind": item.kind} for item in load_resource_labels()]
