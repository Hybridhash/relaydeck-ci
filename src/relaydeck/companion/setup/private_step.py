"""Offer private config, secrets, connector key and enrollment as one consented operation. Hold
the companion state lock throughout the owner-only writes and never display secret values.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from relaydeck.companion import config_edit
from relaydeck.companion.app.lifecycle import AlreadyRunning
from relaydeck.companion.config import CompanionConfig
from relaydeck.companion.setup.context import SetupContext
from relaydeck.companion.setup.files import apply_text_result, create_config, template_text
from relaydeck.companion.setup.private_state import (
    CONFLICT,
    CREATE,
    OWNER_ACCOUNT,
    PRESENT,
    REFUSED,
    REPLACE,
    REWRITE,
    Findings,
    config_paths,
    inspect,
)
from relaydeck.companion.setup.steps import (
    BLOCKED,
    DONE,
    TODO,
    Outcome,
    Plan,
    Status,
    Step,
)
from relaydeck.connector.enrollment import (
    ConnectorCreds,
    ConnectorKeys,
    EnrollmentError,
    UncertainWriteError,
    generate_keys,
    publish_keys,
    save_creds,
)
from relaydeck.gateway.features.access.api import EnrollmentStore, HostStore, RedeemRefusal, redeem
from relaydeck.host.files.descriptors import PrivateFileError, create_private_path
from relaydeck.host.files.private import read_private
from relaydeck.host.security.enrollment import sign_proof

STOP_WAIT_S = 15.0
INVITATION_TTL_MS = 120_000
SECRET_BYTES = 32


def _paths_of(f: Findings) -> dict[str, Path]:
    assert f.paths is not None  # noqa: S101
    return {
        "config": Path(),
        "cookie secret": f.paths.cookie,
        "cursor secret": f.paths.cursor,
        "connector key": Path(),
        "enrollment": f.paths.credentials,
    }


def _describe(ctx: SetupContext, f: Findings) -> list[str]:
    lines = ["Private files (owner-only; no value is shown):"]
    where = _paths_of(f) if f.paths is not None else {}
    for part in f.parts:
        target = ""
        if part.label == "config":
            target = ctx.short(ctx.config_path)
        elif part.label == "connector key":
            target = ctx.short(ctx.key_file)
        elif part.label in where:
            target = ctx.short(where[part.label])
        verb = {
            CREATE: "will be created",
            REWRITE: f"has the template address; will be set to {f.new_origin}",
            REPLACE: f"will be replaced ({part.note})",
            PRESENT: part.note,
            CONFLICT: part.note,
            REFUSED: part.note,
        }[part.state]
        if part.label == "config" and part.state == CREATE:
            verb = f"will be created with the address {f.new_origin}"
        if part.label == "enrollment" and part.state == CREATE:
            verb = "will be enrolled with this Mac's own registry"
        lines.append(f"  {part.label:<14} {target}: {verb}")
    return lines


def _rewrite_question(f: Findings) -> str:
    return f"Set this Mac's address ({f.new_origin}) in the config? [y/N] "


def _question(ctx: SetupContext, f: Findings) -> str:
    if f.replaces:
        extra = (
            " Devices will need to pair again."
            if ctx.environ.get("RELAYDECK_DURABLE_SIGNINS") == "1"
            else ""
        )
        return (
            "Re-enroll this Mac? Its current connector token stops working and a new one is "
            f"issued.{extra} [y/N] "
        )
    if f.file_work:
        return "Create these private files and enroll this Mac's connector? [y/N] "
    return _rewrite_question(f)


class PrivateFilesStep(Step):
    name = "Private files"
    core = True

    def check(self, ctx: SetupContext) -> Status:
        found = inspect(ctx)
        bad = found.blocked
        if bad is not None:
            state = BLOCKED if bad.state == REFUSED else "conflict"
            return Status(state, f"{bad.label}: {bad.note}")
        if not found.work:
            return Status(DONE, lines=tuple(_describe(ctx, found)))
        return Status(TODO)

    def plan(self, ctx: SetupContext) -> Plan:
        found = inspect(ctx)
        return Plan(tuple(_describe(ctx, found)), _question(ctx, found))

    def declined(self, ctx: SetupContext) -> Outcome:
        return Outcome(("Private files: not authorized; nothing was written.",), exit_code=1)

    def apply(self, ctx: SetupContext) -> Outcome:
        found = inspect(ctx)
        if found.blocked is not None:
            return Outcome((f"{found.blocked.label}: {found.blocked.note}",), exit_code=1)
        lines: list[str] = []
        stopped = self._config(ctx, found, lines)
        if stopped is not None:
            return Outcome(
                tuple(lines), stopped.changed, stopped.exit_code, uncertain=stopped.uncertain
            )
        changed = found.config.state in (CREATE, REWRITE)
        if changed:
            ctx.committed.append(f"{self.name}: config")
        done = {"config": {CREATE: "created", REWRITE: "address set"}.get(found.config.state, "")}
        done = {k: v for k, v in done.items() if v}
        if changed and ctx.reload() is None:
            lines.append("config: it does not load after the change; fix it by hand")
            return Outcome(tuple(lines), changed=True, exit_code=1)
        after = inspect(ctx)
        if not any(p.state in (CREATE, REPLACE) for p in after.parts[1:]):
            lines.extend(self._report(after, done))
            return Outcome(tuple(lines), changed=changed)
        assert ctx.config is not None  # noqa: S101
        bound = config_paths(ctx.config)
        with ExitStack() as stack:
            problem = self._hold_state(ctx, bound.state_dir, stack)
            if problem is not None:
                lines.append(problem)
                return Outcome(tuple(lines), changed=changed, exit_code=1)
            fresh = inspect(ctx)
            if fresh.blocked is not None:
                lines.append(f"{fresh.blocked.label}: {fresh.blocked.note}")
                return Outcome(tuple(lines), changed=changed, exit_code=1)
            if fresh.paths != bound or ctx.config is None:
                lines.append(
                    "The config changed while setup was starting; run relaydeck setup again. "
                    "Nothing more was written."
                )
                return Outcome(tuple(lines), changed=changed, exit_code=1)
            return self._files(ctx, ctx.config, fresh, lines, done, changed)

    def _config(self, ctx: SetupContext, f: Findings, lines: list[str]) -> Outcome | None:
        """Create or fix the config; an Outcome means setup must stop here."""
        if f.config.state == CREATE:
            assert f.new_origin is not None  # noqa: S101
            text = config_edit.set_top_level(
                template_text(), "advertised_origin", config_edit.render_string(f.new_origin)
            )
            failure = create_config(ctx.config_path, text)
            if failure is not None:
                lines.append(f"config: not created ({failure})")
                return Outcome(exit_code=1)
            return None
        if f.config.state == REWRITE:
            assert f.new_origin is not None  # noqa: S101
            if f.file_work and not ctx.confirm(_rewrite_question(f)):
                lines.append(
                    "config: it still has the template address; set advertised_origin to "
                    f"{f.new_origin} yourself, or answer y next time"
                )
                return Outcome(exit_code=1)
            old = read_private(ctx.config_path)
            new = config_edit.set_top_level(
                old, "advertised_origin", config_edit.render_string(f.new_origin)
            )
            result = apply_text_result(
                ctx.config_path, old, new, action="origin", entries=[{"field": "advertised_origin"}]
            )
            lines.extend(result.lines)
            if result.state == "failed":
                return Outcome(exit_code=1)
            if result.state == "uncertain":
                return Outcome(changed=True, exit_code=1, uncertain=True)
        return None

    def _hold_state(self, ctx: SetupContext, state_dir: Path, stack: ExitStack) -> str | None:
        try:
            stack.enter_context(ctx.state_lock(state_dir))
            return None
        except AlreadyRunning:
            pass
        except (OSError, ValueError):
            return "The state folder cannot be locked safely; nothing was written."
        try:
            state = ctx.companion_state(state_dir).get("state")
        except (OSError, ValueError):
            state = "starting"
        if state in ("starting", "unresponsive", None, "stopped"):
            return (
                "Setup busy: another RelayDeck process owns the state folder. Wait for it, then "
                "run setup again; nothing was written."
            )
        if not ctx.confirm("Stop RelayDeck for this step? [y/N] "):
            return "RelayDeck is running; stop it first (relaydeck stop). Nothing was written."
        ctx.request_stop(state_dir)
        deadline = ctx.clock() + STOP_WAIT_S
        while True:
            try:
                stack.enter_context(ctx.state_lock(state_dir))
                return None
            except AlreadyRunning:
                if ctx.clock() >= deadline:
                    return "RelayDeck did not stop in time; nothing was written."
                ctx.sleep(0.2)

    def _files(
        self,
        ctx: SetupContext,
        config: CompanionConfig,
        f: Findings,
        lines: list[str],
        done: dict[str, str],
        changed: bool,
    ) -> Outcome:
        assert f.paths is not None  # noqa: S101
        for part, path in ((f.cookie, f.paths.cookie), (f.cursor, f.paths.cursor)):
            if part.state != CREATE:
                continue
            data = (ctx.token_hex(SECRET_BYTES) + "\n").encode()
            try:
                create_private_path(path, data)
            except PrivateFileError as exc:
                lines.append(f"{part.label}: not created ({exc.code})")
                return Outcome(tuple(lines), True, 1, uncertain=exc.code == "uncertain")
            done[part.label] = "created"
            ctx.committed.append(f"{self.name}: {part.label}")
            changed = True
        keys = f.keys
        if f.key.state == CREATE:
            made = generate_keys()
            try:
                # One key serves every profile of this user; whichever setup published first wins.
                keys = publish_keys(made, str(ctx.key_file))
            except (EnrollmentError, OSError) as exc:
                lines.append(f"connector key: not created ({type(exc).__name__})")
                return Outcome(tuple(lines), True, 1)
            if keys == made:
                done["connector key"] = "created"
                ctx.committed.append(f"{self.name}: connector key")
                changed = True
        if f.enrollment.state in (CREATE, REPLACE):
            assert keys is not None  # noqa: S101
            problem = self._enroll(ctx, config, keys, replace=f.enrollment.state == REPLACE)
            if problem is not None:
                lines.extend(self._report(f, done))
                lines.append(problem)
                return Outcome(tuple(lines), True, 1)
            done["enrollment"] = "enrolled" if f.enrollment.state == CREATE else "re-enrolled"
            ctx.committed.append(f"{self.name}: enrollment")
            changed = True
        lines.extend(self._report(f, done))
        return Outcome(tuple(lines), changed)

    def _enroll(
        self, ctx: SetupContext, config: CompanionConfig, keys: ConnectorKeys, *, replace: bool
    ) -> str | None:
        """The registry write and the credentials write, in this process. The invitation, its
        code, the proof and the token exist only in memory and in the two owner-only files."""
        try:
            hosts = HostStore(path=config.state_dir / "hosts.json")
            if replace:
                hosts.revoke(config.host_id)
            invitations = EnrollmentStore()
            now_ms = int(ctx.now() * 1000)
            invitation = invitations.create(
                OWNER_ACCOUNT,
                config.host_id,
                keys.public_openssh,
                keys.fingerprint,
                now_ms,
                INVITATION_TTL_MS,
            )
            seed = base64.b64decode(keys.seed_b64, validate=True)
            proof = (
                base64.urlsafe_b64encode(sign_proof(seed, invitation.id, config.host_id))
                .decode()
                .rstrip("=")
            )

            async def revoke(host_id: str) -> None:
                hosts.revoke(host_id)

            result = asyncio.run(
                redeem(
                    invitations,
                    hosts,
                    invitation.id,
                    invitation.code,
                    config.host_id,
                    keys.public_openssh,
                    proof,
                    now_ms,
                    revoke,
                )
            )
        except (ValueError, OSError, EnrollmentError):
            return "enrollment: the host registry could not be written"
        if isinstance(result, RedeemRefusal):
            return f"enrollment: refused ({result.message})"
        if result["fingerprint"] != keys.fingerprint:
            hosts.revoke(config.host_id)
            return "enrollment: the registry recorded a different key; nothing was kept"
        creds = ConnectorCreds(config.advertised_origin, config.host_id, result["host_token"])
        try:
            save_creds(creds, str(config.connector_credentials))
        except (EnrollmentError, OSError) as exc:
            if not isinstance(exc, UncertainWriteError):
                try:
                    hosts.revoke(config.host_id)  # no credentials hold this token
                except (ValueError, OSError):
                    pass
            return "enrollment: the credentials could not be written"
        if replace and ctx.environ.get("RELAYDECK_DURABLE_SIGNINS") == "1":
            return self._end_restored_signins(ctx)
        return None

    def _end_restored_signins(self, ctx: SetupContext) -> str | None:
        try:
            provider = ctx.provider_factory() if ctx.provider_factory else _mac_provider()
            provider.advance_restore_generation("authentication")
        except Exception:  # noqa: BLE001 - any key-store failure is reported, never its detail
            return "enrollment: restored device sign-ins could not be ended; run setup again"
        return None

    def _report(self, f: Findings, done: dict[str, str]) -> list[str]:
        lines = ["Private files:"]
        for part in f.parts:
            lines.append(f"  {part.label}: {done.get(part.label, 'present (not changed)')}")
        return lines


def _mac_provider() -> Any:
    from relaydeck.host.persistence.keychain import MacKeyProvider

    return MacKeyProvider()
