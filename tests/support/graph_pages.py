"""Small typed graph replies for model and wire tests."""

from __future__ import annotations

from relaydeck.contracts.git import GraphCommit, GraphMode, GraphPage


def graph_commit(
    number: int, *, parents: tuple[int, ...] = (), commit_id: str | None = None
) -> GraphCommit:
    return GraphCommit(
        oid=f"{number:040x}",
        parents=tuple(f"{p:040x}" for p in parents),
        subject=f"c{number}",
        time_ms=number,
        ahead=True,
        commit_id=commit_id,
    )


def graph_page(
    commits: tuple[GraphCommit, ...] = (),
    *,
    mode: GraphMode = "all",
    snapshot: str = "graph-1",
    next: str | None = None,
) -> GraphPage:
    return GraphPage(
        snapshot=snapshot,
        mode=mode,
        head="a" * 40,
        base_oid="b" * 40,
        base_name="main",
        split_oid="b" * 40,
        ahead=3,
        commits=commits,
        as_of_ms=5,
        next=next,
    )
