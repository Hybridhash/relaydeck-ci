"""A lazy export: the name resolves on first access."""

from typing import Any


def __getattr__(name: str) -> Any:
    raise AttributeError(name)
