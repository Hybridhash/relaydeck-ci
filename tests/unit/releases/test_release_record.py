"""The release record step of the release script: the public commit read with `git ls-remote`,
the refusals before anything is signed, the record built from the CHANGELOG, and a SHA256SUMS that
lists the four signed files. The git runner is a fake; nothing reaches the network."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from relaydeck.releases import minisign as minisign
from relaydeck.releases import records as release_records
from tests.support.paths import TESTS_DIR
from tests.support.updates.signing import COMMIT, OTHER_COMMIT, public_blob, sign

ROOT = TESTS_DIR.parent
TREE = "a" * 40
TODAY = date(2026, 12, 2)
SPEC = importlib.util.spec_from_file_location(
    "rd_build_latest_record", ROOT / "scripts" / "build_latest_record.py"
)
assert SPEC is not None and SPEC.loader is not None
release: ModuleType = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

CHANGELOG = """# Changelog

## 0.3.1

- **Pairing**: a pairing window stays open longer. It also shows the host name.
- **Reliability**: a very long line that wraps over two lines of the file and keeps going
  without a full stop for a long time, so that it must be cut at a word boundary somewhere
  past the two hundred character mark of the note, which is why it keeps on going and going.

## 0.3.0

- **Old**: nothing to see.
"""


class Git:
    """A fake git: answers `ls-remote`, `init`, `fetch` and `rev-parse` from a script."""

    def __init__(
        self,
        *,
        tags: str | None = None,
        all_tags: str = "",
        fetch_ok: bool = True,
        tree: str = TREE,
        reachable: bool = True,
    ) -> None:
        self.tags, self.all_tags = tags, all_tags
        self.fetch_ok, self.tree, self.reachable = fetch_ok, tree, reachable
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(args)
        if not self.reachable and "ls-remote" in args:
            return 128, ""
        if args[1:3] == ["ls-remote", "--tags"]:
            return 0, self.all_tags
        if "ls-remote" in args:
            return 0, self.tags or ""
        if "init" in args:
            return 0, ""
        if "fetch" in args:
            return (0, "") if self.fetch_ok else (128, "")
        if "rev-parse" in args:
            return 0, self.tree + "\n"
        raise AssertionError(args)


def annotated(commit: str = COMMIT, tag_object: str = OTHER_COMMIT) -> str:
    return f"{tag_object}\trefs/tags/v0.3.1\n{commit}\trefs/tags/v0.3.1^{{}}\n"


def prepare(tmp_path: Path, git: Git, changelog: str = CHANGELOG) -> str:
    log = tmp_path / "CHANGELOG.md"
    log.write_text(changelog)
    out = tmp_path / "dist"
    return str(release.prepare("0.3.1", log, out, TREE, git, "2026-12-01"))


# -- the public commit ---------------------------------------------------------------------------


def test_an_annotated_tag_uses_its_peeled_commit(tmp_path: Path) -> None:
    assert prepare(tmp_path, Git(tags=annotated())) == COMMIT


def test_a_lightweight_tag_uses_the_plain_line(tmp_path: Path) -> None:
    assert prepare(tmp_path, Git(tags=f"{COMMIT}\trefs/tags/v0.3.1\n")) == COMMIT


def test_the_commit_is_asked_of_the_public_remote_for_both_forms(tmp_path: Path) -> None:
    git = Git(tags=annotated())
    prepare(tmp_path, git)
    first = git.calls[0]
    assert first[:3] == ["git", "ls-remote", release.PUBLIC_REMOTE]
    assert first[3:] == ["refs/tags/v0.3.1", "refs/tags/v0.3.1^{}"]


def test_a_missing_public_tag_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    with pytest.raises(release.Refused, match="does not exist yet"):
        prepare(tmp_path, Git(tags=""))
    assert not (tmp_path / "dist").exists()


def test_an_unreachable_remote_is_refused(tmp_path: Path) -> None:
    with pytest.raises(release.Refused, match="cannot be reached"):
        prepare(tmp_path, Git(reachable=False))


def test_a_public_commit_that_cannot_be_fetched_is_refused(tmp_path: Path) -> None:
    with pytest.raises(release.Refused, match="cannot be fetched"):
        prepare(tmp_path, Git(tags=annotated(), fetch_ok=False))


def test_a_public_commit_with_other_files_than_head_is_refused(tmp_path: Path) -> None:
    with pytest.raises(release.Refused, match="same files as HEAD"):
        prepare(tmp_path, Git(tags=annotated(), tree="b" * 40))
    assert not (tmp_path / "dist").exists()


# -- a higher tag --------------------------------------------------------------------------------


def test_a_numerically_higher_public_release_tag_is_refused(tmp_path: Path) -> None:
    higher = f"{COMMIT}\trefs/tags/v0.10.0\n{COMMIT}\trefs/tags/v0.3.1\n"
    with pytest.raises(release.Refused, match="higher release tag"):
        prepare(tmp_path, Git(tags=annotated(), all_tags=higher))


def test_lower_equal_and_non_release_tags_are_ignored(tmp_path: Path) -> None:
    others = (
        f"{COMMIT}\trefs/tags/v0.3.0\n{COMMIT}\trefs/tags/v0.3.1\n"
        f"{COMMIT}\trefs/tags/v9.0.0-rc1\n{COMMIT}\trefs/tags/nightly\n{COMMIT}\trefs/tags/v0.3.1^{{}}\n"
    )
    assert prepare(tmp_path, Git(tags=annotated(), all_tags=others)) == COMMIT


# -- the record ----------------------------------------------------------------------------------


def test_the_record_comes_from_the_changelog_section_and_passes_its_own_checks(
    tmp_path: Path,
) -> None:
    prepare(tmp_path, Git(tags=annotated()))
    raw = (tmp_path / "dist" / "latest.json").read_bytes()
    doc = json.loads(raw)
    assert doc["version"] == "0.3.1" and doc["commit"] == COMMIT and doc["issued"] == "2026-12-01"
    assert doc["schema"] == "relaydeck.latest/1"
    assert doc["notes"][0] == "Pairing: a pairing window stays open longer."
    assert doc["notes"][1].startswith("Reliability: a very long line")
    assert doc["notes"][1].endswith("…") and len(doc["notes"][1]) <= 200
    assert len(raw) <= release_records.MAX_RECORD_BYTES
    release_records.record_from_doc(doc, today=None)
    assert "Old" not in raw.decode()  # only this version's section


def test_a_missing_changelog_section_is_refused(tmp_path: Path) -> None:
    with pytest.raises(release.Refused, match="no section for 0.3.1"):
        prepare(tmp_path, Git(tags=annotated()), changelog="## 0.3.0\n\n- **x**: y.\n")


def test_a_section_without_entries_is_refused(tmp_path: Path) -> None:
    with pytest.raises(release.Refused, match="no entries"):
        prepare(tmp_path, Git(tags=annotated()), changelog="## 0.3.1\n\nNothing.\n")


def test_markup_is_dropped_and_at_most_twelve_notes_are_kept() -> None:
    many = "## 0.3.1\n\n" + "".join(f"- **T{n}**: `code` and more. Then more.\n" for n in range(20))
    notes = release.notes_from_changelog("0.3.1", many)
    assert len(notes) == 12 and notes[0] == "T0: code and more."


def test_a_record_over_the_size_limit_is_refused() -> None:
    with pytest.raises(release.Refused, match="larger than 4096"):
        release.build_record("0.3.1", COMMIT, "2026-12-01", ["n" * 200] * 12 + ["x" * 3000])


# -- signing, verifying and the sums --------------------------------------------------------------


def sign_release(out: Path, base: str, private: Ed25519PrivateKey) -> None:
    (out / f"{base}.tar.gz").write_bytes(b"archive")
    (out / f"{base}.tar.gz.minisig").write_bytes(b"archive signature")
    raw = (out / "latest.json").read_bytes()
    (out / "latest.json.minisig").write_bytes(
        sign(private, raw, f"relaydeck-latest 0.3.1 {COMMIT}")
    )


def test_finish_verifies_with_the_key_and_lists_the_four_signed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        minisign,
        "PACKAGED_PUBLIC_KEY",
        minisign.parse_public_key(public_blob(private)),
    )
    prepare(tmp_path, Git(tags=annotated()))
    out = tmp_path / "dist"
    base = "relaydeck-web-0.3.1"
    sign_release(out, base, private)
    uploads = release.finish(out, "0.3.1", COMMIT, base, TODAY)
    names = [Path(path).name for path in uploads]
    assert names == [
        f"{base}.tar.gz",
        f"{base}.tar.gz.minisig",
        "latest.json",
        "latest.json.minisig",
        "SHA256SUMS",
    ]
    sums = (out / "SHA256SUMS").read_text().splitlines()
    assert [line.split("  ")[1] for line in sums] == names[:4]
    for line in sums:
        digest, name = line.split("  ")
        assert digest == hashlib.sha256((out / name).read_bytes()).hexdigest()


def test_finish_lists_extra_assets_in_the_sums_and_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        minisign,
        "PACKAGED_PUBLIC_KEY",
        minisign.parse_public_key(public_blob(private)),
    )
    prepare(tmp_path, Git(tags=annotated()))
    out = tmp_path / "dist"
    base = "relaydeck-web-0.3.1"
    sign_release(out, base, private)
    (out / "relaydeck-0.3.1-py3-none-any.whl").write_bytes(b"wheel")
    (out / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    uploads = release.finish(
        out, "0.3.1", COMMIT, base, TODAY, extra=["relaydeck-0.3.1-py3-none-any.whl", "install.sh"]
    )
    names = [Path(path).name for path in uploads]
    assert names[-3:] == ["relaydeck-0.3.1-py3-none-any.whl", "install.sh", "SHA256SUMS"]
    sums = (out / "SHA256SUMS").read_text().splitlines()
    assert [line.split("  ")[1] for line in sums] == names[:-1]


def test_finish_refuses_a_record_signed_for_another_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(
        minisign,
        "PACKAGED_PUBLIC_KEY",
        minisign.parse_public_key(public_blob(private)),
    )
    prepare(tmp_path, Git(tags=annotated()))
    out = tmp_path / "dist"
    sign_release(out, "relaydeck-web-0.3.1", private)
    with pytest.raises(release.Refused, match="not for this release"):
        release.finish(out, "0.3.1", OTHER_COMMIT, "relaydeck-web-0.3.1", TODAY)


def test_finish_refuses_an_unsigned_or_wrongly_signed_record(tmp_path: Path) -> None:
    prepare(tmp_path, Git(tags=annotated()))
    out = tmp_path / "dist"
    sign_release(out, "relaydeck-web-0.3.1", Ed25519PrivateKey.generate())
    with pytest.raises(release_records.UpdateError):
        release.finish(out, "0.3.1", COMMIT, "relaydeck-web-0.3.1", TODAY)
    assert not (out / "SHA256SUMS").exists()


def test_refusals_go_to_stderr_with_a_nonzero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "CHANGELOG.md"
    log.write_text(CHANGELOG)
    code = release.main(
        [
            "prepare",
            "--version",
            "0.3.1",
            "--changelog",
            str(log),
            "--out",
            str(tmp_path / "o"),
            "--head-tree",
            TREE,
        ],
        Git(tags=""),
    )
    captured = capsys.readouterr()
    assert code == 1 and captured.out == "" and "does not exist yet" in captured.err


def test_the_script_runs_the_record_step_before_the_build_and_uploads_every_asset() -> None:
    script = (ROOT / "scripts" / "release_web_bundle.sh").read_text()
    assert script.index('build_latest_record.py" prepare') < script.index("build_web_client.sh")
    assert 'minisign -S -s "$SECRET_KEY" -m "$OUT/latest.json" -t "relaydeck-latest' in script
    assert script.index('build_latest_record.py" finish') < script.index("gh release upload")
    assert "before marking the release latest" in script
    assert "shasum" not in script
    assert 'git -C "$ROOT" archive "$TAG"' in script  # the wheel comes from the tag, not the tree
    assert "uv build --wheel" in script
    assert '--extra "$WHEEL_FILE" --extra install.sh' in script
