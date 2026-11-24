"""Request tokens and load-state transitions shared by Changes views."""

from __future__ import annotations

from dataclasses import replace

from relaydeck.client.features.changes.state import ChangesModel, Load, Slot


def _load(model: ChangesModel, slot: Slot) -> Load:
    if slot == "files":
        return model.files_load
    if slot == "diff":
        return model.diff_load
    if slot == "bases":
        return model.bases_load
    if slot == "graph":
        return model.graph_load
    if slot == "graph_check":
        return model.graph_check
    return model.save_load


def _with_load(model: ChangesModel, slot: Slot, load: Load) -> ChangesModel:
    if slot == "files":
        return replace(model, files_load=load)
    if slot == "diff":
        return replace(model, diff_load=load)
    if slot == "bases":
        return replace(model, bases_load=load)
    if slot == "graph":
        return replace(model, graph_load=load)
    if slot == "graph_check":
        return replace(model, graph_check=load)
    return replace(model, save_load=load)


def _renewed(old: ChangesModel, new: ChangesModel) -> ChangesModel:
    """`new` with idle loads newer than any request `old` made, so late replies drop."""
    return replace(
        new,
        files_load=Load(old.files_load.token + 1),
        diff_load=Load(old.diff_load.token + 1),
        bases_load=Load(old.bases_load.token + 1),
        save_load=Load(old.save_load.token + 1),
        graph_load=Load(old.graph_load.token + 1),
        graph_check=Load(old.graph_check.token + 1),
    )


def _start(model: ChangesModel, slot: Slot) -> tuple[ChangesModel, int]:
    token = _load(model, slot).token + 1
    return _with_load(model, slot, Load(token, "loading")), token


def _current(model: ChangesModel, slot: Slot, token: int) -> bool:
    load = _load(model, slot)
    return load.token == token and load.status == "loading"


def _ready(model: ChangesModel, slot: Slot) -> ChangesModel:
    return _with_load(model, slot, Load(_load(model, slot).token, "ready"))
