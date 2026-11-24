"""String patch targets resolve to their active module owners."""

from __future__ import annotations

from pathlib import Path

from tests.support.architecture.patch_strings import patch_targets, resolve

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures" / "architecture" / "patch_strings"
ROOTS = {"relaydeck": ROOT / "src" / "relaydeck", "tests": TESTS}

# {"dotted.target": "why it is tolerated and when it goes"}
UNRESOLVED: dict[str, str] = {}


def _scan() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(TESTS.rglob("*.py")):
        if "__pycache__" in path.parts or "fixtures" in path.parts:
            continue
        for line, target in patch_targets(path.read_text(encoding="utf-8")):
            if target.split(".")[0] in ROOTS:
                found.setdefault(target, []).append(f"{path.relative_to(ROOT)}:{line}")
    return found


def test_patch_targets_in_tests_are_found() -> None:
    assert len(_scan()) > 10


def test_every_patch_string_resolves() -> None:
    dead = {
        target: (why, places)
        for target, places in _scan().items()
        if target not in UNRESOLVED and (why := resolve(target, ROOTS)) is not None
    }
    assert dead == {}, dead


def test_the_allowlist_only_holds_targets_that_are_still_dead() -> None:
    scanned = _scan()
    stale = [
        target for target in UNRESOLVED if target not in scanned or resolve(target, ROOTS) is None
    ]
    assert stale == [], f"remove from UNRESOLVED: {stale}"


def test_fixture_with_live_targets_resolves() -> None:
    roots = {"relaydeck": FIXTURES / "tree" / "relaydeck"}
    source = (FIXTURES / "live_patches.py").read_text(encoding="utf-8")
    targets = patch_targets(source)
    assert len(targets) == 6
    assert [resolve(target, roots) for _, target in targets] == [None] * 6


def test_fixture_with_dead_targets_fails() -> None:
    roots = {"relaydeck": FIXTURES / "tree" / "relaydeck"}
    source = (FIXTURES / "dead_patches.py").read_text(encoding="utf-8")
    targets = patch_targets(source)
    assert len(targets) == 6
    reasons = [resolve(target, roots) for _, target in targets]
    assert all(reason is not None for reason in reasons), list(zip(targets, reasons, strict=True))


def test_patch_targets_by_attribute_name_and_other_roots_are_not_targets() -> None:
    source = (
        "monkeypatch.setattr(module, 'name', 1)\n"
        "monkeypatch.setattr('plain', 1)\n"
        "mock.patch('os.path.exists')\n"
    )
    assert [target for _, target in patch_targets(source)] == ["os.path.exists"]
    assert resolve("os.path.exists", {"relaydeck": Path("/nonexistent")}) is None
