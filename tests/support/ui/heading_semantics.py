"""Exact projection of heading annotations onto their underlying rendering tree."""

import json
from copy import deepcopy
from typing import Any

_LAYOUT_DEFAULTS = {
    "expand": None,
    "expand_loose": False,
    "col": 12,
    "visible": True,
    "disabled": False,
}


def without_heading_annotations(value: Any) -> Any:
    if isinstance(value, list):
        return [without_heading_annotations(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") == "Semantics" and value.get("props", {}).get("header") is True:
        props = value["props"]
        assert set(props) <= {"header", *_LAYOUT_DEFAULTS}
        assert len(value.get("children", [])) == 1
        child = value["children"][0]
        assert child["slot"] == "content"
        if "key" in child:
            key = json.loads(value.get("key", ""))
            assert isinstance(key, list) and len(key) == 3
            assert key[:2] == ["heading", "str"]
            assert key[2] == child["key"]
        else:
            assert "key" not in value
        for name in props.keys() & _LAYOUT_DEFAULTS.keys():
            assert props[name] == child.get("props", {}).get(name, _LAYOUT_DEFAULTS[name]), name
        result = deepcopy(without_heading_annotations(child))
        result["slot"] = value["slot"]
        return result
    return {key: without_heading_annotations(item) for key, item in value.items()}
