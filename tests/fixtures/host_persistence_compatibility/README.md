# Persistence compatibility fixture

Written by the pre-move code at `6300f2d` using an in-memory key provider, a fixed clock, and synthetic identities only. Compressed files preserve the encrypted database, command journal, private JSON, modes and reserve files. `expected.json` contains test-only decryption keys and public API expectations. No real Keychain or user directory was accessed. The plist uses synthetic absolute paths.
