"""Gateway transport and terminal socket interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from relaydeck.lease_diagnostics import LeaseTrace


class TransportError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = True,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details


class CommandNotAccepted(TransportError):
    """A refusal before durable acceptance, independent of transport retry policy."""


class GatewayTransport(ABC):
    async def usage(
        self,
        host_id: str,
        *,
        days: int | None = None,
        start: str | None = None,
        end: str | None = None,
        space_id: str | None = None,
        cursor: str | None = None,
        bucket: str | None = None,
        if_none_match: str | None = None,
    ) -> dict[str, Any]:
        """One page of the host's usage report. Transports that cannot read it report that."""
        raise TransportError("usage_unavailable", "usage is not available", False)

    async def git_summaries(self, host_id: str, agent_ids: Sequence[str]) -> dict[str, Any]:
        """Branch and change summaries for agents. Transports without the feature report that."""
        raise TransportError("unsupported_capability", "changes are not available", False)

    async def git_changes(
        self, host_id: str, worktree_id: str, *, scope: str, cursor: str | None = None
    ) -> dict[str, Any]:
        """One page of a worktree's changed files."""
        raise TransportError("unsupported_capability", "changes are not available", False)

    async def git_file_diff(
        self,
        host_id: str,
        worktree_id: str,
        file_id: str,
        *,
        cursor: str | None = None,
        load: bool = False,
    ) -> dict[str, Any]:
        """One page of one file's diff; `load` confirms a large diff."""
        raise TransportError("unsupported_capability", "changes are not available", False)

    async def git_graph(
        self, host_id: str, worktree_id: str, *, mode: str = "all", cursor: str | None = None
    ) -> dict[str, Any]:
        raise TransportError("unsupported_capability", "changes are not available", False)

    async def git_commit_changes(
        self, host_id: str, worktree_id: str, commit_id: str, *, cursor: str | None = None
    ) -> dict[str, Any]:
        """One page of the files a commit changed against its first parent."""
        raise TransportError("unsupported_capability", "changes are not available", False)

    async def git_bases(
        self, host_id: str, worktree_id: str, *, cursor: str | None = None
    ) -> dict[str, Any]:
        """The branches a base can be chosen from."""
        raise TransportError("unsupported_capability", "changes are not available", False)

    async def git_set_base(self, host_id: str, worktree_id: str, base_id: str) -> dict[str, Any]:
        """Save the base for a worktree (a RelayDeck preference, not a git setting)."""
        raise TransportError("unsupported_capability", "changes are not available", False)

    @abstractmethod
    async def me(self) -> dict[str, Any]: ...

    @abstractmethod
    async def hosts(self) -> dict[str, Any]: ...

    @abstractmethod
    async def snapshot(self, host_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def agents(
        self,
        host_id: str,
        limit: int = 50,
        page_token: str = "",
        *,
        project: str = "",
        status: str = "",
        query: str = "",
    ) -> dict[str, Any]: ...

    @abstractmethod
    def stream(self, host_id: str, cursor: str) -> AsyncGenerator[SseFrame]: ...

    @abstractmethod
    async def submit_command(self, command: dict[str, Any]) -> dict[str, Any]: ...

    @abstractmethod
    async def command_status(self, host_id: str, command_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def terminal_ticket(self, lease_id: str) -> dict[str, Any]: ...

    async def create_attachment(
        self,
        host_id: str,
        terminal_id: str,
        *,
        kind: str,
        takeover: bool,
        cols: int = 80,
        rows: int = 24,
    ) -> dict[str, Any]:
        """POST an explicit upstream attach authorization.
        Concrete default raises: scripted fakes override only what
        they drive."""
        raise NotImplementedError

    async def create_lease(self, attachment_id: str, *, mode: str) -> dict[str, Any]:
        """Issue a view/control lease on an owned attachment."""
        raise NotImplementedError

    async def renew_lease(self, lease_id: str) -> dict[str, Any]:
        """Foreground renewal of a held lease."""
        raise NotImplementedError


class TerminalSocket(ABC):
    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def send_bytes(self, data: bytes) -> None: ...

    @abstractmethod
    async def send_text(self, data: str) -> None: ...

    @abstractmethod
    async def receive(self) -> tuple[str, Any]:
        """Returns ("bytes", payload) or ("text", payload)."""

    @abstractmethod
    async def close(self) -> None: ...


class TerminalStream(Protocol):
    """Stream state and operations used by the terminal controller."""

    reset_to_live: bool

    @property
    def epoch(self) -> str: ...

    @property
    def next_offset(self) -> int: ...

    @property
    def offered_offset(self) -> int | None: ...

    @property
    def reopening(self) -> bool: ...

    @property
    def _socket(self) -> TerminalSocket | None: ...

    @property
    def _input_seq(self) -> int: ...

    async def open(
        self, lease_id: str, generation: int, ticket: str, epoch: str = "", offset: int = 0
    ) -> None: ...

    async def run(self) -> None: ...

    async def close(self) -> None: ...

    def resume_query(self) -> str: ...

    async def resize(self, cols: int, rows: int) -> bool: ...

    async def send_input(self, data: bytes) -> None: ...


class TerminalStreamFactory(Protocol):
    """Construct the controller's stream with its existing callbacks and diagnostics."""

    def __call__(
        self,
        *,
        socket_factory: Callable[[str], TerminalSocket],
        on_output: Callable[[int, bytes], None],
        on_control: Callable[[dict[str, Any]], None],
        on_gap: Callable[[str], None],
        on_reset: Callable[[str], None],
        on_input_failure: Callable[[int, int], None],
        diagnostics: LeaseTrace | None,
        on_baseline: Callable[[dict[str, Any]], Awaitable[bool]],
    ) -> TerminalStream: ...


@dataclass(frozen=True)
class SseFrame:
    event: str
    data: str
