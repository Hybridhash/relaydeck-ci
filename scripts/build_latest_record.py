"""The signed release record (`latest.json`) for the update notice.

`prepare` checks the public release before anything is signed (the public tag exists, its commit
can be fetched and has HEAD's file tree, and no higher release tag exists) and writes `latest.json`
from the version's CHANGELOG section. `finish` verifies the signed record with the packaged
verifier and writes `SHA256SUMS` for the four release files. Signing itself is done by
`release_web_bundle.sh`, which passes the secret key only to minisign.

The git runner is injectable; tests never reach the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from relaydeck.releases import records as release_records

PUBLIC_REMOTE = "https://github.com/Hybridhash/relaydeck"
RECORD_NAME = "latest.json"
Runner = Callable[[list[str]], tuple[int, str]]

_COMMIT = re.compile(r"[0-9a-f]{40}")
_TAG = re.compile(r"refs/tags/v([0-9]+\.[0-9]+\.[0-9]+)(\^\{\})?")


class Refused(Exception):
    """The release must not be signed; the message is safe to print."""


def run_git(args: list[str]) -> tuple[int, str]:
    done = subprocess.run(args, capture_output=True, text=True, check=False, timeout=120)  # noqa: S603
    return done.returncode, done.stdout


def public_commit(version: str, run: Runner) -> str:
    """The commit of the public tag v<version>: the peeled line of an annotated tag, else the
    plain line of a lightweight one."""
    code, out = run(
        ["git", "ls-remote", PUBLIC_REMOTE, f"refs/tags/v{version}", f"refs/tags/v{version}^{{}}"]
    )
    if code != 0:
        raise Refused("refused: the public repository cannot be reached")
    plain = peeled = None
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/tags/v{version}^{{}}":
            peeled = sha
        elif ref == f"refs/tags/v{version}":
            plain = sha
    commit = peeled or plain
    if commit is None or _COMMIT.fullmatch(commit) is None:
        raise Refused(f"refused: the public tag v{version} does not exist yet; push and tag first")
    return commit


def check_tree(commit: str, head_tree: str, run: Runner) -> None:
    """The public commit must be fetchable and hold exactly HEAD's files."""
    with tempfile.TemporaryDirectory() as scratch:
        steps = (
            ["git", "init", "--bare", "--quiet", scratch],
            ["git", "-C", scratch, "fetch", "--quiet", "--depth", "1", PUBLIC_REMOTE, commit],
        )
        for step in steps:
            if run(step)[0] != 0:
                raise Refused("refused: the public release commit cannot be fetched")
        code, out = run(["git", "-C", scratch, "rev-parse", "FETCH_HEAD^{tree}"])
    if code != 0 or out.strip() != head_tree:
        raise Refused("refused: the public release commit does not have the same files as HEAD")


def refuse_higher_tag(version: str, run: Runner) -> None:
    code, out = run(["git", "ls-remote", "--tags", PUBLIC_REMOTE])
    if code != 0:
        raise Refused("refused: the public repository cannot be reached")
    ours = release_records.parse_version(version)
    for line in out.splitlines():
        match = _TAG.fullmatch(line.partition("\t")[2])
        found = release_records.parse_version(match.group(1)) if match else None
        if found is not None and ours is not None and found > ours:
            raise Refused("refused: a higher release tag already exists on the public repository")


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("**", "").replace("`", "")).strip()


def _cut(text: str, limit: int = release_records.MAX_NOTE_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return head + "…"


def notes_from_changelog(version: str, changelog: str) -> list[str]:
    """One note per bullet of the version's section: its bold title and first sentence."""
    lines = changelog.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == f"## {version}")
    except StopIteration:
        raise Refused(f"refused: CHANGELOG has no section for {version}") from None
    bullets: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            bullets.append([line[2:]])
        elif bullets and line.startswith("  ") and line.strip():
            bullets[-1].append(line.strip())
    notes = []
    for parts in bullets:
        text = _plain(" ".join(parts))
        sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
        if sentence:
            notes.append(_cut(sentence))
    if not notes:
        raise Refused(f"refused: the CHANGELOG section for {version} has no entries")
    return notes[: release_records.MAX_NOTES]


def build_record(version: str, commit: str, issued: str, notes: list[str]) -> bytes:
    doc = {
        "schema": release_records.SCHEMA,
        "version": version,
        "commit": commit,
        "issued": issued,
        "notes": notes,
    }
    raw = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(raw) > release_records.MAX_RECORD_BYTES:
        raise Refused("refused: the release record is larger than 4096 bytes")
    try:
        release_records.record_from_doc(json.loads(raw), today=None)
    except release_records.UpdateError:
        raise Refused("refused: the release record does not pass its own checks") from None
    return raw


def prepare(
    version: str, changelog: Path, out: Path, head_tree: str, run: Runner, issued: str
) -> str:
    """All public checks, then latest.json. Returns the commit to sign into the trusted comment."""
    commit = public_commit(version, run)
    check_tree(commit, head_tree, run)
    refuse_higher_tag(version, run)
    notes = notes_from_changelog(version, changelog.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    (out / RECORD_NAME).write_bytes(build_record(version, commit, issued, notes))
    return commit


def write_sums(out: Path, names: list[str]) -> None:
    lines = [f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}\n" for name in names]
    (out / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def finish(
    out: Path,
    version: str,
    commit: str,
    base: str,
    today: date | None = None,
    extra: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Verify the signed record with the packaged key, write SHA256SUMS for the release files
    (the four signed ones plus any `extra` asset names) and return the upload paths."""
    raw = (out / RECORD_NAME).read_bytes()
    signature = (out / f"{RECORD_NAME}.minisig").read_bytes()
    record = release_records.verify_record(raw, signature, today=today)
    if (record.version, record.commit) != (version, commit):
        raise Refused("refused: the signed record is not for this release")
    names = [
        f"{base}.tar.gz",
        f"{base}.tar.gz.minisig",
        RECORD_NAME,
        f"{RECORD_NAME}.minisig",
        *extra,
    ]
    write_sums(out, names)
    return [*(str(out / name) for name in names), str(out / "SHA256SUMS")]


def main(argv: list[str] | None = None, run: Runner = run_git) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    first = sub.add_parser("prepare")
    first.add_argument("--version", required=True)
    first.add_argument("--changelog", required=True, type=Path)
    first.add_argument("--out", required=True, type=Path)
    first.add_argument("--head-tree", required=True)
    last = sub.add_parser("finish")
    last.add_argument("--version", required=True)
    last.add_argument("--commit", required=True)
    last.add_argument("--out", required=True, type=Path)
    last.add_argument("--base", required=True)
    last.add_argument("--extra", action="append", default=[], help="extra asset name in --out")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            issued = datetime.now(UTC).date().isoformat()
            print(prepare(args.version, args.changelog, args.out, args.head_tree, run, issued))
        else:
            print(
                "\n".join(finish(args.out, args.version, args.commit, args.base, extra=args.extra))
            )
    except (Refused, release_records.UpdateError, OSError) as exc:
        message = exc if isinstance(exc, Refused) else "refused: the release record is not valid"
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
