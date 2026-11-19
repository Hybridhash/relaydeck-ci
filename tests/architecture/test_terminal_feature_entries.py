"""Feature entry lists and canonical definitions stay explicit."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "commands.api": ["CommandObserver", "PendingCommands"],
    "composer.api": [
        "CommandSubscriber",
        "ComposerStore",
        "DraftFieldOwner",
        "PausePersistReport",
        "ResumeRestoreReport",
        "SEND_DELIVERED_VIA_ATTACH",
        "SEND_DELIVERED_VIA_PROMPT",
        "SEND_IGNORED",
        "SEND_SUPERSEDED",
        "SUBMISSION_BLOCKED_INSERTED",
        "SUBMISSION_FAILED",
        "SUBMISSION_PROGRESS",
        "SUBMISSION_SENDING",
        "SUBMISSION_UNKNOWN",
        "StagedMarker",
        "bind_blocked_writer",
        "draft_key",
        "install",
        "pause_persist",
        "resume_restore",
        "unresolved",
    ],
    "composer.ui": [
        "BLOCKED_NOTICE",
        "CANCEL_LABEL",
        "COMPOSER_KEY",
        "CONFIRM_LABEL",
        "ComposerBox",
        "DRAFT_KEY",
        "ERROR_KEY",
        "FOOTER_KEY",
        "MARKERS_KEY",
        "MODE_KEY",
        "NOTICE_KEY",
        "NativeDraftField",
        "REVIEW_CANCEL_KEY",
        "REVIEW_CONFIRM_KEY",
        "REVIEW_COUNTS_KEY",
        "REVIEW_KEY",
        "REVIEW_PREVIEW_KEY",
        "REVIEW_TITLE",
        "SEND_KEY",
        "UNKNOWN_NOTICE",
        "UNRESOLVED_HINT",
        "VALIDATION_KEY",
        "add_key_listener",
        "keep_focus",
        "keyboard_toggle",
        "markers_line",
        "use_composer_focus_log",
        "use_draft_editor",
        "use_draft_value",
        "use_send_shortcut",
        "working_shimmer",
    ],
    "changes.api": [
        "Anchor",
        "ChangesActions",
        "ChangesFailure",
        "ChangesModel",
        "ChangesRepository",
        "ChangesSource",
        "CommitKey",
        "CommitOpening",
        "CommitReading",
        "CopyForm",
        "CopyLine",
        "DEFAULT_RETRY_AFTER",
        "GraphAnchor",
        "GraphRow",
        "HunkGroup",
        "MAX_RETRY_AFTER",
        "ReadingActions",
        "ReadingBookmark",
        "Request",
        "ReviewStateStore",
        "Scope",
        "ViewContext",
        "ViewKey",
        "anchor_for",
        "apply_summary",
        "begin_refresh",
        "close_base_picker",
        "close_commit",
        "commit_actions",
        "commit_receive",
        "earlier_page",
        "fail",
        "graph_anchor",
        "hunk_groups",
        "load_diff",
        "open_base_picker",
        "open_commit",
        "project",
        "receive_base_saved",
        "receive_bases",
        "receive_diff",
        "receive_files",
        "receive_graph",
        "request_bases",
        "request_diff",
        "request_files",
        "request_graph",
        "request_save_base",
        "reselecting",
        "resident_pages",
        "resolve",
        "resume",
        "select",
        "select_file",
        "set_anchor",
        "set_graph_mode",
        "set_scope",
        "set_view",
        "step_file",
        "target_for",
    ],
    "ask_agent.api": [
        "AskHandoff",
        "AskScope",
        "DEFAULT_QUESTION",
        "Identity",
        "PRESETS",
        "PendingAsk",
        "build_prompt",
        "eligibility",
        "short_oid",
        "target",
        "validate",
    ],
    "terminal.api": [
        "ACTIVE",
        "AGENT_LIVE",
        "AgentSession",
        "CLOSE_MESSAGE",
        "CLOSE_TITLE",
        "COMPOSER_HEIGHT",
        "CONTEXT_STRIP_HEIGHT",
        "DEFAULT_GEOMETRY",
        "DetailBudget",
        "EMULATOR_FRESH",
        "FIELD_LINE_HEIGHT",
        "ForegroundRecovery",
        "KeyInputSession",
        "PHONE_DOCK_HEIGHT",
        "PHONE_FALLBACK_WIDTH",
        "PICTURE_HEIGHT",
        "ROUTE_DROPPED",
        "ROUTE_PTY",
        "SHELL_CONFLICT",
        "SHELL_CONNECTING",
        "SHELL_DISCONNECTED",
        "SHELL_EXITED",
        "SHELL_LIVE",
        "ScreenCache",
        "ScreenSnapshot",
        "SelectionState",
        "ShellSession",
        "ShellViewState",
        "SwitcherRow",
        "TAKEOVER_DISCLOSURE_AGENT",
        "TYPING_SPLIT_FALLBACK_WIDTH",
        "TYPING_SPLIT_GAP",
        "TYPING_SPLIT_HEADER_HEIGHT",
        "TerminalController",
        "TerminalRegistry",
        "TerminalUIResumes",
        "TerminalViewState",
        "WINDOW_PADDING",
        "connection_status",
        "copy_text",
        "display_width",
        "drive_recovery",
        "encode_key",
        "fence_hidden_writer",
        "keyboard_overlap",
        "measured_row_height",
        "normalize_range",
        "page_geometry",
        "plan_detail",
        "publish",
        "register",
        "resolved_range",
        "selected_offsets",
        "set_selection_error",
        "switcher_rows",
        "terminal_transition",
        "typing_split_active",
        "typing_split_lines",
        "typing_split_widths",
        "unregister",
        "view_actions",
        "watch_only",
        "working_count",
    ],
    "terminal.ui": [
        "ALT_SELECTION_HINT",
        "ConnectingDots",
        "PasteReviewSheet",
        "REQUEST_REJECTED",
        "REQUEST_REVIEW",
        "ScrollButtons",
        "SelectTextSheet",
        "attach_button",
        "last_lines",
        "note_view_size",
        "on_copy",
        "on_phone_copy",
        "on_send",
        "picture_chips",
        "record_scroll_input",
        "reset_alt_scroll",
        "scroll_away",
        "take_paste_props",
        "use_open_at_end",
        "use_picture_attachment",
        "use_terminal_scroll",
        "view_state",
        "wrap_with_paste_region",
    ],
}
OWNERS = {
    "commands.api": {
        "commands.observation": ["CommandObserver"],
        "commands.recovery": ["PendingCommands"],
    },
    "composer.api": {
        "composer.model": [
            "CommandSubscriber",
            "ComposerStore",
            "SEND_DELIVERED_VIA_ATTACH",
            "SEND_DELIVERED_VIA_PROMPT",
            "SEND_IGNORED",
            "SEND_SUPERSEDED",
            "SUBMISSION_BLOCKED_INSERTED",
            "SUBMISSION_FAILED",
            "SUBMISSION_PROGRESS",
            "SUBMISSION_SENDING",
            "SUBMISSION_UNKNOWN",
            "StagedMarker",
            "bind_blocked_writer",
        ],
        "composer.draft_owner": ["DraftFieldOwner"],
        "composer.persist": [
            "PausePersistReport",
            "ResumeRestoreReport",
            "draft_key",
            "pause_persist",
            "resume_restore",
        ],
        "composer.question": ["install", "unresolved"],
    },
    "composer.ui": {
        "composer.components": [
            "BLOCKED_NOTICE",
            "CANCEL_LABEL",
            "COMPOSER_KEY",
            "CONFIRM_LABEL",
            "ComposerBox",
            "DRAFT_KEY",
            "ERROR_KEY",
            "FOOTER_KEY",
            "MARKERS_KEY",
            "MODE_KEY",
            "NOTICE_KEY",
            "REVIEW_CANCEL_KEY",
            "REVIEW_CONFIRM_KEY",
            "REVIEW_COUNTS_KEY",
            "REVIEW_KEY",
            "REVIEW_PREVIEW_KEY",
            "REVIEW_TITLE",
            "SEND_KEY",
            "UNKNOWN_NOTICE",
            "UNRESOLVED_HINT",
            "VALIDATION_KEY",
            "markers_line",
            "working_shimmer",
        ],
        "composer.draft_field": ["NativeDraftField", "use_draft_editor", "use_draft_value"],
        "composer.shortcut": ["add_key_listener", "use_send_shortcut"],
        "composer.keyboard_toggle": ["keep_focus", "keyboard_toggle"],
        "composer.focus": ["use_composer_focus_log"],
    },
    "changes.api": {
        "changes.state": [
            "Anchor",
            "ChangesModel",
            "CommitKey",
            "CommitOpening",
            "CommitReading",
            "Request",
            "Scope",
        ],
        "changes.actions": ["ChangesActions", "ReadingActions"],
        "changes.source": ["ChangesFailure", "ChangesSource"],
        "changes.repository": ["ChangesRepository"],
        "changes.copy_target": [
            "CopyForm",
            "CopyLine",
            "HunkGroup",
            "hunk_groups",
            "resolve",
            "target_for",
        ],
        "changes.refresh": ["DEFAULT_RETRY_AFTER", "MAX_RETRY_AFTER"],
        "changes.graph_state": ["GraphAnchor"],
        "changes.graph_layout": ["GraphRow", "project"],
        "changes.reading_bookmark": ["ReadingBookmark"],
        "changes.review_state": ["ReviewStateStore", "ViewContext", "ViewKey"],
        "changes.diff_window": [
            "anchor_for",
            "earlier_page",
            "load_diff",
            "receive_diff",
            "request_diff",
            "resident_pages",
            "select_file",
            "set_anchor",
            "step_file",
        ],
        "changes.model": [
            "apply_summary",
            "begin_refresh",
            "close_base_picker",
            "fail",
            "open_base_picker",
            "receive_base_saved",
            "receive_bases",
            "receive_files",
            "request_bases",
            "request_files",
            "request_save_base",
            "reselecting",
            "select",
            "set_scope",
            "set_view",
        ],
        "changes.commit_model": ["close_commit", "commit_receive", "open_commit", "resume"],
        "changes.commit_actions": ["commit_actions"],
        "changes.graph_model": ["graph_anchor", "receive_graph", "request_graph", "set_graph_mode"],
    },
    "ask_agent.api": {
        "ask_agent.handoff": ["AskHandoff", "Identity", "PendingAsk"],
        "ask_agent.model": [
            "AskScope",
            "DEFAULT_QUESTION",
            "PRESETS",
            "build_prompt",
            "eligibility",
            "short_oid",
        ],
        "ask_agent.target": ["target", "validate"],
    },
    "terminal.api": {
        "terminal.switcher": ["ACTIVE", "SwitcherRow", "switcher_rows", "working_count"],
        "terminal.agent_session": ["AGENT_LIVE", "AgentSession"],
        "terminal.shell_session": [
            "CLOSE_MESSAGE",
            "CLOSE_TITLE",
            "SHELL_CONFLICT",
            "SHELL_CONNECTING",
            "SHELL_DISCONNECTED",
            "SHELL_EXITED",
            "SHELL_LIVE",
            "ShellSession",
            "ShellViewState",
        ],
        "terminal.layout_budget": [
            "COMPOSER_HEIGHT",
            "CONTEXT_STRIP_HEIGHT",
            "DetailBudget",
            "FIELD_LINE_HEIGHT",
            "PHONE_DOCK_HEIGHT",
            "PHONE_FALLBACK_WIDTH",
            "PICTURE_HEIGHT",
            "TYPING_SPLIT_FALLBACK_WIDTH",
            "TYPING_SPLIT_GAP",
            "TYPING_SPLIT_HEADER_HEIGHT",
            "WINDOW_PADDING",
            "keyboard_overlap",
            "measured_row_height",
            "plan_detail",
            "typing_split_active",
            "typing_split_lines",
            "typing_split_widths",
        ],
        "terminal.geometry": ["DEFAULT_GEOMETRY", "page_geometry"],
        "terminal.stream": ["EMULATOR_FRESH"],
        "terminal.foreground_recovery": ["ForegroundRecovery"],
        "terminal.keys": ["KeyInputSession", "encode_key"],
        "terminal.input_modes": ["ROUTE_DROPPED", "ROUTE_PTY"],
        "terminal.screen_cache": ["ScreenCache", "ScreenSnapshot"],
        "terminal.models": ["SelectionState", "TerminalViewState"],
        "terminal.sessions": ["TAKEOVER_DISCLOSURE_AGENT", "drive_recovery"],
        "terminal.controller": ["TerminalController"],
        "terminal.retention": ["TerminalRegistry"],
        "terminal.ui_resume": ["TerminalUIResumes", "terminal_transition"],
        "terminal.connection_status": ["connection_status"],
        "terminal.selection_model": [
            "copy_text",
            "display_width",
            "normalize_range",
            "resolved_range",
            "set_selection_error",
        ],
        "terminal.warm_watch": ["fence_hidden_writer", "watch_only"],
        "terminal.visual_inset": ["publish", "register", "unregister"],
        "terminal.selection_area": ["selected_offsets"],
        "terminal.callbacks": ["view_actions"],
    },
    "terminal.ui": {
        "terminal.scrolling": [
            "ALT_SELECTION_HINT",
            "ScrollButtons",
            "last_lines",
            "note_view_size",
            "record_scroll_input",
            "reset_alt_scroll",
            "scroll_away",
            "use_open_at_end",
            "use_terminal_scroll",
            "view_state",
        ],
        "terminal.connecting_dots": ["ConnectingDots"],
        "terminal.paste_review": ["REQUEST_REJECTED", "REQUEST_REVIEW"],
        "terminal.selection_sheet": ["SelectTextSheet"],
        "terminal.pictures": ["attach_button", "picture_chips", "use_picture_attachment"],
        "terminal.haptics": ["on_copy", "on_phone_copy", "on_send"],
        "terminal.picture_paste_region": ["take_paste_props", "wrap_with_paste_region"],
        "terminal.paste_sheet": ["PasteReviewSheet"],
    },
}


@pytest.mark.parametrize("entry", list(EXPECTED))
def test_entry_exports_are_exact_canonical_objects(entry: str) -> None:
    module = importlib.import_module("relaydeck.client.features." + entry)
    assert module.__all__ == EXPECTED[entry]
    for owner, names in OWNERS[entry].items():
        canonical = importlib.import_module("relaydeck.client.features." + owner)
        for name in names:
            assert getattr(module, name) is getattr(canonical, name)


@pytest.mark.parametrize("entry", list(EXPECTED))
def test_entry_contains_only_imports_and_explicit_exports(entry: str) -> None:
    path = ROOT / "src/relaydeck/client/features" / (entry.replace(".", "/") + ".py")
    nodes = ast.parse(path.read_text()).body
    for node in nodes:
        assert isinstance(node, ast.Import | ast.ImportFrom | ast.Assign | ast.Expr)
        if isinstance(node, ast.Assign):
            assert [target.id for target in node.targets] == ["__all__"]
        elif isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def test_command_links_are_exact_and_still_count_for_cycles() -> None:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    independent = next(
        contract
        for contract in contracts
        if contract["name"] == "Client features independent except recorded edges"
    )
    links = {
        "relaydeck.client.features.create.observation -> relaydeck.client.features.commands.api",
        "relaydeck.client.features.rename.rename -> relaydeck.client.features.commands.api",
    }
    assert links <= set(independent["ignore_imports"])
    for contract in contracts:
        if contract["type"] == "acyclic_siblings":
            assert not links.intersection(contract.get("ignore_imports", []))


def test_composer_owners_have_no_legacy_aliases() -> None:
    focus = importlib.import_module("relaydeck.client.features.composer.focus")
    hook = importlib.import_module("relaydeck.client.app.hooks.typing_focus")
    assert focus.use_composer_focus_log.__module__ == focus.__name__
    assert not hasattr(hook, "use_composer_focus_log")
    assert importlib.util.find_spec("relaydeck.client.features.composer.domain") is None


def test_terminal_paste_helpers_are_the_canonical_ui_exports() -> None:
    from relaydeck.client.features.terminal import api, picture_paste_region, ui

    assert ui.take_paste_props is picture_paste_region.take_paste_props
    assert ui.wrap_with_paste_region is picture_paste_region.wrap_with_paste_region
    assert not hasattr(api, "take_paste_props")
    assert not hasattr(api, "wrap_with_paste_region")


@pytest.mark.parametrize(
    "owner", ["features/terminal", "widgets/terminal", "screens/terminal", "screens/shell"]
)
def test_terminal_package_initializers_contain_only_documentation(owner: str) -> None:
    path = ROOT / "src/relaydeck/client" / owner / "__init__.py"
    for node in ast.parse(path.read_text()).body:
        assert isinstance(node, ast.Expr)
        assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def test_old_terminal_renderer_surface_and_shell_paths_are_absent() -> None:
    client = ROOT / "src/relaydeck/client"
    assert not (client / "features/terminal/screen.py").exists()
    assert not (client / "features/terminal/surfaces").exists()
    assert not (client / "features/shell").exists()


def test_permanent_feature_links_are_exact_and_cycle_visible() -> None:
    contracts = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"][
        "contracts"
    ]
    independent = next(
        contract
        for contract in contracts
        if contract["name"] == "Client features independent except recorded edges"
    )
    links = {
        "relaydeck.client.features.ask_agent.handoff -> relaydeck.client.features.changes.api",
        "relaydeck.client.features.ask_agent.target -> relaydeck.client.features.changes.api",
        "relaydeck.client.features.create.observation -> relaydeck.client.features.commands.api",
        "relaydeck.client.features.rename.rename -> relaydeck.client.features.commands.api",
        "relaydeck.client.features.terminal.agent_session -> "
        "relaydeck.client.features.composer.api",
        "relaydeck.client.features.terminal.draft_preparation -> "
        "relaydeck.client.features.composer.api",
        "relaydeck.client.features.terminal.input_modes -> relaydeck.client.features.composer.api",
        "relaydeck.client.features.terminal.session.base -> relaydeck.client.features.composer.api",
        "relaydeck.client.features.terminal.session.context -> "
        "relaydeck.client.features.composer.api",
        "relaydeck.client.features.terminal.session.keys -> relaydeck.client.features.composer.api",
        "relaydeck.client.features.terminal.sessions -> relaydeck.client.features.composer.api",
    }
    assert set(independent["ignore_imports"]) == links
    for contract in contracts:
        if contract["type"] == "acyclic_siblings":
            assert not links.intersection(contract.get("ignore_imports", []))
