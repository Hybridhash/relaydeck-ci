"""The words the reader sees while a sent message is unconfirmed, refused or dismissed."""

from __future__ import annotations

FALLBACK_AGENT = "the agent"


def named(agent: str) -> str:
    return agent.strip() or FALLBACK_AGENT


def checking(agent: str) -> str:
    return f"Checking whether your message reached {named(agent)}…"


def not_sent(agent: str) -> str:
    return (
        f"Your message wasn't sent to {named(agent)}. "
        "It's still in the box — tap Send to try again."
    )


def too_long(agent: str) -> str:
    return f"Your message is too long to send to {named(agent)}. Shorten it and try again."


def not_allowed(agent: str) -> str:
    return f"This device isn't allowed to send messages to {named(agent)}."


def agent_gone(agent: str) -> str:
    return f"{named(agent)} was restarted or closed. Open it again, then send."


TOO_MANY = "Too many messages just now. Wait a moment, then tap Send."


def refused(agent: str, code: str | None) -> str:
    """Why a proven refusal happened when the cause will not go away by itself."""
    if code == "too_large":
        return too_long(agent)
    if code in ("forbidden", "unsupported_capability"):
        return not_allowed(agent)
    if code in ("stale_epoch", "agent_not_found"):
        return agent_gone(agent)
    if code == "rate_limited":
        return TOO_MANY
    return not_sent(agent)


def still_unconfirmed() -> str:
    return "Still not confirmed. Use Dismiss in the banner if you want to send anyway."


def earlier_unconfirmed(agent: str) -> str:
    return (
        f"Your last message to {named(agent)} hasn't been confirmed yet. "
        "Check it first so it isn't sent twice."
    )


def cannot_confirm_title(agent: str) -> str:
    return f"Couldn't confirm a message to {named(agent)}"


def cannot_confirm_body(agent: str) -> str:
    who = named(agent)
    return (
        f"It was sent too long ago for RelayDeck to check whether {who} received it. "
        f"Look in {who}'s terminal to see. Dismiss it to send new messages."
    )


DISMISS_TITLE = "Dismiss this message?"


def dismiss_body(agent: str) -> str:
    return (
        f"If {named(agent)} didn't receive it, it won't be sent. "
        "Anything in your message box stays."
    )


def dismissed(agent: str) -> str:
    return f"Dismissed. You can send to {named(agent)} again."


def summary(messages: int, others: int) -> str:
    """The banner's count of what needs the reader's attention."""
    total = messages + others
    if others:
        return f"{total} {'item needs' if total == 1 else 'items need'} your attention"
    return f"{total} {'message needs' if total == 1 else 'messages need'} your attention"


STATUS_UNAVAILABLE = "Couldn't check right now. Nothing was sent again."
DISMISS_UNAVAILABLE = "Couldn't dismiss this message. Nothing was sent."
EARLIER_UNCONFIRMED_REQUEST = (
    "An earlier request hasn't been confirmed yet. Check it first so nothing is done twice."
)
KEPT_NOTICE = "Your message is kept in the box. Nothing is sent again on its own."
