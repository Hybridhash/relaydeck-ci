"""Composition imports and the pictures API preserve their owner objects."""

from relaydeck.gateway import cli
from relaydeck.gateway.app import bootstrap, config, state
from relaydeck.gateway.features.pictures import api
from relaydeck.gateway.features.pictures.adapters import storage
from relaydeck.gateway.http.middleware.body_cap import BodyCapMiddleware


def test_composition_uses_the_canonical_objects() -> None:
    assert bootstrap.GatewayState is state.GatewayState
    assert bootstrap.BodyCapMiddleware is BodyCapMiddleware
    assert cli.ServeConfigError is config.ServeConfigError
    assert cli.resolve_serve_options is config.resolve_serve_options
    assert cli.build_serve_state is config.build_serve_state


def test_pictures_api_exports_only_the_declared_owner_objects() -> None:
    names = {"DAY_MS", "MAX_BYTES", "PictureError", "PictureStore", "run_picture_cleanup"}
    assert {name for name in vars(api) if not name.startswith("_")} == names
    for name in names:
        assert getattr(api, name) is getattr(storage, name)
