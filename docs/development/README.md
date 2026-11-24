## Development

<details>
<summary>Tests, lint and the local dev server</summary>

```sh
uv sync --locked --extra companion --group dev
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

Build the hosted web assets before starting the local server with synthetic sessions
(no Herdr needed). The build requires the Flutter toolchain described in
[patches/README.md](../../patches/README.md).

```sh
uv sync --locked --extra companion --group dev
export FLET_WEB_NO_CDN=true
scripts/build_web_client.sh /private/tmp/relaydeck-dev-web
FLET_WEB_PATH=/private/tmp/relaydeck-dev-web \
  uv run python -m relaydeck.companion.app.hosted_web.serve --dev-secret --dev-login --dev-demo-sessions
```

Keep the generated index and csp.json together, and rebuild after changing the hosted renderer
configuration. Missing or incompatible assets cause an explanatory exit before the server starts.

Native Android and iOS builds (in progress): see [patches/README.md](../../patches/README.md) and
`scripts/build_native.py`.

The repository tracks the installer as the template `install.sh.in`. `scripts/release_web_bundle.sh`
renders it to `install.sh` in `dist-web/` (or `$RELAYDECK_WEB_OUT`). To try that rendered installer
against a wheel you built (`uv build --wheel`), point it at the local wheel; it is still
hash-checked:

```sh
RELAYDECK_WHEEL=dist/relaydeck-0.3.0-py3-none-any.whl \
  RELAYDECK_WHEEL_SHA256="$(shasum -a 256 dist/relaydeck-0.3.0-py3-none-any.whl | cut -d' ' -f1)" \
  sh dist-web/install.sh
```

</details>

## Related guides

- [Contributing](../../CONTRIBUTING.md)
- [Architecture](../architecture/README.md)
- [Recovery and backups](recovery.md)
- [Compatibility](compatibility.md)
- [Usage hook management](usage-hooks.md)
