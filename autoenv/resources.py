from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceLabel:
    label: str
    kind: str


RESOURCE_LABELS: tuple[ResourceLabel, ...] = (
    ResourceLabel("1260网口", "network"),
    ResourceLabel("1260串口", "serial"),
    ResourceLabel("1712网口", "network"),
    ResourceLabel("1712串口", "serial"),
    ResourceLabel("udie1网口", "network"),
    ResourceLabel("udie1串口", "serial"),
)

_BY_LABEL = {item.label: item for item in RESOURCE_LABELS}


def get_resource_label(label: object) -> ResourceLabel:
    if not isinstance(label, str) or not label.strip():
        raise ValueError("resource_label must be a non-empty string")
    try:
        return _BY_LABEL[label.strip()]
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
    return [{"label": item.label, "kind": item.kind} for item in RESOURCE_LABELS]
