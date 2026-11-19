"""The application version: defined once in pyproject.toml, read from package metadata.

The packaged mobile build ships no package metadata, so its stager writes `_version.py`
from the same pyproject.toml.
"""

from __future__ import annotations

import re
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

UNKNOWN = "0+unknown"


def _resolve() -> str:
    try:
        return version("relaydeck")
    except PackageNotFoundError:
        pass
    try:
        stamp = import_module("relaydeck._version")
    except ImportError:
        return UNKNOWN
    return str(stamp.VERSION)


__version__ = _resolve()


def _resolve_build_revision() -> str:
    """Only packaged build metadata identifies the server; never inspect checkout paths."""
    try:
        stamp = import_module("relaydeck._build")
    except ImportError:
        return "unknown"
    revision = getattr(stamp, "REVISION", None)
    if type(revision) is not str or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        return "unknown"
    return revision


__build_revision__ = _resolve_build_revision()
