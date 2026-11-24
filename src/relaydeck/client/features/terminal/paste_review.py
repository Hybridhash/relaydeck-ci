"""Generation-bound paste review and confirmation. The review captures framing and writer
generation at request time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from relaydeck.client.models.input.paste_policy import (
    OUTCOME_INSERT,
    OUTCOME_REJECTED,
    OUTCOME_REVIEW,
    PasteError,
    PasteReview,
    character_count,
    confirm_chunks,
    insert_chunks,
    line_count,
    preview,
    validate,
)

if TYPE_CHECKING:
    from relaydeck.client.ports.terminal import TerminalController

REQUEST_INSERTED = "inserted"
REQUEST_REVIEW = "review"
REQUEST_REJECTED = "rejected"
REQUEST_BLOCKED = "blocked"


BLOCKED_MESSAGE = "Terminal is not accepting input: no live control writer."
STALE_MESSAGE = "Terminal session changed; review the paste again. Nothing was sent."
NO_REVIEW_MESSAGE = "No paste awaiting review."


@dataclass(frozen=True)
class PendingPaste:
    text: str
    generation: int
    bracketed: bool
    review: PasteReview


class PasteReviewSession:
    """One generation-bound review at a time. `generation_probe` returns the current writer
    generation, or None when no writer is live.
    """

    def __init__(
        self,
        generation_probe: Callable[[], int | None],
        sender: Callable[[bytes], Awaitable[None]],
    ) -> None:
        self._probe = generation_probe
        self._sender = sender
        self._pending: PendingPaste | None = None
        self._error: str | None = None

    @property
    def pending(self) -> PendingPaste | None:
        return self._pending

    @property
    def error(self) -> str | None:
        return self._error

    async def request_paste(
        self,
        text: str,
        bracketed: bool,
        *,
        require_review: bool = False,
        from_app: bool = False,
    ) -> str:
        """Classify and act: single-line inserts now, multiline opens a
        generation-bound review, unsafe controls reject, and a missing
        writer reports blocked. Only the insert path sends."""
        self._error = None
        generation = self._probe()
        if generation is None:
            self._error = BLOCKED_MESSAGE
            return REQUEST_BLOCKED
        validation = validate(text, bracketed)
        if validation.outcome == OUTCOME_INSERT and (require_review or from_app) and text:
            self._pending = PendingPaste(
                text=text,
                generation=generation,
                bracketed=bracketed,
                review=PasteReview(
                    line_count(text), character_count(text), preview(text), from_app=from_app
                ),
            )
            return REQUEST_REVIEW
        if validation.outcome == OUTCOME_INSERT:
            for chunk in insert_chunks(text):
                await self._sender(chunk)
            return REQUEST_INSERTED
        if validation.outcome == OUTCOME_REVIEW:
            assert validation.review is not None  # noqa: S101
            self._pending = PendingPaste(
                text=text,
                generation=generation,
                bracketed=validation.bracketed,
                review=replace(validation.review, from_app=from_app),
            )
            return REQUEST_REVIEW
        assert validation.outcome == OUTCOME_REJECTED  # noqa: S101
        self._pending = None
        self._error = validation.reason or "rejected"
        return REQUEST_REJECTED

    async def confirm_paste(self) -> bool:
        """Send the exact reviewed bytes iff the bound generation is still current. Stale
        generation, dead writer, or no pending review fails closed: pending is cleared and
        nothing is sent.
        """
        pending = self._pending
        if pending is None:
            self._error = NO_REVIEW_MESSAGE
            return False
        if self._probe() != pending.generation:
            self._pending = None
            self._error = STALE_MESSAGE
            return False
        chunks = confirm_chunks(pending.text, pending.bracketed)
        self._pending = None
        for chunk in chunks:
            await self._sender(chunk)
        return True

    def cancel_paste(self) -> None:
        """Drop the pending review. Provably sends nothing."""
        self._pending = None

    def clear_error(self) -> None:
        self._error = None


def bind_review_session(controller: TerminalController) -> PasteReviewSession:
    """Wire a session to the sole current writer: the controller's live
    control-lease generation probe plus its text-frame sender."""
    return PasteReviewSession(
        generation_probe=controller.paste_generation,
        sender=controller.send_paste_chunk,
    )


__all__ = [
    "REQUEST_BLOCKED",
    "REQUEST_INSERTED",
    "REQUEST_REJECTED",
    "REQUEST_REVIEW",
    "PasteError",
    "PasteReviewSession",
    "PendingPaste",
    "bind_review_session",
]
