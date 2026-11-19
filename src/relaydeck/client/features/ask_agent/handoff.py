"""One-shot app-session handoff. Navigation creates sessions; the session owns sending."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from relaydeck.client.features.ask_agent.model import AskScope
from relaydeck.client.features.changes.api import ReadingBookmark


@dataclass(frozen=True, slots=True)
class Identity:
    account: str = field(repr=False)
    transport: Any = field(repr=False)
    key: str = field(repr=False)
    epoch: str = field(repr=False)

    def matches(self, other: Identity) -> bool:
        return (
            self.account == other.account
            and self.transport is other.transport
            and self.key == other.key
            and self.epoch == other.epoch
        )


@dataclass(slots=True, repr=False)
class PendingAsk:
    identity: Identity
    scope: AskScope
    prompt: str
    bookmark: ReadingBookmark
    title: str
    request_id: str = field(default_factory=lambda: uuid4().hex)
    state: str = "pending_navigation"
    reason: str = ""
    staged_count: int = 0


class AskHandoff:
    def __init__(self) -> None:
        self.pending: PendingAsk | None = None
        self.returned: tuple[str, ReadingBookmark] | None = None

    def begin(self, request: PendingAsk) -> bool:
        if self.pending is not None:
            return False
        self.returned = None
        self.pending = request
        return True

    def clear(self) -> None:
        self.pending = None
        self.returned = None

    def back(self) -> tuple[str, ReadingBookmark] | None:
        request = self.pending
        if request is None:
            return None
        self.returned = request.identity.key, request.bookmark
        self.pending = None
        return self.returned

    def observe(self, identity: Identity | None, *, terminal: bool, changes: bool) -> bool:
        request = self.pending
        if request is None:
            return False
        if identity is None or not request.identity.matches(identity):
            self.clear()
            return False
        if changes and request.state == "pending_navigation":
            return False
        if not terminal:
            self.pending = None
            return False
        if request.state == "pending_navigation":
            request.state = "waiting_for_readiness"
        return request.state == "waiting_for_readiness"

    async def drain(self, session: Any, reason: str = "", *, replace: bool = False) -> None:
        request = self.pending
        if request is None or request.state not in ("waiting_for_readiness", "draft_conflict"):
            return
        if reason or session is None:
            request.reason = reason or "Waiting for the terminal."
            return
        reason = session.question_readiness
        if reason:
            request.reason = reason
            return
        # No await between eligibility, native reconciliation, occupancy and claim.
        request.state = "claimed"
        reason = session.stage_question(request.prompt, replace=replace)
        if reason:
            request.state = "draft_conflict" if reason == "draft_conflict" else "refused"
            request.reason = "" if reason == "draft_conflict" else reason
            return
        if replace:
            request.staged_count = len(session.store.messages)
            request.state = "staged"
            return
        try:
            status = await session.on_send(prompt_only=True)
        except asyncio.CancelledError:
            # The send may or may not have landed; never settled, never resent.
            request.state = "uncertain"
            request.reason = "Sending was interrupted."
            raise
        except Exception:
            request.state = "refused"
            request.reason = "Could not send the question."
            raise
        if status in ("unknown", "superseded"):
            # Superseded: the command may have reached the agent; asking again could duplicate it.
            request.state = "uncertain"
            request.reason = ""
        elif isinstance(status, str) and status.startswith("delivered"):
            request.state = "settled"
            request.reason = ""
        else:  # failed, ignored, blocked review, or anything unrecognised
            request.state = "refused"
            request.reason = "Could not send the question."
