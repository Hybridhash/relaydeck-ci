# Herdr installer

## Context

Setup needs Herdr to be installed. Herdr publishes an install script at a fixed
address but no signed release, so RelayDeck cannot verify what it downloads the
way it verifies its own web archive and update record.

## Decision

When Herdr is missing, setup may fetch Herdr's install script from the pinned
host `herdr.dev` over HTTPS without a signature, and only with the owner's
consent: a yes to setup's question, or running `relaydeck setup --yes`, which
answers the install questions in advance. The script is downloaded under the release download rules (TLS,
redirects only to the pinned host, a size cap and one absolute deadline), written
to an owner-only directory made for that run, and run by `/bin/sh` in its own
process group. It receives only `HOME`, a system `PATH`, `TMPDIR`, `LANG` and
`USER`, and it is stopped, with its whole group, if it passes its deadline or
setup is interrupted. If the owner declines, the download fails, or the install
fails, setup prints the manual install command and changes nothing further.

## Consequences

The script is trusted on TLS and the host pin alone: neither a signature nor a
digest ties it to a known release, and the files the script itself downloads are
not checked by RelayDeck. Consent (given up front by `--yes`), a scrubbed environment, a deadline and
process-group termination limit what a bad script can reach and how long it
runs, but not what it does as the owner. The owner can always install Herdr by
hand instead. The installed file must still pass setup's ownership and
permission checks before it is used.

## Revisit when

Herdr publishes signed releases, so setup can verify the script or the Herdr
binary against a published key, or when the pinned host or script address
changes.
