"""Expose terminal overlays, selection, scrolling and picture-attachment controls."""

from relaydeck.client.features.terminal.connecting_dots import ConnectingDots
from relaydeck.client.features.terminal.haptics import on_copy, on_phone_copy, on_send
from relaydeck.client.features.terminal.paste_review import REQUEST_REJECTED, REQUEST_REVIEW
from relaydeck.client.features.terminal.paste_sheet import PasteReviewSheet
from relaydeck.client.features.terminal.picture_paste_region import (
    take_paste_props,
    wrap_with_paste_region,
)
from relaydeck.client.features.terminal.pictures import (
    attach_button,
    picture_chips,
    use_picture_attachment,
)
from relaydeck.client.features.terminal.scrolling import (
    ALT_SELECTION_HINT,
    ScrollButtons,
    last_lines,
    note_view_size,
    record_scroll_input,
    reset_alt_scroll,
    scroll_away,
    use_open_at_end,
    use_terminal_scroll,
    view_state,
)
from relaydeck.client.features.terminal.selection_sheet import SelectTextSheet

__all__ = [
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
]
