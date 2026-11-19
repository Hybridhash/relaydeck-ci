# Contributing

Use small changes with a clear purpose. Read the [architecture overview](docs/architecture/README.md),
[compatibility policy](docs/development/compatibility.md) and
[security policy](SECURITY.md) before changing boundaries or public behavior.

## Toolchain

Use Python 3.13, as specified in [.python-version](.python-version), and uv
0.12.15, as used by CI. Flet is pinned to 1.0.3. The Flutter extensions use Flutter
3.44.8 and its bundled Dart SDK. Use uv's managed Python for the documented
development environment:

```sh
UV_PYTHON=3.13 UV_PYTHON_PREFERENCE=only-managed uv sync --locked --extra companion --group dev
```

Keep dependency declarations and the lockfile consistent. The companion extra
contains the Mac server dependencies. See the [development guide](docs/development/README.md)
for the local server and [patch notes](patches/README.md) for native build pins.

## Checks

[CI](.github/workflows/ci.yml) runs the following Python checks on macOS:

```sh
uv run python scripts/verify_versions.py
uv run ruff check --no-cache .
uv run ruff format --check --no-cache .
uv run mypy src
uv run lint-imports --config pyproject.toml
uv run pytest tests/unit tests/architecture -q
uv run pytest tests/contracts tests/integration tests/security -q
uv run pytest tests/ui -q
uv run python scripts/scan_release_secrets.py
uv run python scripts/license_inventory.py --out sbom.json
uv export --locked --no-hashes --no-emit-project --extra companion --format requirements-txt > requirements-audit.txt
uvx pip-audit==2.9.0 -r requirements-audit.txt --no-deps --disable-pip
```

For the terminal extension, run these from its Flutter package:

```sh
flutter pub get --enforce-lockfile
flutter analyze --no-pub
dart format --output=none --set-exit-if-changed lib test tool
flutter test
flutter test --no-pub "$GITHUB_WORKSPACE/tests/native/flet_paste_menu_test.dart"
```

Set `GITHUB_WORKSPACE` to the repository root when running the last command locally.
For the notifications extension, use the same commands without `tool` in the
format command and without the extra native test. CI also runs:

```sh
FLUTTER=flutter bash patches/flet-reconnect-merge-patch/apply.sh --test
```

Manual CI dispatch additionally builds a static web smoke artifact and an unsigned
Android package. Those jobs do not sign or publish a production release. The
dependency audit also runs weekly and on manual dispatch.

## Imports and ownership

Imports follow the layers described in the architecture overview.
[pyproject.toml](pyproject.toml) is the executable authority: run `lint-imports`
rather than inferring an exception from a directory name. The shared kernel is
standard-library-only; the client does not import Mac services. Host foundations
do not import application policy. Runtime packages never import build scripts.

Cross-feature imports use declared public Python entries, and the graph stays
acyclic. Package initializers are inert: no framework initialization, lazy
exports or hidden imports. A composition root owns construction and lifetime;
features receive narrow capabilities rather than the entire application state.

Migration exceptions are exact import edges with a removal point written next
to the entry. Do not add wildcard ignores. Unused ignores fail the check.
Permanent cross-feature links are declared separately and remain subject to
cycle checks. A move changes paths and imports only; split behavior changes and
visual changes into separate commits. Preserve configured logger names.

## Comments

Comments and docstrings in shipped code (`src/` and the extensions) are one or two plain
sentences that say what the code does or why. Tests and scripts may keep longer descriptive
docstrings. In every file, including tests, scripts and documents, comments name no AI tool or
role, reviewer, project stage, task code or date.

## Tests

Keep tests grouped by kind: unit for isolated behavior; contracts for wire and
public-shape guarantees; integration for cooperating components; security for
authorization and adversarial behavior; architecture for import and repository
rules; UI for headless interaction scenarios; native for Flutter behavior;
performance for idle-work budgets and an opt-in measurement; typing for fixtures that
`tests/architecture/test_terminal_protocols.py` runs through mypy. CI runs the typing
fixtures through that architecture test and does not run `tests/performance`; a plain
`uv run pytest` does.
Shared harnesses belong in `tests/support`, and synthetic data in `tests/fixtures`.
Test modules do not import each other. Browser and real-device checks need
separate evidence; headless CI does not establish device behavior.

## Design system admission

A shared control needs two real callers in two different feature slices, typed
props, mounted accessibility checks, a component-catalog entry, control-tree
goldens and deletion of the copies it replaces. It takes tokens and callbacks rather than product state or transport
objects. Colors, dimensions, radii and type
styles come from `theme.toml` through `Tokens`; preserve 48 px touch targets. Existing
controls read the shared tokens module rather than taking `Tokens`, and some keep product
words and literal sizes (see the [architecture overview](docs/architecture/README.md#client-layers));
a new control follows the rule above.
Screens consume data sources, and screens and widgets do not import transport
implementations. Keep component keys, ancestry, hook order and draft lifetimes
stable during moves.

Add an entry in `devtools/catalog_model.py` and synthetic props/callbacks in
`devtools/catalog_controls.py` for every admitted control. The catalog does not
count as a real feature caller. Cover widths 320, 768 and 1440, light and dark
canvases, and normal, focus, error, disabled, loading and long-text cases. Label
unsupported native states as contextual or not applicable; retain explicit notes
for existing controls with fixed console colors.

Create new per-case goldens under `tests/ui/snapshots/golden/catalog/` through
`test_catalog_snapshots.py`. The existing snapshot helper mounts each case twice
and requires identical control trees before golden comparison. Never regenerate
unrelated goldens. Scope `RELAYDECK_UPDATE_SNAPSHOTS=1` to the new cases only;
review the resulting files and rerun with updates disabled.

Accessibility checks must cover action names, keyboard-capable carriers and focus,
disabled inheritance, error/loading announcements, reduced motion and explicit
48 px hit targets. Check narrow layouts, enlarged text and color contrast in the
renderer, and actual Tab traversal on supported browsers/devices. Headless trees
alone do not establish pixel geometry or platform accessibility.

With the pinned development environment active, list or inspect local specimens:

```sh
PYTHONPATH=src:. python -m relaydeck.devtools.catalog --list
FLET_SERVER_IP=127.0.0.1 FLET_SERVER_PORT=8555 PYTHONPATH=src:. \
  python -m relaydeck.devtools.catalog --control filter-field --width 320 --theme dark --state error
PYTHONPATH=src:. python -m pytest tests/unit/design tests/unit/client/design_system tests/ui/client/design_system/snapshots/test_catalog_snapshots.py
```

For local visual evidence, select an already built Flet 1.0.3 / Flutter 3.44.8
renderer and its build manifest. The optional command verifies their hashes and
requires an already installed local browser CLI that runs the capture script with
`nodejs -e` (passed as `--browser`) and a fresh output directory:

```sh
PYTHONPATH=src:. python -m relaydeck.devtools.catalog_screenshots \
  --renderer /path/to/web --manifest /path/to/web.build-manifest.json \
  --browser /path/to/browser-cli --output temp/catalog-captures-new
```

This check never belongs in CI or default pytest and never builds or installs a
renderer. It prints a clear SKIP when inputs are absent or unselected. Inspect the
PNGs and complete provenance before accepting visual evidence; incomplete output
or a development SKIP cannot close the required capture evidence before release.

## Commits

Use the conventions in the existing history: `feat`, `fix`, `refactor`, `test`
or `build`, an optional scope, a colon and a short description of the result.
For documentation-only changes, use `docs`. Separate moves from behavior,
packaging and visual changes so each can be checked independently.
