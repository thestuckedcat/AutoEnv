from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class FileEntry:
    name: str
    is_directory: bool
    length: int
    modification_time: Optional[datetime]
    full_path: str


@dataclass
class ImageSpec:
    name: str
    link: str
    image_name: str
    base_link: str = ""


@dataclass
class EnvironmentSpec:
    env_name: str
    image_vars: Dict[str, str]
    script_template: str


@dataclass
class DownloadedImage:
    var_name: str
    spec_name: str
    real_name: str = ""
    local_path: str = ""


@dataclass
class RuntimeContext:
    selected_env: EnvironmentSpec
    downloaded_images: List[DownloadedImage] = field(default_factory=list)
