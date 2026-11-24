"""Picture alternatives identify positions without exposing private picture data."""

import asyncio

import flet as ft
import pytest

from relaydeck.client.features.terminal.pictures import picture_chips
from relaydeck.client.models.pictures import Picture


@pytest.mark.parametrize("previews", [(True, False), (False, True), (True, True), (False, False)])
def test_thumbnail_labels_follow_current_positions_and_keep_removal(
    previews: tuple[bool, bool],
) -> None:
    pictures = tuple(
        Picture(
            str(index) * 32,
            f"/private/medical/patient-{index}.png",
            9000,
            f"private-preview-{index}" if preview else "",
        )
        for index, preview in enumerate(previews, start=1)
    )
    removed: list[str] = []

    async def scenario() -> None:
        row = picture_chips(pictures, removed.append)
        assert row.key == "composer-picture-chips" and row.scroll == ft.ScrollMode.AUTO
        assert [chip.key for chip in row.controls] == [
            f"picture-chip-{p.picture_id}" for p in pictures
        ]
        for index, (chip, picture) in enumerate(zip(row.controls, pictures, strict=True), start=1):
            thumbnail, button = chip.controls
            assert thumbnail.semantics_label == f"Attached picture {index}"
            if picture.preview:
                assert isinstance(thumbnail, ft.Image) and thumbnail.src == picture.preview
                assert (
                    thumbnail.width == thumbnail.height == 48 and thumbnail.fit == ft.BoxFit.COVER
                )
            else:
                assert isinstance(thumbnail, ft.Icon) and thumbnail.icon == ft.Icons.IMAGE_OUTLINED
                assert thumbnail.size == 48
            assert button.key == f"picture-remove-{picture.picture_id}"
            assert button.width == button.height == 48 and button.tooltip == "Remove picture"
        assert removed == []
        await row.controls[1].controls[1].on_click(None)
        await row.controls[0].controls[1].on_click(None)
        assert removed == [pictures[1].picture_id, pictures[0].picture_id]
        remaining = picture_chips(pictures[1:], removed.append)
        assert remaining.controls[0].controls[0].semantics_label == "Attached picture 1"
        assert picture_chips((), removed.append).controls == []
        assert removed == [pictures[1].picture_id, pictures[0].picture_id]

    asyncio.run(scenario())
