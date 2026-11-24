# Architecture decisions

The following records describe the repository's current ownership and release policies.

- [One distribution](one-wheel.md): ship one Python distribution with explicit native and hosted boundaries.
- [Client layers](client-layers.md): keep composition, screens, widgets, features and shared presentation in their declared layers.
- [Mac contexts](mac-contexts.md): organize Mac responsibilities by their ownership and public entries.
- [Signed releases](signed-releases.md): verify the signed web archive and update record at their declared trust boundaries.
- [Herdr installer](herdr-installer.md): run Herdr's unsigned install script only after consent, with a scrubbed environment, a deadline and a manual fallback.
- [English-only interface](english-only.md): keep the 0.3 interface and formatting policy explicit.
