"""The typed Tokens value: tiered theme.toml, frozen records, and parity of resolved values."""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Mapping
from typing import Any

import pytest

from relaydeck.client.design_system import theme
from relaydeck.client.design_system import tokens as legacy

TOKENS = theme.TOKENS


def test_theme_toml_has_exactly_the_three_tiers() -> None:
    doc = theme._BASE_DOC
    assert set(doc) == {"meta", "primitive", "semantic", "component", "themes"}
    assert set(doc["primitive"]) == {"scales", "font_size"}
    assert set(doc["semantic"]) == {
        "palette",
        "status",
        "agent",
        "console",
        "type",
        "a11y",
        "overlay",
    }
    assert set(doc["semantic"]["overlay"]) == {"scrim", "context_text_muted"}
    assert theme.SCRIM_COLOR == doc["semantic"]["overlay"]["scrim"]
    assert theme.CONTEXT_TEXT_MUTED == doc["semantic"]["overlay"]["context_text_muted"]
    assert set(doc["component"]) == {"usage"}
    assert set(doc["component"]["usage"]) == {"dark", "light"}


def test_token_names_expose_the_typed_value() -> None:
    assert legacy.TOKENS is theme.TOKENS
    assert legacy.Tokens is theme.Tokens


def test_tokens_and_every_nested_record_are_frozen() -> None:
    records = [
        TOKENS,
        TOKENS.palette_dark,
        TOKENS.palette_light,
        TOKENS.console,
        TOKENS.console_scales,
        TOKENS.usage_dark,
        TOKENS.usage_light,
        TOKENS.agent,
        TOKENS.type_styles["type_title"],
    ]
    for record in records:
        assert not isinstance(record, Mapping)
        assert not hasattr(record, "__getitem__")
        field = dataclasses.fields(record)[0].name
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, field, None)


def test_mappings_inside_tokens_cannot_be_changed() -> None:
    for mapping in (
        TOKENS.font_size,
        TOKENS.status_colors,
        TOKENS.banner_tones,
        TOKENS.type_styles,
    ):
        with pytest.raises(TypeError):
            mapping["x"] = 1  # type: ignore[index]


def test_module_level_names_reference_immutable_records() -> None:
    assert theme.CONSOLE is TOKENS.console
    assert theme.CONSOLE_SCALES is TOKENS.console_scales
    assert theme.USAGE_DARK is TOKENS.usage_dark
    assert theme.USAGE_LIGHT is TOKENS.usage_light
    assert theme.DARK is TOKENS.palette_dark
    assert theme.LIGHT is TOKENS.palette_light
    assert theme.palette(True) is TOKENS.palette_dark
    assert theme.palette(False) is TOKENS.palette_light
    assert theme.SPACING == TOKENS.spacing
    assert theme.RADIUS == TOKENS.radius
    assert theme.MIN_TOUCH == TOKENS.min_touch
    assert theme.FONT_SIZE is TOKENS.font_size
    assert theme.STATUS_COLORS is TOKENS.status_colors
    assert theme.BANNER_TONES is TOKENS.banner_tones
    assert theme.ACCENT == TOKENS.palette_light.accent
    assert theme.TYPE_SCALES is TOKENS.type_styles


def test_legacy_console_constants_equal_the_typed_fields() -> None:
    checked = 0
    for field in dataclasses.fields(TOKENS.console):
        constant = field.name.upper()
        if hasattr(legacy, constant):
            assert getattr(legacy, constant) == getattr(TOKENS.console, field.name), constant
            checked += 1
    assert checked >= 50


def test_legacy_scale_and_type_constants_equal_the_typed_fields() -> None:
    scales = TOKENS.console_scales
    assert legacy.RADIUS_CARD == scales.radius_card
    assert legacy.PAD_PHONE == scales.pad_phone
    assert legacy.GAP_SECTION == scales.gap_section
    assert legacy.SELECT_OUTLINE_WIDTH == scales.select_outline_width
    for name, style in TOKENS.type_styles.items():
        assert getattr(legacy, name.upper()) is style, name
    assert legacy.USAGE_DARK is TOKENS.usage_dark
    assert legacy.usage_palette(False) is TOKENS.usage_light


def _doc() -> dict[str, Any]:
    return copy.deepcopy(theme._BASE_DOC)


def test_building_again_from_the_resolved_document_gives_equal_tokens() -> None:
    assert theme.build_tokens(theme._DOC) == TOKENS


@pytest.mark.parametrize(
    ("mutate", "where"),
    [
        (lambda d: d["semantic"]["palette"]["dark"].pop("bg"), "semantic.palette.dark"),
        (lambda d: d["semantic"]["console"].update(extra="#000000"), "semantic.console"),
        (lambda d: d["component"]["usage"]["light"].update(bg="red"), "component.usage.light"),
        (lambda d: d["primitive"]["scales"]["console"].update(radius_card=1.5), "scales.console"),
        (lambda d: d["primitive"]["scales"].update(min_touch=48.0), "primitive.scales"),
        (lambda d: d["primitive"]["font_size"].update(body=14.5), "primitive.font_size"),
        (lambda d: d["semantic"]["type"]["type_title"].pop("height"), "semantic.type.type_title"),
        (lambda d: d["semantic"]["type"]["type_title"].update(weight=800.0), "type_title"),
        (lambda d: d["semantic"]["agent"].update(space_tints=[]), "semantic.agent"),
        (
            lambda d: d["semantic"]["agent"].update(triples=[["#000000", "#111111"]]),
            "semantic.agent",
        ),
        (lambda d: d["semantic"]["a11y"].update(surface_dark_ref="dark"), "semantic.a11y"),
    ],
)
def test_the_loader_rejects_a_malformed_document(mutate: Any, where: str) -> None:
    doc = _doc()
    mutate(doc)
    with pytest.raises(ValueError, match=where):
        theme.build_tokens(doc)


def test_a_missing_tier_is_refused() -> None:
    doc = _doc()
    del doc["component"]
    with pytest.raises(ValueError, match="component"):
        theme.build_tokens(doc)
