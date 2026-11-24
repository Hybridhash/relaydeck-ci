"""Stale-draft guard shared by the controlled draft text fields. Flet applies native text edits
(paste, typing, autocorrect) to the field before it dispatches `on_change`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as data_field
from itertools import count
from typing import Any

import flet as ft

from relaydeck.client.features.composer.draft_owner import DraftFieldOwner

_EDITING_LIFETIMES = count(1)


def _editable(native: Any) -> bool:
    return (
        native is not None
        and getattr(native, "can_request_focus", False) is True
        and not getattr(native, "disabled", False)
        and getattr(native, "visible", True) is not False
    )


@dataclass(eq=False)
class DraftEditor:
    context: Any
    callback: Any
    owner: DraftFieldOwner = data_field(default_factory=lambda: DraftFieldOwner(eligible=False))
    native: Any = None
    rendered: str | None = None
    accepted: str = ""
    generation: Any = None
    native_generation: int = -1
    serial: int = 0
    key_generation: int = -1
    focused: bool = False
    restore_focus: bool = False
    selection: ft.TextSelection | None = None
    selection_text: str = ""

    @property
    def key(self) -> str:
        return f"draft-editor-{self.serial}"

    def is_current(self, lifetime: int) -> bool:
        return lifetime == self.owner.generation and self.owner.eligible and self.live()

    def focus(self, focused: bool, lifetime: int) -> bool:
        if not self.is_current(lifetime):
            return False
        self.focused = focused
        self.restore_focus = False
        return True

    def select(self, selection: Any, lifetime: int) -> None:
        if self.is_current(lifetime) and isinstance(selection, ft.TextSelection):
            self.selection = selection
            self.selection_text = str(self.native.value)

    def selection_value(self) -> ft.TextSelection | None:
        if (
            self.native_generation == self.owner.generation
            and self.native is not None
            and self.native.value == self.accepted
        ):
            selection = self.native.selection
            return selection if isinstance(selection, ft.TextSelection) else None
        if self.selection is None or self.selection_text != self.accepted:
            return None
        length = len(self.accepted.encode("utf-16-le", errors="surrogatepass")) // 2
        if self.selection.base_offset < 0 or self.selection.extent_offset < 0:
            return None
        return ft.TextSelection(
            min(self.selection.base_offset, length),
            min(self.selection.extent_offset, length),
            affinity=self.selection.affinity,
            directional=self.selection.directional,
        )

    @property
    def autofocus(self) -> bool:
        return (
            self.owner.eligible
            and self.restore_focus
            and self.native_generation != self.owner.generation
        )

    def current_generation(self) -> Any:
        read = getattr(self.callback, "field_generation", None)
        return read() if callable(read) else None

    def live(self) -> bool:
        check = getattr(self.callback, "field_live", None)
        return (
            self.owner.active
            and self.generation == self.current_generation()
            and (bool(check()) if callable(check) else True)
        )

    def fence(self, *, preserve: bool = True, retain_focus: bool = False) -> None:
        if self.owner.eligible:
            self.restore_focus = retain_focus and self.focused
            self.focused = False
        if preserve and self.owner.eligible and self.live() and _editable(self.native):
            self.select(self.native.selection, self.owner.generation)
            reconcile = getattr(self.callback, "fence_field", None)
            if callable(reconcile):
                accepted = reconcile(self.owner)
                if accepted is not None:
                    self.accepted = accepted
            elif isinstance(self.native.value, str):
                self.accepted = self.native.value
                self.callback(self.accepted)
        retire = getattr(self.callback, "retire_field", None)
        if callable(retire):
            retire(self.owner)
        self.owner.invalidate()

    def render(self, draft: str, enabled: bool) -> str:
        if not enabled:
            self.restore_focus = False
            self.focused = False
        generation = self.current_generation()
        if self.generation != generation:
            self.fence(preserve=False, retain_focus=enabled)
            self.generation = generation
        eligible = enabled and self.live()
        if self.owner.eligible and not eligible:
            self.fence(preserve=draft == self.rendered, retain_focus=enabled)
        if draft != self.rendered:
            self.accepted = draft
        elif self.owner.eligible and eligible and _editable(self.native):
            if isinstance(self.native.value, str):
                self.accepted = self.native.value
        # Re-enabling restores the last eligible value, never raw disabled input.
        if eligible and not self.owner.eligible:
            self.owner.generation += 1
        self.owner.eligible = eligible
        if self.key_generation != self.owner.generation:
            self.serial = next(_EDITING_LIFETIMES)
            self.key_generation = self.owner.generation
        self.rendered = draft
        return self.accepted

    def bind(self, native: Any) -> None:
        self.native = native
        self.native_generation = self.owner.generation
        remember = getattr(self.callback, "remember_field", None)
        if callable(remember):
            remember(native, self.owner)

    def change(self, text: str, lifetime: int) -> Any:
        if lifetime != self.owner.generation:
            return None
        if not self.is_current(lifetime) or not _editable(self.native):
            if self.owner.eligible:
                self.restore_focus = self.focused and not self.live()
                self.focused = False
            self.owner.invalidate()
            retire = getattr(self.callback, "retire_field", None)
            if callable(retire):
                retire(self.owner)
            return None
        self.accepted = text
        self.select(self.native.selection, lifetime)
        return self.callback(text)

    def release(self) -> None:
        self.owner.active = False
        self.owner.invalidate()
        forget = getattr(self.callback, "forget_field", None)
        if callable(forget):
            forget(self.owner)
        self.native = None


def use_draft_editor(draft: str, callback: Any, *, enabled: bool) -> tuple[DraftEditor, str]:
    binding: Any = ft.use_ref(None)
    context = getattr(callback, "field_context", None)

    def release() -> None:
        if binding.current is not None:
            binding.current.release()

    if binding.current is None or binding.current.context is not context:
        release()
        binding.current = DraftEditor(context, callback)
    editor: DraftEditor = binding.current
    editor.callback = callback
    value = editor.render(draft, enabled)
    ft.use_effect(lambda: None, dependencies=[], cleanup=release)
    return editor, value


@ft.component
def NativeDraftField(native: Any, key: str = "draft-native") -> Any:
    """A keyed native subtree keeps retired session control IDs out of its successor."""
    return native


def keep_native_value(previous: Any, last_model: str | None, draft: str, *, focusable: bool) -> Any:
    """The value a render should send: the native field's value while the model has not
    moved and the field can take focus, otherwise the model draft."""
    if _editable(previous) and last_model == draft and focusable:
        return previous.value
    return draft


def use_draft_value(draft: str, *, focusable: bool) -> tuple[str, Callable[[Any], None]]:
    """Hook: the value to render for `draft`, and a setter to call with the rendered field.

    Call the setter with the field after building it so the next render can compare.
    """
    field_ref: Any = ft.use_ref(None)
    model_ref = ft.use_ref(draft)
    value = keep_native_value(field_ref.current, model_ref.current, draft, focusable=focusable)

    def remember(field: Any) -> None:
        field_ref.current = field
        model_ref.current = draft

    return value, remember
