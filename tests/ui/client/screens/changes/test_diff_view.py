"""DiffFileView: flattened rows, a lazy fixed-extent list, word marks, states and sizes."""

from __future__ import annotations

from pathlib import Path

from relaydeck.client.screens.changes.parts.diff_view import (
    CHAR_WIDTH,
    ROW_HEIGHT,
    TEXT_SCALE_MARGIN,
    DiffFileView,
    DisplayRow,
    content_width,
    display_columns,
    display_rows,
    max_columns,
    state_text,
)
from relaydeck.contracts.git import FileDiff
from relaydeck.git.parser import DiffParser
from relaydeck.git.secret_policy import LineMasker
from tests.support.git_pem import read_fixture
from tests.support.host_harness import render_component, run
from tests.ui.git.harness import built_rows

FIXTURES = Path(__file__).resolve().parents[4] / "fixtures" / "git"


def pages(name: str) -> tuple[FileDiff, ...]:
    patch = read_fixture(FIXTURES / f"{name}.diff")
    return LineMasker.mask_pages(DiffParser().parse(patch, file_id="f", snapshot="s"))


def test_rows_flatten_pages_with_one_header_per_hunk() -> None:
    loaded = pages("long_hunk")
    rows = display_rows(loaded)
    assert len(loaded) >= 2
    assert sum(row.kind == "hunk" for row in rows) == sum(
        hunk.fragment == 0 for page in loaded for hunk in page.hunks
    )
    numbered = [row.new for row in rows if row.kind in ("add", "context")]
    assert numbered == sorted(
        numbered, key=lambda n: n or 0
    )  # numbering continues across fragments


def test_masked_rows_never_speak_the_secret() -> None:
    rows = display_rows(pages("pem_key_pages"))
    masked = [row for row in rows if row.masked]
    assert masked and all(set(row.text) == {"•"} for row in masked)


def test_state_text_for_non_ok_renders() -> None:
    assert state_text(None) is None
    for render in ("load_required", "too_large", "withheld", "binary", "invalid_encoding"):
        page = FileDiff(file_id="f", snapshot="s", render=render, hunks=())
        assert state_text(page)


def test_renders_a_lazy_fixed_extent_list_sized_to_the_longest_line() -> None:
    rows = display_rows(pages("rows_1000"))

    async def scenario() -> None:
        app = await render_component(DiffFileView, "a.txt", "+10 −2", rows)
        await app.settle()
        listing = app.find("diff-rows")
        assert listing.build_controls_on_demand and listing.data["rows"] == len(rows)
        assert 0 < len(built_rows(app, "diff-rows")) < 80  # the screen, not the file
        assert all(r.height == ROW_HEIGHT for r in built_rows(app, "diff-rows"))
        assert app.find("diff-scroll") is not None and max_columns(rows) > 0

    run(scenario())


def test_word_marks_become_highlighted_spans() -> None:
    rows = display_rows(pages("rows_0050"))
    index, row = next((i, r) for i, r in enumerate(rows) if r.marks)

    async def scenario() -> None:
        app = await render_component(DiffFileView, "a.txt", "", rows)
        await app.settle()
        text = app.find(f"diff-row-{index}").content.controls[3]
        highlighted = [span for span in text.spans if span.style is not None]
        assert len(highlighted) == len(row.marks)
        assert "".join(span.text for span in text.spans) == row.text

    run(scenario())


def test_load_message_offers_the_load_button() -> None:
    loads: list[bool] = []

    async def scenario() -> None:
        gated = await render_component(
            DiffFileView, "big.txt", "", (), message="Large", on_load=lambda: loads.append(True)
        )
        await gated.set_props_and_fire("diff-load", {}, "click")
        assert loads == [True]
        assert gated.find("diff-load").height >= 48

    run(scenario())


def test_rows_are_not_tappable_and_keep_their_extent_with_paging_buttons_outside_the_list() -> None:
    rows = display_rows(pages("rows_0050"))

    async def scenario() -> None:
        app = await render_component(
            DiffFileView,
            "a.txt",
            "",
            rows,
            has_more=True,
            on_more=lambda: None,
            has_earlier=True,
            on_earlier=lambda: None,
        )
        listing = app.find("diff-rows")
        assert listing.data["rows"] == len(rows)
        built = built_rows(app, "diff-rows")
        assert built and all(r.height == ROW_HEIGHT for r in built)
        assert all(control.on_click is None for control in built)
        assert app.find("diff-more").height >= 48 and app.find("diff-earlier").height >= 48

    run(scenario())


def test_rows_leave_room_for_enlarged_text() -> None:
    # Flet 1.0.3 gives a page no text scale, so the row is the font's line box plus a margin.
    assert ROW_HEIGHT >= 12.0 * 1.32 * TEXT_SCALE_MARGIN > 12.0 * 1.32 * 1.4


def test_widths_count_cells_not_code_points() -> None:
    assert display_columns("abc") == 3
    assert display_columns("\tx") == 9  # a tab is drawn up to eight cells wide
    assert display_columns("漢字") == 4  # wide glyphs take two
    assert display_columns("e\u0301") == 1  # a combining mark takes none
    plain = [DisplayRow("add", "x" * 10, new=1)]
    wide = [DisplayRow("add", "漢" * 10, new=1)]
    assert content_width(wide) - content_width(plain) >= 10 * CHAR_WIDTH * TEXT_SCALE_MARGIN


def test_the_gutter_grows_with_the_line_numbers() -> None:
    short = [DisplayRow("add", "x", new=9)]
    long = [DisplayRow("add", "x", new=123_456)]
    assert content_width(long) > content_width(short)


def test_show_more_appears_only_when_more_pages_exist() -> None:
    more: list[bool] = []
    rows = display_rows(pages("rows_0050"))

    async def scenario() -> None:
        app = await render_component(
            DiffFileView, "a.txt", "", rows, has_more=True, on_more=lambda: more.append(True)
        )
        await app.set_props_and_fire("diff-more", {}, "click")
        assert more == [True]

    run(scenario())
