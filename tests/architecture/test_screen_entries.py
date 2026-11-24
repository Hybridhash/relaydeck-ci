"""Screen and feature entries refuse private imports, including hidden imports."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.architecture.entries import entry_violations

ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "src/relaydeck/client"


def _fixture(tmp_path: Path, source: str, content: str) -> Path:
    client = tmp_path / "client"
    files = {
        "features/alpha/api.py": '__all__ = ["Value", "choose"]\n',
        "features/alpha/ui.py": '__all__ = ["Panel"]\n',
        "screens/a/__init__.py": "",
        "screens/b/__init__.py": "",
        source: content,
    }
    for name, text in files.items():
        path = client / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return client


def test_real_screen_and_published_feature_imports_use_entries() -> None:
    assert entry_violations(CLIENT) == []


@pytest.mark.parametrize("source", ["screens/a/screen.py", "widgets/block.py"])
@pytest.mark.parametrize(
    "statement",
    [
        "from fxclient.features.alpha.model import Value\n",
        "from fxclient.features.alpha import model\n",
        "from fxclient.features import alpha\n",
        "import fxclient.features.alpha.model as model\n",
    ],
)
@pytest.mark.parametrize("hidden", [False, True])
def test_private_feature_imports_fail(
    tmp_path: Path, source: str, statement: str, hidden: bool
) -> None:
    text = "def load():\n    " + statement if hidden else statement
    client = _fixture(tmp_path, source, text)
    assert entry_violations(client, "fxclient")


@pytest.mark.parametrize(
    "statement",
    [
        "from fxclient.features.alpha.api import Value\n",
        "from fxclient.features.alpha.ui import Panel\n",
        "import fxclient.features.alpha.api as values\nvalue = values.Value\n",
        "from fxclient.features.alpha import api as values\nvalue = values.choose\n",
    ],
)
def test_public_feature_imports_pass(tmp_path: Path, statement: str) -> None:
    client = _fixture(tmp_path, "screens/a/screen.py", statement)
    assert entry_violations(client, "fxclient") == []


@pytest.mark.parametrize(
    "statement",
    [
        "from fxclient.features.alpha.api import private\n",
        "import fxclient.features.alpha.api as values\nvalue = values.private\n",
    ],
)
def test_undeclared_public_names_fail(tmp_path: Path, statement: str) -> None:
    client = _fixture(tmp_path, "screens/a/screen.py", statement)
    assert entry_violations(client, "fxclient")


def test_a_type_only_private_import_fails(tmp_path: Path) -> None:
    client = _fixture(
        tmp_path,
        "screens/a/screen.py",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    from fxclient.features.alpha.model import Value\n",
    )
    assert entry_violations(client, "fxclient")


def test_app_composition_uses_the_screen_entry(tmp_path: Path) -> None:
    client = _fixture(tmp_path, "app/root.py", "from fxclient.screens.a.screen import View\n")
    assert entry_violations(client, "fxclient") == []


def test_outside_code_cannot_import_screen_parts(tmp_path: Path) -> None:
    client = _fixture(tmp_path, "app/root.py", "from fxclient.screens.a.parts.card import View\n")
    assert entry_violations(client, "fxclient")


def test_screen_parts_can_import_their_own_siblings(tmp_path: Path) -> None:
    client = _fixture(
        tmp_path, "screens/a/parts/row.py", "from fxclient.screens.a.parts.card import View\n"
    )
    assert entry_violations(client, "fxclient") == []


def test_changes_origin_strip_has_one_canonical_owner() -> None:
    from relaydeck.client.app.workflows import ask
    from relaydeck.client.screens.changes import screen
    from relaydeck.client.screens.changes.parts import ask_strip

    assert ask.ask_strip is screen.ask_strip is ask_strip.ask_strip


@pytest.mark.parametrize(
    "source", ["screens/a/screen.py", "widgets/block.py", "features/beta/owner.py"]
)
@pytest.mark.parametrize("wrapper", ["module", "function", "type"])
@pytest.mark.parametrize(
    ("statement", "allowed"),
    [
        ("from fxclient.features.alpha.model import Value", False),
        ("from fxclient.features.alpha import model", False),
        ("from fxclient.features import alpha", False),
        ("import fxclient.features.alpha.model as model", False),
        ("import fxclient.features.alpha.api\nvalue = fxclient.features.alpha.api.private", False),
        ("from fxclient.features.alpha import api\nvalue = api.private", False),
        ("import fxclient.features.alpha.api as values\nvalue = values.private", False),
        ("from fxclient.features.alpha.api import private", False),
        ("from fxclient.features.alpha.ui import Value", False),
        ("from fxclient.features.alpha.api import Value", True),
        ("from fxclient.features.alpha.ui import Panel", True),
        ("import fxclient.features.alpha.api\nvalue = fxclient.features.alpha.api.Value", True),
        ("import fxclient.features.alpha.api as values\nvalue = values.choose", True),
        ("from fxclient.features.alpha import api as values\nvalue = values.Value", True),
    ],
)
def test_published_entry_boundaries_cover_qualified_hidden_and_type_imports(
    tmp_path: Path, source: str, wrapper: str, statement: str, allowed: bool
) -> None:
    if wrapper == "module":
        text = statement + "\n"
    else:
        header = (
            "def load():"
            if wrapper == "function"
            else "from typing import TYPE_CHECKING\nif TYPE_CHECKING:"
        )
        text = header + "\n" + "".join("    " + line + "\n" for line in statement.splitlines())
    client = _fixture(tmp_path, source, text)
    assert (not entry_violations(client, "fxclient")) is allowed


@pytest.mark.parametrize(
    ("source", "statement", "allowed"),
    [
        ("features/alpha/owner.py", "from .model import Value", True),
        (
            "features/alpha/owner.py",
            "import fxclient.features.alpha.model as model\nvalue = model.private",
            True,
        ),
        ("features/beta/owner.py", "from ..alpha.api import Value", True),
        ("features/beta/owner.py", "from ..alpha.model import Value", False),
        (
            "features/beta/owner.py",
            "from ..alpha import api as values\nvalue = values.private",
            False,
        ),
        ("app/root.py", "from fxclient.features.alpha.model import Value", True),
        (
            "screens/a/screen.py",
            "import fxclient.features.alpha.api\nvalue = fxclient.features.alpha.api.Value.member",
            True,
        ),
    ],
)
def test_own_feature_relative_and_composition_import_rules(
    tmp_path: Path, source: str, statement: str, allowed: bool
) -> None:
    client = _fixture(tmp_path, source, statement + "\n")
    assert (not entry_violations(client, "fxclient")) is allowed
