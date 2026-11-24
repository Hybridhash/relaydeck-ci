"""Deterministic graph lanes, independent of rendering and viewport width."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from relaydeck.contracts.git import GraphCommit, GraphMode

SegmentKind = Literal["pass", "up", "down", "in", "out"]
MAX_FRONTIER = 2048


@dataclass(frozen=True, slots=True)
class Track:
    oid: str
    lane: int
    active: bool = False
    seeded: bool = False


@dataclass(frozen=True, slots=True)
class Segment:
    kind: SegmentKind
    start: int
    end: int
    color: int


# A row repeats every lane that passes through it, so the same few segments recur in thousands of
# rows; one shared object per distinct segment keeps wide histories small.
_SEGMENTS: dict[tuple[SegmentKind, int, int, int], Segment] = {}
_SEGMENT_CACHE_LIMIT = 1 << 14


def _segment(kind: SegmentKind, start: int, end: int, color: int) -> Segment:
    key = (kind, start, end, color)
    shared = _SEGMENTS.get(key)
    if shared is None:
        if len(_SEGMENTS) >= _SEGMENT_CACHE_LIMIT:
            _SEGMENTS.clear()
        shared = _SEGMENTS[key] = Segment(kind, start, end, color)
    return shared


@dataclass(frozen=True, slots=True)
class GraphRow:
    oid: str
    lane: int
    color: int
    segments: tuple[Segment, ...]
    head: bool = False
    merge: bool = False
    ahead: bool = False
    dim: bool = False


@dataclass(frozen=True, slots=True)
class LaneState:
    mode: GraphMode
    head: str
    base: str
    split: str
    tracks: tuple[Track, ...]
    seen: frozenset[str] = frozenset()
    width: int = 1  # the most lanes any row so far needed


def seed(mode: GraphMode, head: str, base: str, split: str) -> LaneState:
    tracks: tuple[Track, ...] = (Track(head, 0, seeded=True),)
    if mode == "all" and base != head:
        tracks = Track(base, 0, seeded=True), Track(head, 1, seeded=True)
    return LaneState(mode, head, base, split, tracks)


def _color(state: LaneState, lane: int) -> int:
    if state.mode == "branch":
        return 1 if lane == 0 else lane + 1
    if state.head == state.base and lane == 0:
        return 1
    return lane + 1 if state.head == state.base and lane > 0 else lane


def _free(state: LaneState, tracks: list[Track]) -> int:
    occupied = {t.lane for t in tracks}
    slot = 2 if state.mode == "all" and state.head != state.base else 1
    while slot in occupied:
        slot += 1
    return slot


def append(
    state: LaneState, commits: tuple[GraphCommit, ...]
) -> tuple[tuple[GraphRow, ...], LaneState]:
    tracks, seen, rows = list(state.tracks), set(state.seen), []
    width = state.width
    for commit in commits:
        if commit.oid in seen or any(p in seen or p == commit.oid for p in commit.parents):
            raise ValueError("graph order changed")
        seen.add(commit.oid)
        matches = sorted((t for t in tracks if t.oid == commit.oid), key=lambda t: t.lane)
        node = matches[0] if matches else Track(commit.oid, _free(state, tracks))
        if state.mode == "branch" and commit.oid == state.split:
            lane = matches[0].lane if matches else 0
            incoming = tuple(
                _segment("up" if t.lane == lane else "in", t.lane, lane, _color(state, t.lane))
                for t in matches
                if t.active
            )
            width = max(width, lane + 1, *(t.lane + 1 for t in matches))
            rows.append(
                GraphRow(
                    commit.oid,
                    lane,
                    _color(state, lane),
                    incoming,
                    commit.oid == state.head,
                    len(commit.parents) > 1,
                    False,
                    True,
                )
            )
            tracks.clear()
            continue
        passing = [
            _segment("pass", t.lane, t.lane, _color(state, t.lane))
            for t in sorted(tracks, key=lambda t: t.lane)
            if t.active and t.oid != commit.oid
        ]
        segments = [
            _segment(
                "up" if t.lane == node.lane else "in", t.lane, node.lane, _color(state, t.lane)
            )
            for t in matches
            if t.active
        ]
        tracks = [t for t in tracks if t.oid != commit.oid]
        parents = tuple(dict.fromkeys(commit.parents))
        for index, parent in enumerate(parents):
            targets = sorted((t for t in tracks if t.oid == parent), key=lambda t: t.lane)
            target = next((t for t in targets if t.seeded), None)
            if index == 0 and target is None:
                target = Track(parent, node.lane, True)
                tracks.append(target)
                segments.append(_segment("down", node.lane, node.lane, _color(state, node.lane)))
                continue
            if target is None:
                target = targets[0] if targets else Track(parent, _free(state, tracks))
                if target not in tracks:
                    tracks.append(target)
            tracks = [replace(t, active=True) if t is target else t for t in tracks]
            segments.append(_segment("out", node.lane, target.lane, _color(state, target.lane)))
        if len(tracks) > MAX_FRONTIER:
            raise ValueError("graph lane limit")
        width = max(width, node.lane + 1, *(t.lane + 1 for t in tracks))
        rows.append(
            GraphRow(
                commit.oid,
                node.lane,
                _color(state, node.lane),
                tuple(segments + passing),
                commit.oid == state.head,
                len(parents) > 1,
                commit.ahead,
            )
        )
    return tuple(rows), replace(state, tracks=tuple(tracks), seen=frozenset(seen), width=width)


@dataclass(frozen=True, slots=True)
class Projection:
    row: GraphRow
    limit: int
    hidden: int

    def slot(self, lane: int) -> int:
        return min(lane, self.limit - 1) if self.hidden else lane


def project(row: GraphRow, limit: int) -> Projection:
    if limit < 2:
        raise ValueError("graph needs two visible lanes")
    lanes = {row.lane} | {s.start for s in row.segments} | {s.end for s in row.segments}
    hidden = len({lane for lane in lanes if lane >= limit - 1}) if max(lanes) >= limit else 0
    return Projection(row, limit, hidden)
