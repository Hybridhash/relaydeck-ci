"""Patch calls whose targets were all moved or never existed (read as text, never run)."""


def patches(monkeypatch, mock):
    monkeypatch.setattr("relaydeck.gone.LIMIT", 1)
    monkeypatch.setattr("relaydeck.alive.renamed_function", None)
    monkeypatch.setattr("relaydeck.alive.Box.vanished", None)
    monkeypatch.delattr("relaydeck.pkg.inner.moved")
    mock.patch("relaydeck.pkg.missing_module.deep")
    mock.patch.dict("relaydeck.alive.NOT_THERE", {})
