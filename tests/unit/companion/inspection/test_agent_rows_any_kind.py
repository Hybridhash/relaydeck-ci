"""An agent Herdr lists is an agent row whatever its kind: no catalog id or alias gate."""

from __future__ import annotations

import pytest

from relaydeck.companion.inspection.detect import load_catalog
from relaydeck.connector.source import translate_snapshot
from relaydeck.contracts.protocol import EXPECTED_PROTOCOL
from relaydeck.contracts.schemas import validate

_DETECTED = sorted(entry.id for entry in load_catalog().entries if entry.herdr_detects)


def _session() -> dict[str, object]:
    return {
        "protocol": EXPECTED_PROTOCOL,
        "workspaces": [{"workspace_id": "w1", "label": "space"}],
        "tabs": [{"tab_id": "w1:t1", "workspace_id": "w1", "label": "work", "pane_count": 1}],
        "panes": [{"pane_id": "w1:p1", "workspace_id": "w1", "tab_id": "w1:t1"}],
    }


@pytest.mark.parametrize("kind", _DETECTED)
def test_a_listed_agent_of_any_detected_kind_is_an_agent_row(kind: str) -> None:
    listed = {
        "terminal_id": "term_1",
        "workspace_id": "w1",
        "tab_id": "w1:t1",
        "pane_id": "w1:p1",
        "agent": kind,
        "agent_status": "idle",
        "terminal_title": f"{kind}-cli",
    }
    doc = translate_snapshot("host", "e1", _session(), [listed])
    validate("relaydeck/gateway/v1/snapshot", doc)
    assert [(row["agent"], row["tab_id"]) for row in doc["agents"]] == [(kind, "w1:t1")]
