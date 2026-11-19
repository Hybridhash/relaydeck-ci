"""Create an empty 0700 probe folder and let the user decide trust in the tool's own terminal
prompt. Never send tool input or write its trust file.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from relaydeck.usage.api import (
    NOT_INSTALLED,
    ProbeTarget,
    Spawn,
    offer_trust,
    on_path,
    probe_folder,
    run_in_terminal,
)


def load_probe_targets(config_path: Path, state_dir: Path | None = None) -> tuple[ProbeTarget, ...]:
    """Return enabled limits blocks whose catalog recipe uses a companion-owned probe folder.
    Treat unreadable configuration or catalog data as no targets.
    """
    from relaydeck.companion.config import DEFAULT_STATE
    from relaydeck.companion.inspection.detect import CatalogError, load_catalog

    try:
        body = tomllib.loads(config_path.read_text(encoding="utf-8"))
        entries = {entry.id: entry for entry in load_catalog().entries}
    except (OSError, ValueError, CatalogError):
        return ()
    usage = body.get("usage")
    blocks = usage.get("limits", []) if isinstance(usage, dict) else []
    if state_dir is None:
        state = body.get("state_dir")
        state_dir = Path(str(state if isinstance(state, str) and state else DEFAULT_STATE))
        state_dir = state_dir.expanduser()
        if not state_dir.is_absolute():
            state_dir = config_path.parent / state_dir
    targets: list[ProbeTarget] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict) or block.get("enabled", True) is not True:
            continue
        recipe = getattr(entries.get(str(block.get("kind"))), "tui", None)
        folder = probe_folder(getattr(recipe, "probe_dir", None), state_dir)
        if recipe is None or folder is None:
            continue
        target = ProbeTarget(str(block["kind"]), recipe.binary, folder, recipe.trust_store)
        if all(t.kind != target.kind for t in targets):
            targets.append(target)
    return tuple(targets)


def _offer_with_summary(target: ProbeTarget, say: Callable[[str], None], **options: Any) -> None:
    spoke = False

    def tell(text: str) -> None:
        nonlocal spoke
        spoke = True
        say(text)

    state = offer_trust(target, say=tell, **options)
    if spoke:
        return
    if state == NOT_INSTALLED:
        say(f"{target.binary} not installed; its plan limits stay off until it is.")
    else:
        say(f"{target.binary} probe folder: {state}")


def offer_setup_trust(
    targets: Sequence[ProbeTarget],
    *,
    ask: Callable[[str], bool],
    say: Callable[[str], None],
    interactive: bool,
    spawn: Spawn | None = None,
    which: Callable[[str], Any] | None = None,
) -> None:
    """The setup step: `offer_trust` per target, all output through `say`. It only advises, so
    no state here is a failure.
    """
    for target in targets:
        _offer_with_summary(
            target,
            say,
            ask=ask,
            spawn=spawn or run_in_terminal,
            interactive=interactive,
            which=which or on_path,
        )
