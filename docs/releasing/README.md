## Updating

<details>
<summary>How to update</summary>

About once a day the Mac checks for a newer signed release (see [Privacy](../../README.md#privacy)). When there is
one, Settings shows "RelayDeck X.Y.Z is available on your Mac" and `relaydeck setup --check`
prints it, each with the exact commands for your install. They are the same as these, with the
signed version filled in:

```sh
uv tool install --managed-python --python 3.13 --force \
  "relaydeck[companion] @ https://github.com/Hybridhash/relaydeck/releases/download/v<version>/relaydeck-<version>-py3-none-any.whl" \
  && relaydeck web fetch --version <version> \
  && relaydeck stop
printf '%s\n' 'Then start RelayDeck again in its terminal: relaydeck start --connect-herdr'
printf '%s\n' 'Manual update: if it fails, run the same commands again; there is no automatic rollback yet.'
```

The commands install the release wheel for the version the signed record names (the file the
install command above installs, so no git is needed), fetch the signed web bundle for that version,
and stop RelayDeck; start it again in its terminal afterwards. If you run
the login service and it was set up for this install, the last command restarts it in place
instead; a login service set up by 0.2.x is restarted the same way, with a reminder to run
`relaydeck setup` once to replace its older command. `&&` means nothing restarts after a failed install or fetch. RelayDeck does not install
anything itself and has no automatic rollback yet: if an update fails, run the same commands
again. The wheel is an HTTPS release asset, not a signed file: only the web bundle and the update record are signed.

Release files, when published, are signed with [minisign](https://jedisct1.github.io/minisign/). Verify one
with the public key in [`minisign.pub`](../../minisign.pub): `minisign -Vm <file> -p minisign.pub`.

</details>

## Related guides

- [Signed releases](../decisions/signed-releases.md)
- [Compatibility](../development/compatibility.md)
- [Recovery and backups](../development/recovery.md)
