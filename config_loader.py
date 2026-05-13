import json
from typing import Dict

from models import ImageSpec


def load_image_specs(config_path: str = "config.json") -> Dict[str, ImageSpec]:
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("config.json 顶层必须是数组")

    specs: Dict[str, ImageSpec] = {}
    for item in data:
        name = str(item.get("name", "")).strip()
        link = str(item.get("link", "")).strip()
        image_name = str(item.get("image_name", "")).strip()
        base_link = str(item.get("base_link", "")).strip()
        raw_target_file = item.get("target_file", [])
        if isinstance(raw_target_file, str):
            target_file = [raw_target_file.strip()] if raw_target_file.strip() else []
        elif isinstance(raw_target_file, list):
            target_file = [str(value).strip() for value in raw_target_file if str(value).strip()]
        elif raw_target_file in (None, ""):
            target_file = []
        else:
            display_name = name or "<unknown>"
            raise ValueError(f"配置 {display_name} 的 target_file 必须是字符串或数组")

        if not name:
            raise ValueError("存在配置缺少 name")
        if not image_name:
            raise ValueError(f"配置 {name} 缺少 image_name")
        if name in specs:
            raise ValueError(f"name 重复：{name}")

        specs[name] = ImageSpec(
            name=name,
            link=link,
            image_name=image_name,
            base_link=base_link,
            target_file=target_file,
        )

    return specs
