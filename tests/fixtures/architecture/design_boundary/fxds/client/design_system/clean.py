"""Imports only what the boundary allows."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from fxds.client.design_system import sibling
from fxds.client.design_system.sibling import __doc__ as sibling_doc
from fxds.client.platform.diagnostics.terminal import __doc__ as diag_doc
from fxds.client.platform.flet import events

if TYPE_CHECKING:
    pass

__all__ = ["diag_doc", "events", "json", "sibling", "sibling_doc"]
