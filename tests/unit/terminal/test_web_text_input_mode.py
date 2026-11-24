"""The terminal extension leaves Flutter web in its default text-input mode."""

from __future__ import annotations

from tests.support.paths import TESTS_DIR

FLUTTER = TESTS_DIR.parent / "extensions/terminal/src/flutter/relaydeck_terminal/lib"


def test_the_terminal_extension_does_not_force_web_semantics() -> None:
    # With semantics forced on, Flutter web takes text only through semantic text fields: the
    # terminal view's own input gets no DOM input, so the phone keyboard, IME and dictation fail,
    # and the page's keyboard guard looks for the input in the wrong host.
    sources = {path.name: path.read_text(encoding="utf-8") for path in FLUTTER.rglob("*.dart")}
    assert sources, "terminal extension sources not found"
    assert [name for name, text in sources.items() if "ensureSemantics" in text] == []
