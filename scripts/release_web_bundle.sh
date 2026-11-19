#!/usr/bin/env bash
# Build, deterministically archive, sign and verify the release assets: the web
# bundle, the update record, the wheel and the bootstrap installer.
#
# Usage: scripts/release_web_bundle.sh <version> [minisign-secret-key-path]
# The secret key path (or MINISIGN_SECRET_KEY) is only passed to minisign; this
# script never reads or stores the secret. It prints artifacts and the exact
# upload command; uploading is left to the owner.
#
# Order: push the public squash and tag v<version> first, then run this, then upload every
# asset, and only then mark the release latest. Besides the web bundle it signs latest.json, the
# record the update notice reads, which names the public tag's commit.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:?usage: release_web_bundle.sh <version> [secret-key-path]}"
SECRET_KEY="${2:-${MINISIGN_SECRET_KEY:-$HOME/.minisign/minisign.key}}"
OUT="${RELAYDECK_WEB_OUT:-$ROOT/dist-web}"
PY="$ROOT/.venv/bin/python"
# The uv version the rendered bootstrap installer pins and was tested with.
UV_VERSION="0.12.15"

if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "refused: version must be plain X.Y.Z" >&2
  exit 1
fi

PACKAGE_VERSION="$("$PY" -c 'import sys, tomllib
print(tomllib.loads(open(sys.argv[1], encoding="utf-8").read())["project"]["version"])' "$ROOT/pyproject.toml")"
if [ "$PACKAGE_VERSION" != "$VERSION" ]; then
  echo "refused: pyproject version is $PACKAGE_VERSION, not $VERSION" >&2
  exit 1
fi

TAG="v$VERSION"
# The public release commit is a squash of HEAD, so compare file trees, not commits.
HEAD_TREE="$(git -C "$ROOT" rev-parse "HEAD^{tree}")"
TAG_TREE="$(git -C "$ROOT" rev-parse "$TAG^{tree}" 2>/dev/null || true)"
if [ "$TAG_TREE" != "$HEAD_TREE" ]; then
  echo "refused: tag $TAG does not have the same files as HEAD; create the tag first" >&2
  exit 1
fi
if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
  echo "refused: the working tree has uncommitted changes" >&2
  exit 1
fi

if [ ! -f "$SECRET_KEY" ]; then
  echo "refused: no minisign secret key at the given path" >&2
  exit 1
fi
command -v minisign >/dev/null || { echo "refused: minisign is not installed" >&2; exit 1; }
command -v uv >/dev/null || { echo "refused: uv is not installed" >&2; exit 1; }

BASE="relaydeck-web-$VERSION"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

# The release record: refuses before any build when the public tag, its files or a higher tag are
# wrong. It is signed below, with the public commit in the trusted comment.
COMMIT="$(PYTHONPATH="$ROOT/src" "$PY" "$ROOT/scripts/build_latest_record.py" prepare \
  --version "$VERSION" --changelog "$ROOT/CHANGELOG.md" --out "$OUT" --head-tree "$HEAD_TREE")"

# Build the web client plus its sibling build manifest, then check the
# manifest names this version before anything is signed.
scripts/build_web_client.sh "$WORK/$BASE"
MANIFEST="$WORK/$BASE.build-manifest.json"
"$PY" -c 'import json, sys
version = json.load(open(sys.argv[1], encoding="utf-8")).get("version")
sys.exit(0 if version == sys.argv[2] else "refused: manifest version mismatch")' "$MANIFEST" "$VERSION"

# Deterministic archive: sorted entries, fixed owner and timestamps.
ARCHIVE="$OUT/$BASE.tar.gz"
"$PY" - "$WORK" "$ARCHIVE" "$BASE" <<'PYEOF'
import gzip
import io
import sys
import tarfile
from pathlib import Path

work, archive, base = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
bundle = work / base
entries = sorted(
    [p for p in bundle.rglob("*")] + [work / f"{base}.build-manifest.json"],
    key=lambda p: p.relative_to(work).as_posix(),
)
with archive.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in entries:
                info = tarfile.TarInfo(path.relative_to(work).as_posix())
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                if path.is_dir():
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    tar.addfile(info)
                else:
                    data = path.read_bytes()
                    info.size = len(data)
                    info.mode = 0o644
                    tar.addfile(info, io.BytesIO(data))
PYEOF

minisign -S -s "$SECRET_KEY" -m "$ARCHIVE" -t "$BASE.tar.gz"

# Verify the produced signature with the packaged public key before upload,
# through the verifier that ships in the product.
PYTHONPATH="$ROOT/src" "$PY" - "$ARCHIVE" "$BASE.tar.gz" <<'PYEOF'
import sys
from pathlib import Path

from relaydeck.releases import minisign

archive = Path(sys.argv[1])
minisign.verify(
    archive.read_bytes(),
    Path(str(archive) + ".minisig").read_bytes(),
    trusted_comment=sys.argv[2],
)
print("signature verified with the packaged key")
PYEOF

minisign -S -s "$SECRET_KEY" -m "$OUT/latest.json" -t "relaydeck-latest $VERSION $COMMIT"

# The wheel, built once from a git archive export of the tag (never the
# working tree), and the bootstrap installer rendered for exactly that wheel.
mkdir "$WORK/wheel-src"
git -C "$ROOT" archive "$TAG" | tar -x -C "$WORK/wheel-src"
(cd "$WORK/wheel-src" && uv build --wheel --out-dir "$WORK/wheel")
_wheels=("$WORK"/wheel/*.whl)
[ "${#_wheels[@]}" -eq 1 ] && [ -f "${_wheels[0]}" ] \
  || { echo "refused: the wheel build did not produce exactly one wheel" >&2; exit 1; }
WHEEL_FILE="$(basename "${_wheels[0]}")"
mv "${_wheels[0]}" "$OUT/$WHEEL_FILE"
# (the checksum file itself stays owned by the record step's finish)
WHEEL_SHA="$("$PY" -c 'import hashlib, sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$OUT/$WHEEL_FILE")"
"$PY" - "$ROOT/install.sh.in" "$OUT/install.sh" "$VERSION" "$WHEEL_FILE" "$WHEEL_SHA" "$UV_VERSION" <<'PYEOF'
import sys
from pathlib import Path

template, out, version, wheel, wheel_sha, uv_version = sys.argv[1:]
text = Path(template).read_text(encoding="utf-8")
for token, value in (
    ("@VERSION@", version),
    ("@WHEEL@", wheel),
    ("@WHEEL_SHA256@", wheel_sha),
    ("@UV_VERSION@", uv_version),
):
    if token not in text:
        sys.exit(f"refused: install.sh.in is missing {token}")
    text = text.replace(token, value)
Path(out).write_text(text, encoding="utf-8")
PYEOF

# Verify the record with the packaged key, then list every release file in SHA256SUMS.
UPLOADS="$(PYTHONPATH="$ROOT/src" "$PY" "$ROOT/scripts/build_latest_record.py" finish \
  --version "$VERSION" --commit "$COMMIT" --out "$OUT" --base "$BASE" \
  --extra "$WHEEL_FILE" --extra install.sh)"

printf 'archive:  %s\n' "$ARCHIVE"
printf 'upload (creates nothing itself; upload every asset before marking the release latest):\n'
printf 'gh release upload %s' "$TAG"
while IFS= read -r asset; do printf ' "%s"' "$asset"; done <<<"$UPLOADS"
printf '\n'
