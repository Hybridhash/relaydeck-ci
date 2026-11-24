"""Patch calls whose targets all exist in the fixture tree (read as text, never run)."""


def patches(monkeypatch, mock):
    monkeypatch.setattr("relaydeck.alive.LIMIT", 1)
    monkeypatch.setattr("relaydeck.alive.function", None)
    monkeypatch.setattr("relaydeck.alive.Box.method", None)
    monkeypatch.delattr("relaydeck.alive.os_path")
    mock.patch("relaydeck.pkg.inner.deep")
    mock.patch.dict("relaydeck.alive.CONDITIONAL", {})
