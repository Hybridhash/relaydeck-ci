"""Finite presentation cases for the design-system catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

State = Literal["normal", "focus", "error", "disabled", "loading", "long-text"]
Theme = Literal["light", "dark"]
WIDTHS = (320, 768, 1440)
THEMES: tuple[Theme, ...] = ("light", "dark")
STATES: tuple[State, ...] = ("normal", "focus", "error", "disabled", "loading", "long-text")
LONG_TEXT = (
    "An unusually long example label that must remain readable at a narrow viewport "
    "and enlarged text settings."
)


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    module: str
    symbol: str
    focusable: bool = False
    palette: bool = False
    native_error: bool = False

    @property
    def source(self) -> str:
        return f"relaydeck.client.design_system.{self.module}.{self.symbol}"


ENTRIES = (
    Entry("state-pill", "controls", "StatePill"),
    Entry("pill-button", "controls", "PillButton", True),
    Entry("segmented-switch", "controls", "SegmentedSwitch", True),
    Entry("prompt-body", "controls", "prompt_body", True),
    Entry("notice-panel", "controls", "NoticePanel", True),
    Entry("message-box", "notices", "message_box", True),
    Entry("toggle-switch", "controls", "ToggleSwitch", True),
    Entry("setting-row", "controls", "SettingRow", True),
    Entry("settings-panel", "controls", "SettingsPanel", True),
    Entry("stepper", "controls", "Stepper", True),
    Entry("action-carrier", "primitives", "action_button", True),
    Entry("status-pill", "primitives", "StatusPill"),
    Entry("count-tile", "primitives", "CountTile"),
    Entry("round-icon-button", "primitives", "RoundIconButton", True),
    Entry("dotted-ground", "primitives", "DottedGround"),
    Entry("section-header", "headers", "SectionHeader"),
    Entry("action-button", "primitives", "ActionButton", True),
    Entry("filter-field", "inputs", "FilterField", True, native_error=True),
    Entry("mode-button", "input_chrome", "mode_button", True, True),
    Entry("loading-bars", "loading", "LoadingBars"),
    Entry("bottom-bar", "navigation", "ConsoleBottomBar", True),
    Entry("console-rail", "navigation", "ConsoleRail", True),
    Entry("status-dot", "status", "StatusDot", palette=True),
    Entry("surface-frame", "surfaces", "SurfaceFrame", palette=True),
    Entry("muted-text", "surfaces", "MutedText", palette=True),
    Entry("action-toast", "toast", "ActionToast", native_error=True),
    Entry("interface-text", "typography", "ui", palette=True),
    Entry("live-region", "accessibility.announcer", "LiveRegion"),
    Entry("placed-list", "lists.virtual_list", "PlacedListView", True),
    Entry("virtual-list", "lists.virtual_list", "VirtualList", True),
    Entry("sheet", "sheets", "Sheet", True, True),
    Entry("dialog", "dialogs", "Dialog", True, True),
    Entry("screen-header", "headers", "ScreenHeader", palette=True),
    Entry("badge", "badges", "Badge", palette=True),
    Entry("stat-tile", "stat_tiles", "StatTile", palette=True),
    Entry("notice", "notices", "Notice", True, True),
    Entry("text-input", "inputs", "TextInput", True, True, True),
    Entry("search-field", "inputs", "SearchField", True, True, True),
    Entry("button", "buttons", "Button", True, True),
    Entry("spinner", "loading_spinner", "Spinner", palette=True),
)


@dataclass(frozen=True, slots=True)
class Case:
    entry: str
    width: int = 320
    theme: Theme = "light"
    state: State = "normal"

    def __post_init__(self) -> None:
        if self.entry not in {entry.id for entry in ENTRIES}:
            raise ValueError("Unknown catalog entry")
        if self.width not in WIDTHS or self.theme not in THEMES or self.state not in STATES:
            raise ValueError("Unknown catalog presentation")

    @property
    def key(self) -> str:
        return f"{self.entry}-{self.width}-{self.theme}-{self.state}"

    @property
    def definition(self) -> Entry:
        return next(entry for entry in ENTRIES if entry.id == self.entry)

    @property
    def dark(self) -> bool:
        return self.theme == "dark"

    @property
    def label(self) -> str:
        return LONG_TEXT if self.state == "long-text" else "Example label"

    @property
    def note(self) -> str:
        notes = []
        if not self.definition.palette:
            notes.append("Existing fixed console tokens; theme changes the surrounding canvas.")
        if self.state == "focus" and not self.definition.focusable:
            notes.append(
                "No programmatic focus target; inspect keyboard behavior locally if actionable."
            )
        if self.state == "error" and not self.definition.native_error:
            notes.append("Error context; the control has no validation-error property.")
        if self.state == "loading" and self.entry != "loading-bars":
            notes.append("Loading context; the control has no loading property.")
        if self.state == "long-text" and self.entry in ("loading-bars", "status-dot"):
            notes.append("No text-bearing property; long text is not applicable.")
        return " ".join(notes)


def cases() -> tuple[Case, ...]:
    return tuple(
        Case(entry.id, width, theme, state)
        for entry in ENTRIES
        for width in WIDTHS
        for theme in THEMES
        for state in STATES
    )
