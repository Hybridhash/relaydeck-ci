"""Load and save device-local presentation preferences."""

from __future__ import annotations

import asyncio
from dataclasses import fields, replace
from typing import Any

import flet as ft

from relaydeck.client.models import preferences as prefs_store
from relaydeck.client.models.preferences import TerminalPrefs


def use_preferences(*, preferences: Any) -> tuple[Any, ...]:
    """Device-local terminal preferences: loaded once, saved through the platform store."""
    # Save device-local preferences when a platform store exists; otherwise keep session values.
    prefs, set_prefs_state = ft.use_state(TerminalPrefs())

    prefs_saved, set_prefs_saved = ft.use_state(True)

    prefs_box: Any = ft.use_ref(None)

    prefs_ref: Any = ft.use_ref(TerminalPrefs())

    prefs_edited: Any = ft.use_ref(False)

    prefs_loaded: Any = ft.use_ref(False)

    prefs_loading: Any = ft.use_ref(False)

    # Fields changed before the stored document arrived; merged onto it on load
    # so an early edit can never overwrite the saved document with defaults.
    prefs_pending: Any = ft.use_ref({})

    prefs_save_lock: Any = ft.use_ref(lambda: asyncio.Lock())

    prefs_ref.current = prefs

    def persist(value: TerminalPrefs) -> None:
        store = prefs_box.current
        if not store:
            return

        async def save() -> None:
            async with prefs_save_lock.current:
                if value != prefs_ref.current:
                    return
                saved = await prefs_store.save(store.set, value)
                if value == prefs_ref.current:
                    set_prefs_saved(saved)

        asyncio.create_task(save())

    def start_load(store: Any) -> None:
        if prefs_loaded.current or prefs_loading.current:
            return
        prefs_loading.current = True

        async def load() -> None:
            try:
                loaded = await prefs_store.try_load(store.get)
            finally:
                prefs_loading.current = False
            if loaded is None:
                # A failed read is not hydration: the saved document may
                # still exist unseen. Keep every pending edit in memory and
                # persist nothing until a read succeeds (the next edit
                # retries the read).
                set_prefs_saved(False)
                return
            prefs_loaded.current = True
            pending = dict(prefs_pending.current)
            prefs_pending.current = {}
            if pending:
                merged = replace(loaded, **pending)
                set_prefs_state(merged)
                prefs_ref.current = merged
                persist(merged)
            elif (
                not prefs_edited.current
                and loaded != prefs_ref.current
                and prefs_ref.current == TerminalPrefs()
            ):
                set_prefs_state(loaded)

        asyncio.create_task(load())

    def load_prefs() -> None:
        if prefs_box.current is None:
            try:
                prefs_box.current = preferences or ft.SharedPreferences()
            except Exception:  # noqa: BLE001 - no page services (tests)
                prefs_box.current = False
        store = prefs_box.current
        if not store:
            prefs_loaded.current = True
            prefs_pending.current = {}
            set_prefs_saved(False)
            return
        start_load(store)

    ft.use_effect(load_prefs, dependencies=[])

    def set_prefs(value: TerminalPrefs) -> None:
        if not prefs_loaded.current:
            for field in fields(TerminalPrefs):
                changed = getattr(value, field.name)
                if changed != getattr(prefs_ref.current, field.name):
                    prefs_pending.current[field.name] = changed
        prefs_edited.current = True
        set_prefs_state(value)
        prefs_ref.current = value
        if not prefs_loaded.current:
            if prefs_box.current:
                start_load(prefs_box.current)  # recovery: retry a failed read
            return  # persisted once, merged, when a successful load lands
        persist(value)

    return (prefs, prefs_ref, prefs_saved, set_prefs)
