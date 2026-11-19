# relaydeck-terminal extension

Flet user extension bridging the `xterm2` Flutter terminal emulator.
Status: the Python control, the wire protocol and the Dart widget are implemented. CI analyzes and
tests the Flutter package on every push (`.github/workflows/ci.yml`, job "flutter extension and
patch tests").

- Python control: `src/relaydeck_terminal/terminal.py`
- Wire protocol + incremental decoder: `src/relaydeck_terminal/protocol.py`
- Flutter widget: `src/flutter/relaydeck_terminal/`
- Fixture viewer example: `examples/viewer/` (run with `flet run --web`; its `pyproject.toml`
  sets `[tool.flet.dev_packages]`)

## Prove it

With the Flutter SDK pinned in `CONTRIBUTING.md`, run the same commands as CI from the Flutter
package. Set `GITHUB_WORKSPACE` to the repository root for the last one.

```sh
cd src/flutter/relaydeck_terminal
flutter pub get --enforce-lockfile
flutter analyze --no-pub
dart format --output=none --set-exit-if-changed lib test tool
flutter test
flutter test --no-pub "$GITHUB_WORKSPACE/tests/native/flet_paste_menu_test.dart"
```
