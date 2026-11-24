"""Published operation entries import without the Flet runtime."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "src/relaydeck/client/features"
ENTRIES = sorted(path.parent.name for path in FEATURES.glob("*/api.py"))


def _probe(statement: str) -> subprocess.CompletedProcess[str]:
    paths = [ROOT / "src", ROOT, ROOT / "extensions/terminal/src"]
    code = f"""
import importlib.abc
import sys
sys.path[:0] = {list(map(str, paths))!r}
attempted = []
class NoFlet(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'flet' or fullname.startswith('flet.'):
            attempted.append(fullname)
            raise RuntimeError('headless entry attempted to import Flet')
sys.meta_path.insert(0, NoFlet())
{statement}
assert not attempted, attempted
assert not any(name == 'flet' or name.startswith('flet.') for name in sys.modules)
"""
    return subprocess.run(
        [sys.executable, "-I", "-B", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.mark.parametrize("feature", ENTRIES)
def test_published_feature_api_refuses_flet_dependencies(feature: str) -> None:
    statement = (
        f"import relaydeck.client.features.{feature}.api as entry\n"
        f"assert entry.__file__ == {str(FEATURES)!r} + '/{feature}/api.py'"
    )
    result = _probe(statement)
    assert result.returncode == 0, result.stdout + result.stderr


def test_headless_probe_rejects_a_direct_flet_import() -> None:
    result = _probe("import flet")
    assert result.returncode != 0
    assert "headless entry attempted to import Flet" in result.stderr


def test_rename_context_has_one_pure_owner() -> None:
    from relaydeck.client.features.rename import rename, rename_model, rename_sheet, rename_wiring

    assert rename_sheet.SheetContext is rename_model.SheetContext
    assert rename_wiring.SheetContext is rename_model.SheetContext
    assert rename.SheetContext is rename_model.SheetContext
    assert rename_model.SheetContext.__module__ == rename_model.__name__


def test_terminal_protocol_and_recovery_values_refuse_flet_dependencies() -> None:
    extension_file = ROOT / "extensions/terminal/src/relaydeck_terminal/__init__.py"
    statement = "\n".join(
        [
            "import relaydeck_terminal as extension",
            "import relaydeck_terminal.protocol",
            "from relaydeck_terminal.resume import ResumeOutcome",
            "import relaydeck.client.features.terminal.ui_resume as service",
            "assert service.ResumeOutcome is ResumeOutcome",
            "assert ResumeOutcome('noop', 'fresh').resent_bytes == 0",
            f"assert extension.__file__ == {str(extension_file)!r}",
        ]
    )
    result = _probe(statement)
    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_recovery_value_has_one_canonical_owner() -> None:
    from relaydeck_terminal.resume import ResumeOutcome
    from relaydeck_terminal.terminal import ResumeOutcome as RendererOutcome

    from relaydeck.client.features.terminal.ui_resume import ResumeOutcome as ServiceOutcome

    assert RendererOutcome is ResumeOutcome
    assert ServiceOutcome is ResumeOutcome
    assert ResumeOutcome.__module__ == "relaydeck_terminal.resume"


def test_clipboard_service_adapter_has_no_eager_flet_dependency() -> None:
    clipboard_file = ROOT / "src/relaydeck/client/platform/flet/clipboard.py"
    statement = f"""
import asyncio
import relaydeck.client.platform.flet.clipboard as clipboard
assert clipboard.__file__ == {str(clipboard_file)!r}
class Service:
    async def _invoke_method(self, name, *, timeout):
        assert name == 'get' and timeout == clipboard.CLIPBOARD_READ_TIMEOUT
        return 'clipboard value'
    async def set(self, value):
        self.value = value
async def check():
    service = Service()
    port = clipboard.FletClipboard(service)
    await port.set_text('copied value')
    assert service.value == port.copied_text() == 'copied value'
    result = await clipboard.read_clipboard(port)
    assert result.status is clipboard.ClipboardReadStatus.AVAILABLE
    assert result.text == 'clipboard value' and not result.from_app
asyncio.run(check())
"""
    result = _probe(statement)
    assert result.returncode == 0, result.stdout + result.stderr


def test_paste_review_logic_refuses_flet_dependencies() -> None:
    review_file = FEATURES / "terminal/paste_review.py"
    statement = (
        "import relaydeck.client.features.terminal.paste_review as review\n"
        f"assert review.__file__ == {str(review_file)!r}\n"
        "assert review.PasteReviewSession.__module__ == review.__name__\n"
        "assert review.PendingPaste.__module__ == review.__name__"
    )
    result = _probe(statement)
    assert result.returncode == 0, result.stdout + result.stderr


def test_paste_sheet_consumers_share_the_canonical_component() -> None:
    import relaydeck.client.screens.shell.screen as screen
    import relaydeck.client.widgets.terminal.screen_overlays as screen_overlays
    from relaydeck.client.features.terminal import paste_sheet

    assert screen.PasteReviewSheet is paste_sheet.PasteReviewSheet
    assert screen_overlays.PasteReviewSheet is paste_sheet.PasteReviewSheet


def test_terminal_controller_refuses_concrete_transport_dependencies() -> None:
    controller_file = FEATURES / "terminal/controller.py"
    fence = """
class NoTransport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'relaydeck.client.platform.transport' or fullname.startswith(
            'relaydeck.client.platform.transport.'
        ):
            attempted.append(fullname)
            raise RuntimeError('terminal core attempted a concrete transport import')
sys.meta_path.insert(0, NoTransport())
"""
    result = _probe(
        fence
        + "import relaydeck.client.features.terminal.controller as controller\n"
        + f"assert controller.__file__ == {str(controller_file)!r}"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    negative = _probe(fence + "import relaydeck.client.platform.transport.terminal_stream")
    assert negative.returncode != 0
    assert "terminal core attempted a concrete transport import" in negative.stderr
