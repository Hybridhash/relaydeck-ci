"""Paste confirmation sheet with a verbatim text preview."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.theme import FONT_SIZE, MIN_TOUCH
from relaydeck.client.models.input.paste_policy import PasteReview
from relaydeck.client.platform.flet.events import click

REVIEW_TITLE = "Review paste"

CONFIRM_LABEL = "Paste"

CANCEL_LABEL = "Cancel"

SHEET_KEY = "paste-review-sheet"

TITLE_KEY = "paste-review-title"

COUNTS_KEY = "paste-review-counts"

SOURCE_KEY = "paste-review-source"

APP_COPY_LABEL = "Last copied in RelayDeck; clipboard unavailable"

PREVIEW_KEY = "paste-review-preview"

PREVIEW_FRAME_KEY = "paste-review-preview-frame"

CONFIRM_KEY = "paste-review-confirm"

CANCEL_KEY = "paste-review-cancel"


def counts_line(review: PasteReview) -> str:
    return f"{review.line_count} lines \u00b7 {review.character_count} characters"


@ft.component
def PasteReviewSheet(
    review: PasteReview,
    on_confirm: Callable[[], Any],
    on_cancel: Callable[[], Any],
) -> Any:
    """Render untrusted preview text with counts and explicit actions.

    The mounting screen supplies the themed surface frame.
    """
    return ft.Column(
        key=SHEET_KEY,
        spacing=8,
        controls=[
            ft.Text(
                REVIEW_TITLE,
                key=TITLE_KEY,
                size=FONT_SIZE["body"],
                weight=ft.FontWeight.BOLD,
            ),
            ft.Text(counts_line(review), key=COUNTS_KEY, size=FONT_SIZE["small"]),
            ft.Text(
                APP_COPY_LABEL,
                key=SOURCE_KEY,
                visible=review.from_app,
                size=FONT_SIZE["small"],
            ),
            ft.Container(
                key=PREVIEW_FRAME_KEY,
                height=176,
                padding=12,
                content=ft.Column(
                    scroll=ft.ScrollMode.AUTO,
                    controls=[
                        ft.Text(
                            review.preview,
                            key=PREVIEW_KEY,
                            selectable=True,
                            font_family="monospace",
                        )
                    ],
                ),
            ),
            ft.Row(
                wrap=True,
                alignment=ft.MainAxisAlignment.END,
                controls=[
                    Button(
                        CANCEL_LABEL,
                        key=CANCEL_KEY,
                        height=MIN_TOUCH,
                        on_click=click(on_cancel),
                    ),
                    Button(
                        CONFIRM_LABEL,
                        key=CONFIRM_KEY,
                        height=MIN_TOUCH,
                        on_click=click(on_confirm),
                    ),
                ],
            ),
        ],
    )


__all__ = [
    "APP_COPY_LABEL",
    "CANCEL_KEY",
    "CANCEL_LABEL",
    "CONFIRM_KEY",
    "CONFIRM_LABEL",
    "COUNTS_KEY",
    "PREVIEW_FRAME_KEY",
    "PREVIEW_KEY",
    "SHEET_KEY",
    "SOURCE_KEY",
    "TITLE_KEY",
    "PasteReviewSheet",
    "counts_line",
]
