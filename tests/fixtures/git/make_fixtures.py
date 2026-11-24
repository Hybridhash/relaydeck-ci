"""Deterministic synthetic diffs and the pages the parser must produce for them.

Run `python tests/fixtures/git/make_fixtures.py` to rewrite the committed files. The expected
pages come from the row model below (explicit numbering, explicit marks, a plain 500-row page
split), never from the parser, so the golden files are an independent check. Everything is
synthetic text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE_ROWS = 500
FILE_ID = "file-1"
SNAPSHOT = "snapshot-1"
KINDS = {" ": "context", "-": "delete", "+": "add", "\\": "no_newline"}
MARKER = "\\ No newline at end of file"
# The committed key fixture carries the placeholder; readers put the header back.
PEM_HEADER = "-----BEGIN " + "PRIVATE KEY-----"
PEM_PLACEHOLDER = "<pem-private-header>"

Row = tuple[str, str, tuple[tuple[int, int], ...]]


@dataclass
class HunkSpec:
    rows: list[Row]
    old_start: int = 1
    new_start: int = 1
    section: str = ""

    def counts(self) -> tuple[int, int]:
        old = sum(1 for code, _, _ in self.rows if code in " -")
        new = sum(1 for code, _, _ in self.rows if code in " +")
        return old, new

    def range_header(self) -> str:
        old, new = self.counts()
        return f"@@ -{_range(self.old_start, old)} +{_range(self.new_start, new)} @@"

    def header(self) -> str:
        text = self.range_header()
        return f"{text} {self.section}" if self.section else text


@dataclass
class Scenario:
    head: list[str] = field(default_factory=list)
    hunks: list[HunkSpec] = field(default_factory=list)
    render: str = "ok"
    old_mode: str | None = None
    new_mode: str | None = None
    raw_tail: bytes = b""


def _range(start: int, count: int) -> str:
    return str(start) if count == 1 else f"{start},{count}"


def _context(text: str) -> Row:
    return (" ", text, ())


def _block(k: int, pos: int) -> Row:
    """Row `pos` of the 5-row block `k`: context, delete, add (a pair), context, add."""
    lead = f"    value_{k:05d} = "
    if pos == 0:
        return (" ", f"    keep_{k:05d} = {k}", ())
    if pos == 1:
        old = f"old_{k:05d}"
        return ("-", lead + old, ((len(lead), len(lead) + len(old)),))
    if pos == 2:
        new = f"new_{k:05d}"
        return ("+", lead + new, ((len(lead), len(lead) + len(new)),))
    if pos == 3:
        return (" ", f"    after_{k:05d}()", ())
    return ("+", f"    extra_{k:05d} = True", ())


def numbered_rows(count: int, lead: int = 0, first: int = 0) -> list[Row]:
    """`lead` plain context rows, then blocks until `count` rows exist."""
    rows = [_context(f"    lead_{i:05d}") for i in range(lead)]
    index = 0
    while len(rows) < count:
        rows.append(_block(first + index // 5, index % 5))
        index += 1
    if rows[-1][0] == "-":  # a delete cut off before its add has no pair to mark against
        rows[-1] = ("-", rows[-1][1], ())
    return rows


def _net(rows: list[Row]) -> int:
    return sum(1 for code, _, _ in rows if code == "+") - sum(
        1 for code, _, _ in rows if code == "-"
    )


def _head(path: str = "sample/file_one.txt") -> list[str]:
    return [
        f"diff --git a/{path} b/{path}",
        "index 1111111..2222222 100644",
        f"--- a/{path}",
        f"+++ b/{path}",
    ]


def rows_0050() -> Scenario:
    first, second = numbered_rows(20), numbered_rows(30, first=100)
    return Scenario(
        head=_head(),
        hunks=[
            HunkSpec(first),
            HunkSpec(second, old_start=101, new_start=101 + _net(first), section="def sample():"),
        ],
    )


def rows_1000() -> Scenario:
    return Scenario(head=_head(), hunks=[HunkSpec(numbered_rows(1000))])


def rows_20000() -> Scenario:
    return Scenario(head=_head(), hunks=[HunkSpec(numbered_rows(20000))])


def long_hunk() -> Scenario:
    # Three lead rows put the 500th row on a delete, so a delete/add pair straddles the page break.
    return Scenario(head=_head(), hunks=[HunkSpec(numbered_rows(1200, lead=3))])


def _wide(length: int) -> Scenario:
    text = ("0123456789abcdef" * (length // 16 + 1))[:length]
    rows = [_context("head_line"), ("+", text, ()), _context("tail_line")]
    return Scenario(head=_head(), hunks=[HunkSpec(rows)])


def line_16k() -> Scenario:
    return _wide(16 * 1024)


def line_16k_over() -> Scenario:
    scenario = _wide(16 * 1024 + 1)
    scenario.render = "too_large"
    return scenario


def unicode_rtl() -> Scenario:
    rows: list[Row] = [
        _context("# שלום עולם (RTL) مرحبا"),
        ("-", 'title = "naïve \U0001f600 שלום"', ((17, 21),)),
        ("+", 'title = "naïve \U0001f600 עולם"', ((17, 21),)),
        _context("é café العربية"),
        ("+", 'note = "a‮b c"', ()),
    ]
    return Scenario(head=_head(), hunks=[HunkSpec(rows)])


def no_newline() -> Scenario:
    rows: list[Row] = [
        _context("first line"),
        ("-", "last line", ()),
        ("\\", MARKER[1:], ()),
        ("+", "last line!", ((9, 10),)),
        ("\\", MARKER[1:], ()),
    ]
    return Scenario(head=_head(), hunks=[HunkSpec(rows)])


def rename_similarity() -> Scenario:
    old, new = "old_dir/sample_name.txt", "new_dir/sample_name.txt"
    head = [
        f"diff --git a/{old} b/{new}",
        "similarity index 90%",
        f"rename from {old}",
        f"rename to {new}",
        "index 1111111..2222222 100644",
        f"--- a/{old}",
        f"+++ b/{new}",
    ]
    rows: list[Row] = [
        _context("alpha"),
        ("-", "beta = 1", ((7, 8),)),
        ("+", "beta = 2", ((7, 8),)),
        _context("gamma"),
    ]
    return Scenario(head=head, hunks=[HunkSpec(rows)])


def mode_change() -> Scenario:
    head = ["diff --git a/tool.sh b/tool.sh", "old mode 100644", "new mode 100755"]
    return Scenario(head=head, old_mode="100644", new_mode="100755")


def binary() -> Scenario:
    head = [
        "diff --git a/blob.bin b/blob.bin",
        "index 1111111..2222222 100644",
        "Binary files a/blob.bin and b/blob.bin differ",
    ]
    return Scenario(head=head, render="binary")


def invalid_utf8() -> Scenario:
    return Scenario(
        head=_head(),
        render="invalid_encoding",
        raw_tail=b"@@ -1,2 +1,2 @@\n ok line\n-old\n+bad \xff\xfe bytes\n",
    )


def pem_key_pages() -> Scenario:
    """A synthetic key block that begins on row 496 and ends on row 528 of a new file."""
    rows: list[Row] = [("+", f"setting_{i:04d} = {i}", ()) for i in range(495)]
    rows.append(("+", PEM_HEADER, ()))
    rows += [("+", "synthetic not a key " + f"{i:04d}" * 8, ()) for i in range(31)]
    rows.append(("+", "-----END PRIVATE KEY-----", ()))
    rows += [("+", f"tail_{i:04d} = {i}", ()) for i in range(30)]
    head = [
        "diff --git a/sample/keys.txt b/sample/keys.txt",
        "new file mode 100644",
        "index 0000000..2222222",
        "--- /dev/null",
        "+++ b/sample/keys.txt",
    ]
    return Scenario(head=head, hunks=[HunkSpec(rows, old_start=0)], new_mode="100644")


SCENARIOS = {
    "rows_0050": rows_0050,
    "rows_1000": rows_1000,
    "rows_20000": rows_20000,
    "long_hunk": long_hunk,
    "line_16k": line_16k,
    "line_16k_over": line_16k_over,
    "unicode_rtl": unicode_rtl,
    "no_newline": no_newline,
    "rename_similarity": rename_similarity,
    "mode_change": mode_change,
    "binary": binary,
    "invalid_utf8": invalid_utf8,
    "pem_key_pages": pem_key_pages,
}
# Too big to commit; the tests build the diff and its pages from the row model.
NOT_COMMITTED = {"rows_20000"}


def diff_bytes(scenario: Scenario) -> bytes:
    out = [line + "\n" for line in scenario.head]
    for hunk in scenario.hunks:
        out.append(hunk.header() + "\n")
        out += [code + text + "\n" for code, text, _ in hunk.rows]
    return "".join(out).encode("utf-8") + scenario.raw_tail


def _hunk_lines(hunk: HunkSpec) -> list[dict[str, object]]:
    old, new = hunk.old_start, hunk.new_start
    lines: list[dict[str, object]] = []
    for code, text, marks in hunk.rows:
        line: dict[str, object] = {"kind": KINDS[code], "masked": False}
        if code == "\\":
            line["text"] = code + text
        else:
            line["text"] = text
            if code in " -":
                line["old"], old = old, old + 1
            if code in " +":
                line["new"], new = new, new + 1
        line["marks"] = [list(mark) for mark in marks]
        lines.append(line)
    return lines


def _fragment(hunk: HunkSpec, fragment: int, lines: list[dict[str, object]]) -> dict[str, object]:
    old, new = hunk.counts()
    return {
        "header": hunk.range_header(),
        "old_start": hunk.old_start,
        "old_lines": old,
        "new_start": hunk.new_start,
        "new_lines": new,
        "fragment": fragment,
        "lines": lines,
    }


def expected_pages(scenario: Scenario) -> list[dict[str, object]]:
    pages: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    rows = 0
    if scenario.render == "ok":
        for hunk in scenario.hunks:
            lines, fragment, pos = _hunk_lines(hunk), 0, 0
            if not lines:
                current.append(_fragment(hunk, 0, []))
            while pos < len(lines):
                if rows == PAGE_ROWS:
                    pages.append(current)
                    current, rows = [], 0
                    fragment += 1 if pos else 0
                take = lines[pos : pos + PAGE_ROWS - rows]
                current.append(_fragment(hunk, fragment, take))
                pos, rows = pos + len(take), rows + len(take)
    pages.append(current)
    base: dict[str, object] = {
        "file_id": FILE_ID,
        "snapshot": SNAPSHOT,
        "render": scenario.render,
        "truncated": False,
    }
    if scenario.old_mode:
        base["old_mode"] = scenario.old_mode
    if scenario.new_mode:
        base["new_mode"] = scenario.new_mode
    return [{**base, "hunks": hunks} for hunks in pages]


def render_json(pages: list[dict[str, object]]) -> str:
    body = ",\n".join(json.dumps(page, ensure_ascii=False, sort_keys=True) for page in pages)
    return f"[\n{body}\n]\n"


def main() -> None:
    for name, build in SCENARIOS.items():
        if name in NOT_COMMITTED:
            continue
        scenario = build()
        diff = diff_bytes(scenario).replace(PEM_HEADER.encode(), PEM_PLACEHOLDER.encode())
        pages = render_json(expected_pages(scenario)).replace(PEM_HEADER, PEM_PLACEHOLDER)
        (HERE / f"{name}.diff").write_bytes(diff)
        (HERE / f"{name}.expected.json").write_text(pages, encoding="utf-8")


if __name__ == "__main__":
    main()
