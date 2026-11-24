"""Agent names are worked out once per inventory and only for agents that changed."""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from relaydeck.client.models import inventory_selectors
from relaydeck.client.models.inventory_selectors import agent_names
from relaydeck.domain.models import (
    Agent,
    AgentStatus,
    AppState,
    Inventory,
    Tab,
    scope_key,
)

HOST = "home-mac"
KINDS = ("kind-a", "kind-b", "")
LABELS = ("", "Docs", "Docs Writer", "docs-writer", "Review")
TITLES = ("", "Subject one", "Subject two")


def _agent(index: int, rng: random.Random) -> Agent:
    return Agent(
        terminal_id=f"t{index}",
        host_id=HOST,
        workspace_id="w1",
        tab_id=f"tab-{index // 2}",
        pane_id=f"p{index}",
        name=rng.choice(("", "docs-writer", "Docs Writer", f"agent {index}")),
        agent=rng.choice(KINDS),
        title=rng.choice(TITLES),
        status=AgentStatus.WORKING,
    )


def _state(count: int, rng: random.Random) -> AppState:
    agents = [_agent(index, rng) for index in range(count)]
    tabs = {
        scope_key(HOST, f"tab-{index}"): Tab(
            id=f"tab-{index}", host_id=HOST, workspace_id="w1", label=rng.choice(LABELS)
        )
        for index in range(count // 2)
    }
    inventory = Inventory(tabs=tabs, agents={scope_key(HOST, a.terminal_id): a for a in agents})
    return AppState(inventory=inventory)


def _fresh(state: AppState) -> dict[str, str]:
    """The names of this inventory worked out with nothing remembered."""
    return inventory_selectors._compute_names(state.inventory, inventory_selectors._Names()).names


def _count_work(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = inventory_selectors._display

    def counting(name: object, tab_label: object) -> str:
        seen.append(str(name))
        return real(name, tab_label)

    monkeypatch.setattr(inventory_selectors, "_display", counting)
    return seen


def test_one_inventory_is_worked_out_once_however_often_it_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(
        40,
        random.Random(  # noqa: S311
            1
        ),
    )
    seen = _count_work(monkeypatch)
    first = agent_names(state)
    worked = len(seen)
    assert worked == 40
    assert agent_names(state) is first
    assert agent_names(replace(state, stale=True)) is first  # same inventory
    assert len(seen) == worked


def test_the_shared_names_cannot_be_mutated_by_a_caller() -> None:
    state = _state(
        3,
        random.Random(  # noqa: S311
            7
        ),
    )
    names = agent_names(state)
    with pytest.raises(TypeError):
        names["forged"] = "forged"  # type: ignore[index]
    assert "forged" not in agent_names(state)


def test_a_changed_agent_is_the_only_one_worked_out_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(
        40,
        random.Random(  # noqa: S311
            2
        ),
    )
    agent_names(state)
    key = scope_key(HOST, "t7")
    agents = dict(state.inventory.agents)
    agents[key] = replace(agents[key], name="renamed", status=AgentStatus.BLOCKED)
    # The reducers hand over a copy of every map, so only the agent objects tell what changed.
    changed = replace(
        state,
        inventory=replace(state.inventory, agents=agents, tabs=dict(state.inventory.tabs)),
    )
    seen = _count_work(monkeypatch)
    names = agent_names(changed)
    assert seen == ["renamed"]
    assert names == _fresh(changed)
    assert names[key] == "renamed"


def test_a_changed_tab_label_reaches_agents_whose_objects_did_not_change() -> None:
    state = _state(
        20,
        random.Random(  # noqa: S311
            3
        ),
    )
    agents = dict(state.inventory.agents)
    for terminal in ("t4", "t5"):  # the two agents of tab-2 are named by their tab
        agents[scope_key(HOST, terminal)] = replace(
            agents[scope_key(HOST, terminal)], name="", title=""
        )
    state = replace(state, inventory=replace(state.inventory, agents=agents))
    agent_names(state)
    tab_key = scope_key(HOST, "tab-2")
    tabs = dict(state.inventory.tabs)
    tabs[tab_key] = replace(tabs[tab_key], label="Brand New Label")
    changed = replace(state, inventory=replace(state.inventory, tabs=tabs))
    names = agent_names(changed)
    assert names == _fresh(changed)
    assert names[scope_key(HOST, "t4")].startswith("Brand New Label")
    assert names != _fresh(state)


def test_removed_agents_and_new_agents_follow_the_inventory() -> None:
    rng = random.Random(  # noqa: S311
        4
    )
    state = _state(20, rng)
    agent_names(state)
    agents = dict(state.inventory.agents)
    del agents[scope_key(HOST, "t3")]
    agents[scope_key(HOST, "t99")] = _agent(99, rng)
    changed = replace(state, inventory=replace(state.inventory, agents=agents))
    names = agent_names(changed)
    assert names == _fresh(changed)
    assert scope_key(HOST, "t3") not in names and scope_key(HOST, "t99") in names


def test_names_after_random_edits_equal_names_worked_out_from_nothing() -> None:
    rng = random.Random(  # noqa: S311
        5
    )
    state = _state(30, rng)
    agent_names(state)
    for _ in range(300):
        agents, tabs = dict(state.inventory.agents), dict(state.inventory.tabs)
        for _ in range(rng.randrange(1, 4)):
            choice = rng.randrange(4)
            key = rng.choice(sorted(agents))
            if choice == 0:
                agents[key] = replace(agents[key], name=rng.choice(("", "docs-writer", "same")))
            elif choice == 1:
                agents[key] = replace(agents[key], status=rng.choice(list(AgentStatus)))
            elif choice == 2 and tabs:
                tab_key = rng.choice(sorted(tabs))
                tabs[tab_key] = replace(tabs[tab_key], label=rng.choice(LABELS))
            elif len(agents) > 5:
                del agents[key]
        state = replace(state, inventory=replace(state.inventory, agents=agents, tabs=tabs))
        assert agent_names(state) == _fresh(state)
