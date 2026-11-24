"""SecretPolicy withholds secret paths; LineMasker hides key blocks and known token shapes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from relaydeck.contracts.git import DiffLine, FileDiff, LineKind
from relaydeck.git.parser import DiffParser
from relaydeck.git.secret_policy import BULLET, LineMasker, SecretPolicy, UnsafeKeyContext
from tests.support.git_pem import (
    PEM_PRIVATE_HEADER,
    PEM_PRIVATE_HEADER_BYTES,
    PEM_RSA_PRIVATE_HEADER_BYTES,
    read_fixture,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "git"


@pytest.mark.parametrize(
    "side",
    [
        PEM_PRIVATE_HEADER_BYTES + b"\nFAKE-NOT-A-KEY\n",
        PEM_RSA_PRIVATE_HEADER_BYTES + b"\nFAKE-NOT-A-KEY\n-----END PRIVATE KEY-----\n",
    ],
)
def test_incomplete_full_side_is_not_a_safe_masking_context(side: bytes) -> None:
    with pytest.raises(UnsafeKeyContext):
        LineMasker.from_sides(side, b"")


def test_undecodable_bytes_elsewhere_in_a_side_still_allow_masking() -> None:
    side = (
        b"caf\xe9\n"
        + PEM_PRIVATE_HEADER_BYTES
        + b"\nFAKE-NOT-A-KEY\n-----END PRIVATE KEY-----\nplain\n"
    )
    patch = b"@@ -3,3 +3,3 @@\n FAKE-NOT-A-KEY\n -----END PRIVATE KEY-----\n-plain\n+plainer\n"
    masker = LineMasker.from_sides(side, side.replace(b"plain", b"plainer"))
    (page,) = DiffParser().parse(patch, file_id="file", snapshot="snapshot", masker=masker)
    assert page.render == "ok"
    texts = [line.text for line in page.hunks[0].lines]
    assert texts[2:] == ["plain", "plainer"]
    assert all(set(text) == {BULLET} for text in texts[:2])


def test_patch_rows_that_disagree_with_complete_sides_are_withheld() -> None:
    patch = b"@@ -2 +2 @@\n-unmatched old\n+unmatched new\n"
    masker = LineMasker.from_sides(b"public\nactual old\n", b"public\nactual new\n")
    (page,) = DiffParser().parse(patch, file_id="file", snapshot="snapshot", masker=masker)
    assert page.render == "withheld" and not page.hunks


# Assembled from parts so no real credential shape appears in the source.
TOKENS = {
    "classic": "gh" + "p_" + "a1B2" * 9,
    "fine_grained": "github" + "_pat_" + "x1Y2z3" * 5,
    "api_key": "s" + "k-" + "proj-" + "a1B2" * 6,
    "chat": "xo" + "xb-" + "1234567890-abcdefghij",
    "cloud": "AK" + "IA" + "ABCD1234EFGH5678",
    "cloud_session": "AS" + "IA" + "ABCD1234EFGH5678",
    "project_token": "glp" + "at-" + "a1B2c3D4e5F6g7H8i9J0",
    "maps": "AI" + "za" + "Ab1_" * 8 + "Ab1",
    "package": "np" + "m_" + "a1B2" * 9,
}


@pytest.mark.parametrize(
    "path",
    [
        b".env",
        b".env.local",
        b".env.production",
        b"app/.env",
        b"server.pem",
        b"deploy/private.key",
        b"store.p12",
        b"id_rsa",
        b"home/.ssh/id_ed25519.pub",
        b"secrets/token.txt",
        b"deep/secrets/inner/file.txt",
        b"credentials.json",
        b"credentials",
        b"config/credentials.yml",
        b"certs.pem/readme.txt",
        b"ENV/.ENV",
        b"keys/SERVER.PEM",
        b"dir/\xff/.env",
        b"./secrets/x",
        b"a//b//.env",
    ],
)
def test_secret_paths_are_withheld(path: bytes) -> None:
    assert SecretPolicy().withheld(path)


@pytest.mark.parametrize(
    "path",
    [
        b"src/environment.py",
        b"docs/env.md",
        b"app/keyboard.py",
        b"store.p12.md",
        b"src/secrets.py",
        b"src/identity.py",
        b"notes/pem.txt",
        b"credit/score.py",
        b"README.md",
        b"dir/\xff/ok.txt",
        b"",
    ],
)
def test_ordinary_paths_are_not_withheld(path: bytes) -> None:
    assert not SecretPolicy().withheld(path)


def test_configured_paths_cover_their_subtree_only() -> None:
    policy = SecretPolicy([b"private/notes", b"./Vault/", b""])
    assert policy.withheld(b"private/notes")
    assert policy.withheld(b"private/notes/a/b.txt")
    assert policy.withheld(b"vault/x")
    assert not policy.withheld(b"private/notes2/a.txt")
    assert not policy.withheld(b"private")
    assert not policy.withheld(b"other/private/notes")
    assert not SecretPolicy([b""]).withheld(b"anything")


def test_a_rename_is_withheld_when_either_side_is_secret() -> None:
    policy = SecretPolicy()
    assert policy.withheld_any(b"docs/new.txt", b"secrets/old.txt")
    assert policy.withheld_any(b"config/prod.pem", b"docs/old.txt")
    assert not policy.withheld_any(b"docs/new.txt", b"docs/old.txt")
    assert not policy.withheld_any(b"docs/new.txt", None)
    assert not policy.withheld_any()


def add(text: str, marks: tuple[tuple[int, int], ...] = ()) -> DiffLine:
    return DiffLine(kind="add", new=1, text=text, marks=marks)


@pytest.mark.parametrize("token", TOKENS.values(), ids=TOKENS.keys())
def test_known_token_shapes_are_replaced_by_bullets_of_the_same_length(token: str) -> None:
    line = add(f'api = "{token}" # keep')
    masked = LineMasker().mask_line(line)
    assert masked.masked
    assert masked.text == f'api = "{BULLET * len(token)}" # keep'
    assert len(masked.text) == len(line.text)


def test_several_tokens_on_one_line_are_all_masked() -> None:
    first, second = TOKENS["classic"], TOKENS["cloud"]
    masked = LineMasker().mask_line(add(f"{first},{second}"))
    assert masked.text == BULLET * len(first) + "," + BULLET * len(second)


def test_masking_works_on_every_side_of_a_change() -> None:
    token = TOKENS["classic"]
    kinds: tuple[LineKind, ...] = ("context", "delete", "add")
    for kind in kinds:
        masked = LineMasker().mask_line(DiffLine(kind=kind, text=f"t={token}"))
        assert masked.text == "t=" + BULLET * len(token) and masked.masked


@pytest.mark.parametrize(
    "text",
    [
        "task-list-item-with-a-long-descriptive-name",
        "risk-assessment-document-final-version-2",
        "sk-short",
        "ghp_tooshort",
        "AKIA1234",
        "AKIA" + "ABCD1234EFGH5678" + "9",
        "xoxb-1",
        "github_pat_short",
        "x = ghpx_" + "a" * 36,
        "def sk_learn(): pass",
        "-----BEGIN PUBLIC KEY-----",
        "-----BEGIN CERTIFICATE-----",
        "plain text with unicode שלום and \U0001f600",
        "",
    ],
)
def test_text_that_is_not_a_secret_is_returned_untouched(text: str) -> None:
    line = add(text)
    assert LineMasker().mask_line(line) is line


def test_marks_are_dropped_from_masked_lines_and_kept_elsewhere() -> None:
    token = TOKENS["classic"]
    masker = LineMasker()
    secret = masker.mask_line(add(f"k = {token}", ((4, 10),)))
    assert secret.marks == () and secret.masked
    plain = add("k = 1", ((4, 5),))
    assert masker.mask_line(plain) is plain


def test_notices_are_never_masked() -> None:
    notice = DiffLine(kind="no_newline", text="\\ No newline at end of file")
    masker = LineMasker()
    masker.mask_line(add(PEM_PRIVATE_HEADER))
    assert masker.mask_line(notice) is notice


@pytest.mark.parametrize(
    "label",
    [
        "PRIVATE KEY",
        "RSA PRIVATE KEY",
        "OPENSSH PRIVATE KEY",
        "EC PRIVATE KEY",
        "PGP PRIVATE KEY BLOCK",
    ],
)
def test_a_key_block_is_masked_from_begin_to_end_inclusive(label: str) -> None:
    lines = [
        add("before"),
        add(f"-----BEGIN {label}-----"),
        add("SYNTHETICBODYSYNTHETICBODY"),
        add(""),
        add(f"-----END {label}-----"),
        add("after"),
    ]
    masker = LineMasker()
    out = [masker.mask_line(line) for line in lines]
    assert [line.masked for line in out] == [False, True, True, True, True, False]
    assert [line.text for line in out[1:5]] == [BULLET * len(line.text) for line in lines[1:5]]
    assert out[0] is lines[0] and out[5] is lines[5]


def test_a_block_on_one_line_masks_that_line_and_leaves_nothing_open() -> None:
    masker = LineMasker()
    one = add(f"x {PEM_PRIVATE_HEADER} y -----END PRIVATE KEY----- z")
    assert masker.mask_line(one).text == BULLET * len(one.text)
    after = add("next line")
    assert masker.mask_line(after) is after


def test_a_block_the_diff_never_closes_stays_masked_to_the_end() -> None:
    masker = LineMasker()
    masker.mask_line(add(PEM_PRIVATE_HEADER))
    assert all(masker.mask_line(add(f"row {i}")).masked for i in range(5))


def test_old_and_new_sides_track_their_own_blocks() -> None:
    masker = LineMasker()
    old = [
        DiffLine(kind="delete", old=i, text=t)
        for i, t in enumerate((PEM_PRIVATE_HEADER, "OLD", "-----END PRIVATE KEY-----"))
    ]
    new = [add(t) for t in (PEM_PRIVATE_HEADER, "NEW", "-----END PRIVATE KEY-----")]
    assert all(masker.mask_line(line).masked for line in old + new)
    after = DiffLine(kind="context", old=9, new=9, text="after")
    assert masker.mask_line(after) is after


def test_an_open_block_on_one_side_does_not_mask_the_other_sides_lines() -> None:
    masker = LineMasker()
    masker.mask_line(DiffLine(kind="delete", old=1, text=PEM_PRIVATE_HEADER))
    inside = masker.mask_line(DiffLine(kind="delete", old=2, text="old body"))
    outside = DiffLine(kind="add", new=1, text="new text")
    assert inside.masked and masker.mask_line(outside) is outside


def test_a_context_line_inside_a_block_on_either_side_is_masked() -> None:
    masker = LineMasker()
    masker.mask_line(DiffLine(kind="delete", old=1, text=PEM_PRIVATE_HEADER))
    assert masker.mask_line(DiffLine(kind="context", old=2, new=1, text="body")).masked


def pem_pages() -> tuple[FileDiff, ...]:
    patch = read_fixture(FIXTURES / "pem_key_pages.diff")
    return DiffParser().parse(patch, file_id="file-1", snapshot="snapshot-1")


def flat(pages: tuple[FileDiff, ...]) -> list[DiffLine]:
    return [line for page in pages for hunk in page.hunks for line in hunk.lines]


def test_a_key_block_that_starts_on_one_page_stays_masked_on_the_next() -> None:
    pages = pem_pages()
    assert len(pages) == 2 and len(pages[0].hunks[0].lines) == 500
    masked = LineMasker.mask_pages(pages)
    before, after = flat(pages), flat(masked)
    begin = next(i for i, line in enumerate(before) if "BEGIN" in line.text)
    end = next(i for i, line in enumerate(before) if "END" in line.text)
    assert begin < 500 <= end  # the block really crosses the page break
    assert [i for i, line in enumerate(after) if line.masked] == list(range(begin, end + 1))
    for original, hidden in zip(before[begin : end + 1], after[begin : end + 1], strict=True):
        assert hidden.text == BULLET * len(original.text)
    assert all(
        a == b
        for a, b in zip(
            before[:begin] + before[end + 1 :], after[:begin] + after[end + 1 :], strict=True
        )
    )
    assert [hunk.fragment for page in masked for hunk in page.hunks] == [0, 1]


def test_masking_page_by_page_with_one_masker_matches_masking_them_together() -> None:
    pages = pem_pages()
    masker = LineMasker()
    one_by_one = tuple(masker.mask_page(page) for page in pages)
    assert one_by_one == LineMasker.mask_pages(pages)


def test_each_file_starts_with_a_fresh_masker() -> None:
    first = LineMasker()
    first.mask_line(add(PEM_PRIVATE_HEADER))
    assert first.mask_line(add("body")).masked
    assert not LineMasker().mask_line(add("body")).masked


def test_masked_pages_keep_their_structure() -> None:
    pages = pem_pages()
    masked = LineMasker.mask_pages(pages)
    assert [replace(p, hunks=()) for p in masked] == [replace(p, hunks=()) for p in pages]
    for original, hidden in zip(pages, masked, strict=True):
        assert [h.header for h in original.hunks] == [h.header for h in hidden.hunks]
        assert [len(h.lines) for h in original.hunks] == [len(h.lines) for h in hidden.hunks]
