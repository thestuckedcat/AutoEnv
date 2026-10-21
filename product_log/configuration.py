"""Explicit, portable product-log source and parser configuration.

Defaults are data, not missing-rule fallbacks. Every API returns or consumes plain
JSON values so the identical snapshot can be shipped to the offline worker.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path, PureWindowsPath
import re


_TOP_KEYS = {"version", "sources", "parsers", "rules", "module_aliases", "firmware_types", "modules"}
_FIELDS = ("raw_text", "module_name", "log_type", "device_id", "slot_id", "cpu_id", "text")
_CATEGORIES = ("user_driver", "kernel_driver", "firmware")
_DIAG_PATTERNS = {"header": 2, "outer_shape": 0, "slot": 1, "cpu": 1, "module": 1,
                  "service": 1, "info": 1, "firmware": 4, "firmware_marker": 0, "uptime": 1}
_SPACE_CLASS = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_NONSPACE_CLASS = (r"\x00-\x08\x0e-\x1f!-\x9f\xa1-\u167f\u1681-\u1fff\u200b-\u2027"
                   r"\u202a-\u202e\u2030-\u205e\u2060-\u2fff\u3001-\ufefe\uff00-\U0010ffff")
_SPACE_CHARS = "\t\n\v\f\r \u00a0\u1680" + "".join(chr(value) for value in range(0x2000, 0x200b)) + "\u2028\u2029\u202f\u205f\u3000\ufeff"


def _portable_pattern(pattern: str) -> str:
    """Use ECMAScript Unicode whitespace while keeping ASCII digit/word sets."""
    output, in_class, cursor = [], False, 0
    while cursor < len(pattern):
        char = pattern[cursor]
        if char == "\\" and cursor + 1 < len(pattern):
            escaped = pattern[cursor + 1]
            if escaped in {"s", "S"}:
                contents = _SPACE_CLASS if escaped == "s" else _NONSPACE_CLASS
                output.append(contents if in_class else "[" + (_SPACE_CLASS if escaped == "s" else "^" + _SPACE_CLASS) + "]")
            else:
                output.append(pattern[cursor:cursor + 2])
            cursor += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        output.append(char)
        cursor += 1
    return "".join(output)


def _object(value, label, keys, required=()):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if set(value) - set(keys):
        raise ValueError(f"{label} has unknown fields: {', '.join(sorted(set(value) - set(keys)))}")
    missing = set(required) - set(value)
    if missing:
        raise ValueError(f"{label} is missing: {', '.join(sorted(missing))}")
    return value


def _text(value, label, *, empty=False):
    if not isinstance(value, str) or "\x00" in value or (not empty and not value.strip(_SPACE_CHARS)):
        raise ValueError(f"{label} must be {'a string' if empty else 'a nonempty string'}")
    return value


def _list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _strings(value, label, *, nonempty=False):
    values = _list(value, label)
    if nonempty and not values:
        raise ValueError(f"{label} must not be empty")
    for item in values:
        _text(item, label)
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must not contain duplicates")
    return values


@lru_cache(maxsize=512)
def _compiled(pattern: str, ignore_case: bool = False) -> re.Pattern:
    # This supported subset has the same meaning in Python and browser RegExp.
    if (re.search(r"\(\?(?![:=!])", pattern) or re.search(r"\\[AZzGKNpPuU]", pattern)
            or re.search(r"(?:[*+?]|\})\+", pattern)):
        raise ValueError("Regex must use portable numbered captures and no inline flags")
    cursor = 0
    while cursor < len(pattern):
        if pattern[cursor] == "\\" and cursor + 1 < len(pattern):
            char = pattern[cursor + 1]
            if char.isascii() and (char.isdigit() or char.isalpha() and char not in "bBdDsSwWfnrtvx"):
                raise ValueError("Regex contains a nonportable escape or backreference")
            if char == "x" and not re.fullmatch(r"[0-9a-fA-F]{2}", pattern[cursor + 2:cursor + 4]):
                raise ValueError("Regex contains an invalid hexadecimal escape")
            cursor += 2
        else:
            cursor += 1
    try:
        return re.compile(_portable_pattern(pattern), re.I | re.ASCII if ignore_case else re.ASCII)
    except re.error as error:
        raise ValueError(f"Invalid regex: {error}") from error


def compile_regex(pattern: str, ignore_case: bool = False) -> re.Pattern:
    """Compile the shared profile/rule regex subset with consistent whitespace."""
    return _compiled(pattern, ignore_case)


def _pattern(value, label, groups=0, *, empty=False):
    _text(value, label, empty=empty)
    if value or not empty:
        if _compiled(value).groups < groups:
            raise ValueError(f"{label} requires at least {groups} numbered capture groups")


@lru_cache(maxsize=1)
def _template():
    return json.loads(Path(__file__).with_name("default_config.json").read_text(encoding="utf-8"))


def default_config() -> dict:
    """Return a fresh copy of the explicit initial settings extracted from code."""
    return deepcopy(_template())


def normalize_config(value=None) -> dict:
    """Validate and clone an explicit snapshot; empty lists stay empty."""
    config = deepcopy(default_config() if value is None else value)
    _object(config, "analysis_config", _TOP_KEYS, _TOP_KEYS)
    if type(config["version"]) not in (int, float) or config["version"] != 1:
        raise ValueError("analysis_config version must be 1")
    parsers, sources, rules = {}, {}, {}
    for parser in _list(config["parsers"], "parsers"):
        keys = {"id", "engine", "time_format", "patterns", "months"}
        _object(parser, "parser", keys, keys)
        identity = _text(parser["id"], "parser.id")
        if identity in parsers:
            raise ValueError(f"Duplicate parser id: {identity}")
        if parser["engine"] not in ("diag", "kernel"):
            raise ValueError("parser.engine must be diag or kernel")
        if parser["time_format"] not in ("iso", "dmesg", "none"):
            raise ValueError("parser.time_format must be iso, dmesg or none")
        if parser["engine"] == "diag" and parser["time_format"] == "dmesg":
            raise ValueError("diag parsers require iso or none time_format")
        patterns = parser["patterns"]
        required = (_DIAG_PATTERNS if parser["engine"] == "diag" else
                    ({"date": 5 if parser["time_format"] == "dmesg" else 1} if parser["time_format"] != "none" else {}))
        _object(patterns, "parser.patterns", _DIAG_PATTERNS if parser["engine"] == "diag" else {"date"}, required)
        for key, expression in patterns.items():
            _pattern(expression, f"parser.patterns.{key}", required.get(key, 0))
        months = _strings(parser["months"], "parser.months")
        if parser["time_format"] == "dmesg" and (len(months) != 12 or len({month.lower() for month in months}) != 12):
            raise ValueError("dmesg parsers require twelve distinct month names")
        parsers[identity] = parser
    for source in _list(config["sources"], "sources"):
        keys = {"id", "name", "directory_patterns", "file_patterns", "file_regex", "parser_id", "recursive", "order"}
        _object(source, "source", keys, keys)
        identity = _text(source["id"], "source.id")
        _text(source["name"], "source.name")
        if identity in sources:
            raise ValueError(f"Duplicate source id: {identity}")
        if _text(source["parser_id"], "source.parser_id") not in parsers:
            raise ValueError(f"Unknown parser: {source['parser_id']}")
        for directory in _strings(source["directory_patterns"], "source.directory_patterns", nonempty=True):
            normalized = directory.replace("\\", "/")
            if (normalized.startswith("/") or PureWindowsPath(directory).drive or ":" in normalized
                    or any(part in {"", ".", ".."} or "**" in part for part in normalized.split("/"))):
                raise ValueError("source directory patterns must contain safe relative components")
        for pattern in _strings(source["file_patterns"], "source.file_patterns", nonempty=True):
            if "/" in pattern or "\\" in pattern:
                raise ValueError("source file patterns must match basenames")
        _pattern(source["file_regex"], "source.file_regex", empty=True)
        if type(source["recursive"]) is not bool:
            raise ValueError("source.recursive must be boolean")
        order = _object(source["order"], "source.order", {"type", "current", "pattern"}, {"type"})
        if order["type"] not in ("name", "current_last", "numeric_rotation"):
            raise ValueError("Unknown source order type")
        if "current" in order:
            _text(order["current"], "source.order.current", empty=True)
        if order["type"] == "current_last":
            _text(order.get("current"), "source.order.current")
        if "pattern" in order:
            _pattern(order["pattern"], "source.order.pattern", 1 if order["type"] == "numeric_rotation" else 0,
                     empty=order["type"] != "numeric_rotation")
        elif order["type"] == "numeric_rotation":
            raise ValueError("numeric_rotation requires order.pattern")
        sources[identity] = source
    for rule in _list(config["rules"], "rules"):
        keys = {"id", "category", "component", "source_ids", "match"}
        _object(rule, "rule", keys, keys)
        identity = _text(rule["id"], "rule.id")
        _text(rule["component"], "rule.component")
        if identity in rules:
            raise ValueError(f"Duplicate rule id: {identity}")
        if rule["category"] not in _CATEGORIES:
            raise ValueError("rule.category must be user_driver, kernel_driver or firmware")
        for source_id in _strings(rule["source_ids"], "rule.source_ids"):
            if source_id not in sources:
                raise ValueError(f"Unknown source: {source_id}")
            engine = parsers[sources[source_id]["parser_id"]]["engine"]
            if (engine == "kernel") != (rule["category"] == "kernel_driver"):
                raise ValueError(f"Source {source_id} cannot produce {rule['category']}")
        match = _object(rule["match"], "rule.match", {"field", "operator", "value", "values", "ignore_case"}, {"field", "operator"})
        if match["field"] not in _FIELDS or match["operator"] not in ("any", "equals", "contains", "in", "regex"):
            raise ValueError("Invalid rule match field or operator")
        if "ignore_case" in match and type(match["ignore_case"]) is not bool:
            raise ValueError("match.ignore_case must be boolean")
        if match["operator"] in ("equals", "contains", "regex"):
            _text(match.get("value"), "match.value")
        elif "value" in match:
            _text(match["value"], "match.value", empty=True)
        if match["operator"] == "in" or "values" in match:
            _strings(match.get("values"), "match.values", nonempty=match["operator"] == "in")
        if match["operator"] == "regex":
            _pattern(match["value"], "match.value")
        rules[identity] = rule
    for alias in _list(config["module_aliases"], "module_aliases"):
        keys = {"pattern", "replacement", "ignore_case"}
        _object(alias, "module alias", keys, keys)
        _pattern(alias["pattern"], "alias.pattern")
        _text(alias["replacement"], "alias.replacement")
        if type(alias["ignore_case"]) is not bool:
            raise ValueError("alias.ignore_case must be boolean")
    for alias in config["module_aliases"]:
        if normalize_module(alias["replacement"], config) != alias["replacement"]:
            raise ValueError("alias.replacement must be a canonical literal name under the first matching alias")
    labels = config["firmware_types"]
    if not isinstance(labels, dict):
        raise ValueError("firmware_types must be an object")
    for key, label in labels.items():
        if not isinstance(key, str) or not re.fullmatch(r"\d+", key, re.ASCII):
            raise ValueError("firmware_types keys must be nonnegative integer strings")
        _text(label, "firmware type name")
    module_names = set()
    for module in _list(config["modules"], "modules"):
        keys = {"module", "user_module_name", "kernel_keyword"}
        _object(module, "module", keys, keys)
        label = _text(module["module"], "module.module")
        if label in module_names:
            raise ValueError(f"Duplicate module: {label}")
        module_names.add(label)
        _text(module["user_module_name"], "module.user_module_name", empty=True)
        _text(module["kernel_keyword"], "module.kernel_keyword", empty=True)
    return config


def active_sources(config: dict) -> list[dict]:
    selected = {source for rule in config["rules"] for source in rule["source_ids"]}
    return [source for source in config["sources"] if source["id"] in selected]


def record_enabled(config: dict, source_ids, category: str, metadata: dict, raw: str, body: str) -> bool:
    """Match complete records without dropping continuation lines prematurely."""
    selected = set(source_ids)
    for rule in config["rules"]:
        if rule["category"] != category or not selected.intersection(rule["source_ids"]):
            continue
        match = rule["match"]
        operator = match["operator"]
        if operator == "any":
            return True
        candidate = raw if match["field"] == "raw_text" else body if match["field"] == "text" else metadata.get(match["field"], "")
        value = "" if candidate is None else str(candidate)
        ignore_case = match.get("ignore_case", False)
        if operator == "regex":
            if _compiled(match["value"], ignore_case).search(value):
                return True
            continue
        expected = match.get("value", "")
        options = match.get("values", [])
        if ignore_case:
            value, expected = value.casefold(), expected.casefold()
            options = [item.casefold() for item in options]
        if ((operator == "equals" and value == expected) or (operator == "contains" and expected in value)
                or (operator == "in" and value in options)):
            return True
    return False


def normalize_module(value: str, config=None) -> str:
    value = str(value).strip(_SPACE_CHARS)
    for alias in (default_config() if config is None else config)["module_aliases"]:
        if _compiled(alias["pattern"], alias["ignore_case"]).search(value):
            return alias["replacement"]
    return value


def firmware_type_name(value, config=None) -> str:
    value = str(value)
    return (default_config() if config is None else config)["firmware_types"].get(value, value)
