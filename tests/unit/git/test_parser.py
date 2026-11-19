"""DiffParser: golden fixtures, page limits, fragments, render states and word marks."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from relaydeck.contracts.cursors import MAX_CURSOR_LEN
from relaydeck.contracts.git import FileDiff
from relaydeck.git import parser as parser_module
from relaydeck.git.parser import (
    MARK_MAX_CHARS,
    MAX_LINE_BYTES,
    MAX_PAGE_BYTES,
    MAX_PAGE_ROWS,
    DiffParser,
    page_size,
)
from tests.support.git_pem import (
    PEM_PLACEHOLDER,
    PEM_PRIVATE_HEADER,
    read_fixture,
    read_fixture_text,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "git"
GOLDEN = sorted(
    path.name.removesuffix(".expected.json") for path in FIXTURES.glob("*.expected.json")
)


def _maker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "git_fixture_maker", FIXTURES / "make_fixtures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse(patch: bytes, parser: DiffParser | None = None) -> tuple[FileDiff, ...]:
    return (parser or DiffParser()).parse(patch, file_id="file-1", snapshot="snapshot-1")


def rows_of(pages: tuple[FileDiff, ...]) -> list[Any]:
    return [line for page in pages for hunk in page.hunks for line in hunk.lines]


def new_file_patch(count: int, *, start: int = 100, text: str = "row_{i:05d}") -> bytes:
    """An added file of `count` rows whose numbers all have the same width."""
    body = "".join("+" + text.format(i=i) + "\n" for i in range(count))
    return f"@@ -0,0 +{start},{count} @@\n{body}".encode()


def with_cursor(page: FileDiff) -> FileDiff:
    return replace(page, next="x" * MAX_CURSOR_LEN)


@pytest.mark.parametrize("name", GOLDEN)
def test_fixture_matches_its_golden_pages(name: str) -> None:
    pages = parse(read_fixture(FIXTURES / f"{name}.diff"))
    expected = json.loads(read_fixture_text(FIXTURES / f"{name}.expected.json"))
    assert [page.to_dict() for page in pages] == expected


def test_the_key_fixture_stores_the_placeholder_the_readers_replace() -> None:
    maker = _maker()
    assert (maker.PEM_HEADER, maker.PEM_PLACEHOLDER) == (PEM_PRIVATE_HEADER, PEM_PLACEHOLDER)
    for suffix in (".diff", ".expected.json"):
        stored = (FIXTURES / f"pem_key_pages{suffix}").read_text(encoding="utf-8")
        assert PEM_PLACEHOLDER in stored and PEM_PRIVATE_HEADER not in stored


def test_twenty_thousand_rows_match_pages_built_from_the_row_model() -> None:
    maker = _maker()
    scenario = maker.rows_20000()
    pages = parse(maker.diff_bytes(scenario))
    assert [page.to_dict() for page in pages] == maker.expected_pages(scenario)
    assert len(pages) == 40 and {len(rows_of((page,))) for page in pages} == {MAX_PAGE_ROWS}
    assert [hunk.fragment for page in pages for hunk in page.hunks] == list(range(40))


def test_one_row_over_twenty_thousand_is_too_large() -> None:
    at_limit = parse(new_file_patch(20_000))
    over = parse(new_file_patch(20_001))
    assert at_limit[0].render == "ok" and sum(len(rows_of((p,))) for p in at_limit) == 20_000
    assert [(p.render, p.hunks) for p in over] == [("too_large", ())]


def test_a_long_hunk_continues_as_numbered_fragments_with_the_same_header() -> None:
    pages = parse((FIXTURES / "long_hunk.diff").read_bytes())
    fragments = [hunk for page in pages for hunk in page.hunks]
    assert [h.fragment for h in fragments] == [0, 1, 2]
    assert (
        len({(h.header, h.old_start, h.old_lines, h.new_start, h.new_lines) for h in fragments})
        == 1
    )
    assert [len(h.lines) for h in fragments] == [500, 500, 200]
    rows = rows_of(pages)
    olds = [r.old for r in rows if r.old is not None]
    news = [r.new for r in rows if r.new is not None]
    assert olds == list(range(1, len(olds) + 1)) and news == list(range(1, len(news) + 1))


def test_a_pair_split_by_a_page_break_still_gets_its_marks() -> None:
    pages = parse((FIXTURES / "long_hunk.diff").read_bytes())
    last, first = pages[0].hunks[0].lines[-1], pages[1].hunks[0].lines[0]
    assert (last.kind, first.kind) == ("delete", "add") and last.marks and first.marks


def test_a_hunk_that_ends_on_a_page_boundary_does_not_leave_an_empty_fragment() -> None:
    patch = new_file_patch(500) + b"@@ -900 +900 @@\n-old\n+new\n"
    pages = parse(patch)
    assert [len(rows_of((p,))) for p in pages] == [500, 2]
    assert [h.fragment for p in pages for h in p.hunks] == [0, 0]


@pytest.mark.parametrize(("count", "pages"), [(499, 1), (500, 1), (501, 2), (1000, 2), (1001, 3)])
def test_pages_hold_at_most_five_hundred_rows(count: int, pages: int) -> None:
    result = parse(new_file_patch(count))
    assert len(result) == pages
    assert all(1 <= len(rows_of((page,))) <= MAX_PAGE_ROWS for page in result)
    assert sum(len(rows_of((page,))) for page in result) == count


def _bytes_to_hold(rows: int) -> int:
    """The byte limit at which exactly `rows` of the 80 sample rows fit a page."""
    page = parse(new_file_patch(80), DiffParser(max_rows=rows))[0]
    assert len(rows_of((page,))) == rows
    return page_size(with_cursor(page))


@pytest.mark.parametrize("fit", [3, 10, 37])
def test_pages_stop_at_the_byte_limit_and_just_above_it_takes_one_more_row(fit: int) -> None:
    patch = new_file_patch(80)
    at = parse(patch, DiffParser(max_bytes=_bytes_to_hold(fit)))
    assert len(rows_of((at[0],))) == fit
    below = parse(patch, DiffParser(max_bytes=_bytes_to_hold(fit) - 1))
    assert len(rows_of((below[0],))) == fit - 1
    for page in at:
        assert page_size(with_cursor(page)) <= _bytes_to_hold(fit)
        assert not page.truncated


def test_every_golden_page_fits_the_wire_limits_with_a_full_length_cursor() -> None:
    for name in GOLDEN:
        for page in parse((FIXTURES / f"{name}.diff").read_bytes()):
            assert page_size(with_cursor(page)) <= MAX_PAGE_BYTES
            assert len(rows_of((page,))) <= MAX_PAGE_ROWS


def test_json_escaping_is_counted_so_control_characters_page_early() -> None:
    pages = parse(new_file_patch(200, text="\x01" * 4000))
    assert all(page_size(with_cursor(page)) <= MAX_PAGE_BYTES for page in pages)
    # Each character costs six bytes once escaped: ten rows fill a page, eleven would not fit.
    assert {len(rows_of((page,))) for page in pages[:-1]} == {10}


def test_a_row_too_big_for_any_page_goes_alone_and_marks_the_page_truncated() -> None:
    pages = parse(new_file_patch(5), DiffParser(max_bytes=700))
    assert [len(rows_of((page,))) for page in pages] == [1] * 5
    assert all(page.truncated for page in pages)
    assert [r.text for r in rows_of(pages)] == [f"row_{i:05d}" for i in range(5)]


def test_a_patch_without_hunks_is_still_one_page() -> None:
    for patch in (b"", b"diff --git a/x b/x\nindex 1..2 100644\n"):
        (page,) = parse(patch)
        assert (page.render, page.hunks, page.next, page.truncated) == ("ok", (), None, False)


def test_a_line_of_exactly_sixteen_kib_renders_and_one_more_byte_does_not() -> None:
    def wide(size: int) -> bytes:
        return f"@@ -0,0 +1 @@\n+{'a' * size}\n".encode()

    assert parse(wide(MAX_LINE_BYTES))[0].render == "ok"
    assert [p.render for p in parse(wide(MAX_LINE_BYTES + 1))] == ["too_large"]


def test_the_line_limit_counts_bytes_for_context_and_deleted_lines_too() -> None:
    big = "\u00e9" * (MAX_LINE_BYTES // 2 + 1)  # two bytes each
    assert parse(f"@@ -1 +1 @@\n {big}\n".encode())[0].render == "too_large"
    assert parse(f"@@ -1 +0,0 @@\n-{big}\n".encode())[0].render == "too_large"
    short = "\u00e9" * (MAX_LINE_BYTES // 2)
    assert parse(f"@@ -0,0 +1 @@\n+{short}\n".encode())[0].render == "ok"


@pytest.mark.parametrize(
    "patch",
    [
        b"@@ -0,0 +1 @@\n+bad \xff bytes\n",
        b"@@ -0,0 +1 @@\n+truncated \xe2\x82\n",
        b"@@ -1 +0,0 @@\n-\xc0\xaf\n",
    ],
    ids=["byte", "cut_sequence", "overlong"],
)
def test_invalid_utf8_is_never_decoded_with_replacement(patch: bytes) -> None:
    (page,) = parse(patch)
    assert (page.render, page.hunks) == ("invalid_encoding", ())
    assert "\ufffd" not in json.dumps(page.to_dict())


def test_invalid_bytes_in_a_header_path_do_not_matter() -> None:
    patch = b"diff --git a/d\xff/f b/d\xff/f\n--- a/d\xff/f\n+++ b/d\xff/f\n@@ -1 +1 @@\n-a\n+b\n"
    (page,) = parse(patch)
    assert page.render == "ok" and [r.text for r in rows_of((page,))] == ["a", "b"]


def test_binary_patches_have_no_content() -> None:
    for marker in (
        b"Binary files a/x.bin and b/x.bin differ\n",
        b"GIT binary patch\nliteral 3\nabc\n",
    ):
        (page,) = parse(b"diff --git a/x.bin b/x.bin\nindex 1..2 100644\n" + marker)
        assert (page.render, page.hunks) == ("binary", ())


def test_modes_are_read_from_mode_headers_only() -> None:
    cases = {
        b"old mode 100644\nnew mode 100755\n": ("100644", "100755"),
        b"new file mode 100644\n": (None, "100644"),
        b"deleted file mode 100755\n": ("100755", None),
        b"index 1111111..2222222 100644\n": (None, None),
    }
    for header, modes in cases.items():
        (page,) = parse(b"diff --git a/x b/x\n" + header)
        assert (page.old_mode, page.new_mode) == modes


def test_header_paths_never_reach_the_result() -> None:
    pages = parse((FIXTURES / "rename_similarity.diff").read_bytes())
    dump = json.dumps([page.to_dict() for page in pages])
    assert "old_dir" not in dump and "new_dir" not in dump and "sample_name" not in dump


def test_omitted_counts_mean_one_line() -> None:
    (page,) = parse(b"@@ -4 +7 @@ name\n-a\n+b\n")
    hunk = page.hunks[0]
    assert (hunk.old_start, hunk.old_lines, hunk.new_start, hunk.new_lines) == (4, 1, 7, 1)
    assert hunk.header == "@@ -4 +7 @@"
    assert [(r.old, r.new) for r in hunk.lines] == [(4, None), (None, 7)]


def test_a_deleted_line_that_looks_like_a_file_header_stays_content() -> None:
    (page,) = parse(b"@@ -1,2 +1 @@\n--- not a header\n keep\n")
    assert [(r.kind, r.text) for r in rows_of((page,))] == [
        ("delete", "-- not a header"),
        ("context", "keep"),
    ]


def test_carriage_returns_and_odd_separators_stay_in_the_text() -> None:
    (page,) = parse("@@ -0,0 +1,2 @@\n+a\r\n+b c\n".encode())
    assert [r.text for r in rows_of((page,))] == ["a\r", "b c"]


@pytest.mark.parametrize(
    "patch",
    [
        b"@@ -1,3 +1,3 @@\n a\n-b\n",
        b"@@ -1,1 +1,1 @@\n a\n b\n",
        b"@@ -1 +1 @@\n-a\n+b\n-- \n2.0\n",
        b"@@@ -1,2 -1,2 +1,3 @@@\n a\n",
        b"@@ nonsense @@\n a\n",
        b"@@ -1 +1 @@\n?bad\n",
        b"@@ -1,1 +1,0 @@\n+extra\n",
        b"diff --git a/x b/x\n@@ -1 +1 @@\n-a\n+b\ndiff --git a/y b/y\n@@ -1 +1 @@\n-a\n+b\n",
    ],
    ids=[
        "short",
        "long",
        "trailing",
        "combined",
        "bad_header",
        "bad_prefix",
        "overflow",
        "two_files",
    ],
)
def test_malformed_patches_are_unsupported(patch: bytes) -> None:
    (page,) = parse(patch)
    assert (page.render, page.hunks) == ("unsupported", ())


def test_an_empty_line_inside_a_hunk_is_an_empty_context_line() -> None:
    (page,) = parse(b"@@ -1,2 +1,2 @@\n\n-a\n+b\n")
    assert [(r.kind, r.text) for r in rows_of((page,))] == [
        ("context", ""),
        ("delete", "a"),
        ("add", "b"),
    ]


def marks(old: str, new: str) -> tuple[Any, Any]:
    (page,) = parse(f"@@ -1 +1 @@\n-{old}\n+{new}\n".encode())
    first, second = rows_of((page,))
    return first.marks, second.marks


def test_word_marks_cover_the_changed_words_in_code_points() -> None:
    assert marks("let total = price", "let total = cost") == (((12, 17),), ((12, 16),))
    assert marks("a b c", "a x c") == (((2, 3),), ((2, 3),))
    assert marks("one two", "one two three") == ((), ((7, 13),))
    assert marks("\U0001f600 old", "\U0001f600 new") == (((2, 5),), ((2, 5),))


def test_separate_changes_in_one_line_give_separate_marks() -> None:
    assert marks("aa bb cc", "xx bb yy") == (((0, 2), (6, 8)), ((0, 2), (6, 8)))


def test_a_mark_over_the_whole_line_is_dropped_and_identical_text_has_none() -> None:
    assert marks("alpha", "omega") == ((), ())
    assert marks("same", "same") == ((), ())


def test_lines_over_two_hundred_code_points_get_no_marks() -> None:
    body = "w " * ((MARK_MAX_CHARS - 4) // 2)
    exact = body + "old1"
    assert len(exact) == MARK_MAX_CHARS
    assert marks(exact, body + "new1")[0] == ((MARK_MAX_CHARS - 4, MARK_MAX_CHARS),)
    assert marks(exact + "x", body + "new1x") == ((), ())


def test_deletes_and_adds_are_paired_in_order_and_extras_stay_unmarked() -> None:
    patch = b"@@ -1,3 +1,2 @@\n-a1 x\n-a2 x\n-a3 x\n+b1 x\n+b2 x\n"
    rows = rows_of(parse(patch))
    assert [bool(r.marks) for r in rows] == [True, True, False, True, True]
    patch = b"@@ -1,2 +1,2 @@\n ctx\n-q one\n+q two\n"
    assert [bool(r.marks) for r in rows_of(parse(patch))] == [False, True, True]


def test_a_context_line_ends_a_run_and_a_new_delete_starts_the_next() -> None:
    patch = b"@@ -1,3 +1,3 @@\n-a b\n+a c\n ctx\n-d e\n+d f\n"
    assert [bool(r.marks) for r in rows_of(parse(patch))] == [True, True, False, True, True]


def test_work_is_bounded_when_every_pair_costs_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    class Counting(parser_module.difflib.SequenceMatcher):  # type: ignore[type-arg]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            calls.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(parser_module.difflib, "SequenceMatcher", Counting)
    line = "ab " * 66 + "a"  # 133 alternating tokens: the worst case for the matcher
    other = "ab " * 66 + "b"
    body = "".join(f"-{line}\n+{other}\n" for _ in range(400))
    pages = parse(f"@@ -1,400 +1,400 @@\n{body}".encode())
    cost = 133 * 133
    assert 0 < len(calls) <= parser_module.MARK_WORK_BUDGET // cost
    marked = [r for r in rows_of(pages) if r.marks]
    assert len(marked) == 2 * len(calls)


def test_the_work_budget_is_per_file_and_leaves_later_pairs_unmarked() -> None:
    body = "-aa 1\n+aa 2\n" * 10
    parser = DiffParser(mark_work=3 * 3 * 3)  # three pairs of three tokens each
    rows = rows_of(parse(f"@@ -1,10 +1,10 @@\n{body}".encode(), parser))
    assert [bool(r.marks) for r in rows] == [True] * 6 + [False] * 14


def test_marks_are_always_valid_ranges() -> None:
    for name in GOLDEN:
        for line in rows_of(parse((FIXTURES / f"{name}.diff").read_bytes())):
            end = 0
            for start, stop in line.marks:
                assert end <= start < stop <= len(line.text)
                end = stop


def test_hunk_header_keeps_only_the_range() -> None:
    (page,) = parse(b"@@ -1 +1 @@ def leak(key='sk-abcdefghijklmnopqrstuvwxyz') \xff\n-a\n+b\n")
    assert page.render == "ok" and page.hunks[0].header == "@@ -1 +1 @@"


def test_masked_pages_still_fit_the_byte_limit() -> None:
    from relaydeck.git.parser import MAX_PAGE_BYTES, DiffParser, page_size
    from relaydeck.git.secret_policy import LineMasker

    token = "sk-" + "a" * 300
    body = "".join(f"+{token}\n" for _ in range(800))
    patch = f"@@ -0,0 +1,800 @@\n{body}".encode()
    pages = DiffParser().parse(patch, file_id="f", snapshot="s", masker=LineMasker())
    assert len(pages) > 1
    assert all(page_size(page) <= MAX_PAGE_BYTES for page in pages)
    assert all(line.masked for page in pages for hunk in page.hunks for line in hunk.lines)
