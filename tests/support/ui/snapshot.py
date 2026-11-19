"""Stable control-tree snapshots of a mounted page, for golden comparison.

A snapshot is JSON: one node per control (type, key, the props that affect
rendering, children by slot). Only props that differ from their Flet default
are recorded. Declarative components appear as ``Component:<name>`` nodes
whose rendered body is their child, so structure and hook-driven output are
both covered.

What is excluded, on purpose (the volatile allowlist):

- private fields (``_i`` session ids, ``_values``, ``_b`` internals, ...) and
  ``service_id`` (the number a session assigns to a service control);
- ``data`` (test/model scratch state carried on controls, not rendering);
- event handlers and other callables (``on_*`` and any callable prop);
- component ``args``/``kwargs`` (callbacks and state objects; the rendered
  body below them is what the user sees);
- values that cannot be read or compared, recorded as ``"<unreadable>"``.

Strings are scrubbed for generated material wherever they appear as props or
keys: UUIDs, runs of 24 or more hex digits, 13-digit epoch-millisecond
numbers, memory addresses, and relative ages (``"12 s ago"``) become fixed
markers, so a fresh id or a fresh clock does not churn the goldens. Two
generated key families are scrubbed by name: ``draft-editor-<serial>``
(the serial counts editor ownership changes) and any ``:reset:<epoch>``
key suffix (the epoch counts direct-typing resets). Synthetic ids from the
test worlds (``term-1`` and friends) are left alone.

Goldens regenerate only when the environment variable
``RELAYDECK_UPDATE_SNAPSHOTS=1`` is set; otherwise a mismatch fails with the
first differing line.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
import math
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

UPDATE_ENV = "RELAYDECK_UPDATE_SNAPSHOTS"

EXCLUDED_FIELDS = frozenset({"data", "args", "kwargs", "fn", "memoized", "service_id"})

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{24,}\b")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_EPOCH_MS = re.compile(r"\b1[0-9]{12}\b")
_AGE = re.compile(r"\b[0-9]+ (?:s|min|h|d) ago\b")
_DRAFT_EDITOR_KEY = re.compile(r"draft-editor-[0-9]+")
_RESET_KEY = re.compile(r":reset:[0-9]+")

_MAX_DEPTH = 200


def scrub(text: str) -> str:
    """Replace generated ids and clock readings in a string with fixed markers."""
    text = _UUID.sub("<uuid>", text)
    text = _HEX_RUN.sub("<hex>", text)
    text = _EPOCH_MS.sub("<epoch-ms>", text)
    text = _AGE.sub("<age> ago", text)
    text = _ADDRESS.sub("0x...", text)
    text = _DRAFT_EDITOR_KEY.sub("draft-editor-<serial>", text)
    return _RESET_KEY.sub(":reset:<epoch>", text)


def _is_control(value: Any) -> bool:
    return dataclasses.is_dataclass(value) and hasattr(value, "_values") and hasattr(value, "_i")


def _type_name(control: Any) -> str:
    if type(control).__name__ == "Component":
        fn = getattr(control, "fn", None)
        name = getattr(fn, "__name__", None) or type(fn).__name__
        return f"Component:{name}"
    return type(control).__name__


def _default(field: dataclasses.Field[Any]) -> Any:
    if field.default is not dataclasses.MISSING:
        return field.default
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory()
    return dataclasses.MISSING


def _normalize(value: Any, depth: int) -> Any:
    """One prop value as stable JSON data."""
    if _is_control(value):
        # A control reached as a prop (not through a child slot) is a service
        # or a reference; its own tree is recorded where it is mounted.
        ref: dict[str, Any] = {"@": _type_name(value)}
        key = getattr(value, "key", None)
        if key is not None:
            ref["key"] = scrub(str(key))
        return ref
    if value is None or isinstance(value, bool | int | str):
        return scrub(value) if isinstance(value, str) else value
    if isinstance(value, float):
        if math.isnan(value):
            return "<nan>"
        if math.isinf(value):
            return "<inf>" if value > 0 else "<-inf>"
        return value + 0.0  # normalise -0.0
    if isinstance(value, enum.Enum):
        inner = value.value
        if isinstance(inner, bool | int | str | float):
            return {"enum": type(value).__name__, "value": _normalize(inner, depth)}
        return {"enum": type(value).__name__, "value": value.name}
    if isinstance(value, bytes | bytearray):
        digest = hashlib.sha256(bytes(value)).hexdigest()[:12]
        return f"<bytes:{len(value)}:{digest}>"
    if depth > 12:
        return f"<{type(value).__name__}>"
    if isinstance(value, list | tuple):
        return [_normalize(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {
            scrub(str(k)): _normalize(v, depth + 1)
            for k, v in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if dataclasses.is_dataclass(value) and not _is_control(value):
        out: dict[str, Any] = {"@": type(value).__name__}
        for field in dataclasses.fields(value):
            if field.name.startswith("_") or field.name in EXCLUDED_FIELDS:
                continue
            try:
                item = getattr(value, field.name)
            except Exception:  # noqa: BLE001 - a snapshot must not die on one prop
                out[field.name] = "<unreadable>"
                continue
            if callable(item):
                continue
            default = _default(field)
            try:
                if default is not dataclasses.MISSING and item == default:
                    continue
            except Exception:  # noqa: BLE001, S110 - uncomparable values are still recorded
                pass
            out[field.name] = _normalize(item, depth + 1)
        return out
    return {"@": type(value).__name__, "repr": scrub(repr(value))[:200]}


def _control_slots(control: Any) -> list[tuple[str, Any]]:
    """(slot, child) pairs: the component body, then control-valued fields."""
    slots: list[tuple[str, Any]] = []
    body = getattr(control, "_b", None)
    if body is not None:
        for child in body if isinstance(body, list | tuple) else [body]:
            if _is_control(child):
                slots.append(("body", child))
    for field in dataclasses.fields(control):
        if field.name.startswith("_") or field.name in EXCLUDED_FIELDS:
            continue
        try:
            value = getattr(control, field.name)
        except Exception:  # noqa: BLE001, S112 - tolerant tree walk
            continue
        if _is_control(value):
            slots.append((field.name, value))
        elif isinstance(value, list | tuple):
            for child in value:
                if _is_control(child):
                    slots.append((field.name, child))
    return slots


def _node(control: Any, seen: set[int], depth: int, slot: str) -> dict[str, Any]:
    name = _type_name(control)
    if id(control) in seen or depth > _MAX_DEPTH:
        return {"type": name, "slot": slot, "ref": "seen"}
    seen.add(id(control))
    node: dict[str, Any] = {"type": name, "slot": slot}
    key = getattr(control, "key", None)
    if key is not None:
        node["key"] = scrub(str(key))
    child_ids = {id(child) for _slot, child in _control_slots(control)}
    props: dict[str, Any] = {}
    for field in dataclasses.fields(control):
        if field.name.startswith("_") or field.name in EXCLUDED_FIELDS or field.name == "key":
            continue
        try:
            value = getattr(control, field.name)
        except Exception:  # noqa: BLE001
            props[field.name] = "<unreadable>"
            continue
        if callable(value) or id(value) in child_ids:
            continue
        if isinstance(value, list | tuple) and any(id(item) in child_ids for item in value):
            continue
        default = _default(field)
        try:
            if default is not dataclasses.MISSING and value == default:
                continue
        except Exception:  # noqa: BLE001, S110 - uncomparable values are still recorded
            pass
        props[field.name] = _normalize(value, 0)
    if props:
        node["props"] = props
    children = [_node(child, seen, depth + 1, slot) for slot, child in _control_slots(control)]
    if children:
        node["children"] = children
    return node


def snapshot_tree(app: Any) -> dict[str, Any]:
    """The mounted page (first view plus any dialogs) as snapshot JSON data."""
    page = app.page
    roots: list[Any] = []
    view_controls = page.views[0].controls
    roots.extend(view_controls if isinstance(view_controls, list | tuple) else [view_controls])
    dialogs = getattr(page, "_dialogs", None)
    if dialogs is not None:
        roots.extend(dialogs.controls or [])
    seen: set[int] = set()
    return {"roots": [_node(root, seen, 0, "root") for root in roots if _is_control(root)]}


def snapshot_json(app: Any) -> str:
    """The snapshot as stable text: sorted keys, one prop per line."""
    return json.dumps(snapshot_tree(app), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def golden_path(directory: Path, name: str) -> Path:
    return directory / f"{name}.json"


def assert_matches_golden(app: Any, directory: Path, name: str) -> None:
    """Compare the app's tree with ``<directory>/<name>.json`` (or rewrite it)."""
    text = snapshot_json(app)
    path = golden_path(directory, name)
    if os.environ.get(UPDATE_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.exists(), f"missing golden {path}; regenerate with {UPDATE_ENV}=1"
    expected = path.read_text(encoding="utf-8")
    if text == expected:
        return
    got_lines, want_lines = text.splitlines(), expected.splitlines()
    for index, (got, want) in enumerate(zip(got_lines, want_lines, strict=False)):
        if got != want:
            raise AssertionError(
                f"snapshot {name} differs at line {index + 1}:\n  got:  {got}\n  want: {want}\n"
                f"regenerate with {UPDATE_ENV}=1 if this rendering change is intended"
            )
    raise AssertionError(
        f"snapshot {name} differs in length: {len(got_lines)} lines, golden has {len(want_lines)}"
    )


async def snapshot_scenario(
    render: Callable[[], Awaitable[Any]], directory: Path, name: str
) -> None:
    """Render twice: the two trees must be identical, then match the golden."""
    first = snapshot_json(await render())
    second = snapshot_json(await render())
    assert first == second, f"snapshot {name} is not identical across two renders"
    path = golden_path(directory, name)
    if os.environ.get(UPDATE_ENV) == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(first, encoding="utf-8")
        return
    assert path.exists(), f"missing golden {path}; regenerate with {UPDATE_ENV}=1"
    expected = path.read_text(encoding="utf-8")
    if first != expected:
        got_lines, want_lines = first.splitlines(), expected.splitlines()
        for index, (got, want) in enumerate(zip(got_lines, want_lines, strict=False)):
            if got != want:
                raise AssertionError(
                    f"snapshot {name} differs at line {index + 1}:\n  got:  {got}\n  want: {want}\n"
                    f"regenerate with {UPDATE_ENV}=1 if this rendering change is intended"
                )
        raise AssertionError(
            f"snapshot {name} differs in length: {len(got_lines)} lines,"
            f" golden has {len(want_lines)}"
        )
