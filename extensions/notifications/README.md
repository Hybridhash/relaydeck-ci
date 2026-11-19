# relaydeck-notifications extension

Flet user extension bridging Firebase Cloud Messaging and Android app
links. Status: the Python control and the Dart side are implemented. CI analyzes and tests the
Flutter package on every push (`.github/workflows/ci.yml`, job "flutter extension and patch
tests"). Device delivery is not part of that run.

Firebase project setup is a separately authorized owner step: do NOT create a Firebase project,
download `google-services.json`, or register sender IDs without approval. The
`google-services.json` file must never be committed.

## Prove it

With the Flutter SDK pinned in `CONTRIBUTING.md`, run the same commands as CI from the Flutter
package.

```sh
cd src/flutter/relaydeck_notifications
flutter pub get --enforce-lockfile
flutter analyze --no-pub
dart format --output=none --set-exit-if-changed lib test
flutter test
```

Device proof additionally needs a test Firebase project, a release-mode
APK on a physical device, and the device matrix (background, kill,
force-stop, reboot, Doze, rotation, reinstall, denied permission).
