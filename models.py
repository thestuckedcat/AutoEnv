from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


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
    target_file: List[str] = field(default_factory=list)


@dataclass
class ImageVarRef:
    """环境脚本变量到 config.json 包规则/目标文件的映射。"""

    spec_name: str
    target_file: str | List[str] | None = None


ImageVarInput = str | ImageVarRef | Sequence[str] | Dict[str, Any]
ProcessFunc = Callable[[Any], None]


@dataclass
class EnvironmentSpec:
    env_name: str
    image_vars: Dict[str, ImageVarInput]
    script_templates: Dict[str, str] = field(default_factory=dict)
    process: ProcessFunc | None = None
    telnet_commands: List[str] = field(default_factory=list)
    upload_protocol: str = "scp"
    ssh_defaults: Dict[str, str | int] = field(default_factory=dict)
    telnet_defaults: Dict[str, str | int | float] = field(default_factory=dict)
    ftp_defaults: Dict[str, str | int] = field(default_factory=dict)
    # 兼容旧注册方式：未迁移时仍可传入单个 script_template。
    script_template: str = ""

    def get_script_templates(self) -> Dict[str, str]:
        if self.script_templates:
            return self.script_templates
        if self.script_template:
            return {"main": self.script_template}
        return {}


@dataclass
class DownloadedImage:
    var_name: str
    spec_name: str
    real_name: str = ""
    local_path: str = ""
    target_file: str | List[str] | None = None
    extracted_paths: List[str] = field(default_factory=list)
    selected_local_path: str = ""
    selected_real_name: str = ""


@dataclass
class RuntimeContext:
    selected_env: EnvironmentSpec
    downloaded_images: List[DownloadedImage] = field(default_factory=list)


def normalize_image_var_ref(value: ImageVarInput) -> ImageVarRef:
    """把环境注册里的 image_vars 值统一转换成 ImageVarRef。

    支持形态：
    - "A1"：只引用 config.json 中 name=A1 的包；
    - ("A1", "file1") / ["A1", "file1"]：引用包并选择 target_file；
    - {"name": "A1", "target_file": "file1"}：显式字段写法。
    """
    if isinstance(value, ImageVarRef):
        return value
    if isinstance(value, str):
        return ImageVarRef(spec_name=value)
    if isinstance(value, dict):
        spec_name = str(value.get("name") or value.get("spec_name") or "").strip()
        if not spec_name:
            raise ValueError(f"image_vars 字典项缺少 name/spec_name: {value}")
        target_file = value.get("target_file")
        return ImageVarRef(spec_name=spec_name, target_file=target_file)
    if isinstance(value, Sequence):
        items = list(value)
        if len(items) == 1:
            return ImageVarRef(spec_name=str(items[0]).strip())
        if len(items) == 2:
            return ImageVarRef(spec_name=str(items[0]).strip(), target_file=items[1])
        raise ValueError(f"image_vars 序列项必须是 [name] 或 [name, target_file]: {value}")
    raise TypeError(f"不支持的 image_vars 配置类型: {type(value)!r}")
