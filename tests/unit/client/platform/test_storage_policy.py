"""Storage policy follows the native credential adapter and production draft keys."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from relaydeck.client.app.native.runtime import IDENTITY_ORIGIN_KEY, ScopedDrafts
from relaydeck.client.features.auth import service as auth
from relaydeck.client.features.composer.persist import draft_key, pause_persist, resume_restore
from relaydeck.client.features.pairing.service import PENDING_KEY, VERIFIER_PREFIX
from relaydeck.client.models.preferences import PREFS_KEY, TerminalPrefs, save
from relaydeck.client.platform.flet.secure_storage import KEY_PREFIX, FletSecureCredentialStore
from relaydeck.client.platform.session_drafts import session_drafts

POLICY = Path(__file__).resolve().parents[4] / "docs/development/storage.md"


class SecureService:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.enabled = True

    async def get_availability(self) -> bool:
        return self.enabled

    async def set(self, key: str, value: str, **kwargs: Any) -> None:
        self.values[key] = value

    async def get(self, key: str, **kwargs: Any) -> str | None:
        return self.values.get(key)


def test_documented_secure_keys_are_the_real_adapter_keys() -> None:
    async def scenario() -> None:
        service = SecureService()
        secure = FletSecureCredentialStore(service)
        note = POLICY.read_text()
        for key in (
            auth.ACCESS_KEY,
            auth.REFRESH_KEY,
            auth.VERIFIER_KEY,
            auth.STATE_KEY,
            auth.EXPIRES_KEY,
            IDENTITY_ORIGIN_KEY,
            PENDING_KEY,
            VERIFIER_PREFIX + "random",
        ):
            documented = VERIFIER_PREFIX if key.startswith(VERIFIER_PREFIX) else key
            assert f"`{documented}" in note
            await secure.set(key, "sensitive value")
            assert service.values[KEY_PREFIX + key] == "sensitive value"
        drafts = ScopedDrafts(secure, "account")
        snapshot = {"draft": "sensitive draft", "markers": []}
        report = await pause_persist(drafts, draft_key("terminal"), snapshot)
        assert report.persisted
        stored = (await resume_restore(drafts, draft_key("terminal"))).snapshot
        assert stored == snapshot
        assert any(key.startswith(KEY_PREFIX + "draft-") for key in service.values)
        prefs: dict[str, str] = {}

        async def set_pref(key: str, value: str) -> None:
            prefs[key] = value

        assert await save(set_pref, TerminalPrefs())
        assert set(prefs) == {PREFS_KEY}
        assert "sensitive" not in str(prefs)
        assert f"`{PREFS_KEY}`" in note
        before = dict(service.values)
        service.enabled = False
        outage = await pause_persist(drafts, draft_key("terminal"), {"draft": "new secret"})
        assert not outage.persisted and outage.held_snapshot == {"draft": "new secret"}
        assert outage.capability_error
        assert service.values == before
        assert set(prefs) == {PREFS_KEY}
        assert session_drafts(secure) is secure

    asyncio.run(scenario())
