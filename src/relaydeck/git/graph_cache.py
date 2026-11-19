"""Graph cache values; their owner uses the snapshot cache's accounting lock."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass, field

from relaydeck.git.results import GraphContext, GraphResult

GraphKey = tuple[str, str, str, str, str]
GRAPH_TTL_S = 15 * 60.0
GRAPH_IDLE_S = 60.0
COMMIT_IDLE_S = 60.0


@dataclass(slots=True)
class GraphEntry:
    context: GraphContext
    pages: dict[int, GraphResult] = field(default_factory=dict)
    size: int = 0
    touched: float = 0.0


def memory_size(value: object, seen: set[int] | None = None) -> int:
    """Charge the actual retained containers and values, including the OID membership set."""
    visited = seen if seen is not None else set()
    if id(value) in visited:
        return 0
    visited.add(id(value))
    size = sys.getsizeof(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        size += sum(memory_size(getattr(value, f.name), visited) for f in dataclasses.fields(value))
    elif isinstance(value, dict):
        size += sum(memory_size(k, visited) + memory_size(v, visited) for k, v in value.items())
    elif isinstance(value, tuple | list | set | frozenset):
        size += sum(memory_size(v, visited) for v in value)
    return size


def graph_key(context: GraphContext) -> GraphKey:
    return context.account, context.host, context.worktree, context.mode, context.revision
