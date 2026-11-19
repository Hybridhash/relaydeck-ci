"""Read-only bounded executable lookup in the companion's PATH. Never execute probes."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def discover_agent_catalog(
    observed_ms: int,
    *,
    kinds: Sequence[str],
    programs: Mapping[str, str | None] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Report configured kinds found on PATH without executing them. A programs entry of None
    marks an unsupported kind; otherwise look up its mapped program or the kind name.
    """
    doc: dict[str, Any] = {
        "status": "ready",
        "source": "companion-path",
        "observed_ms": observed_ms,
        "entries": [],
    }
    found: dict[str, bool] = {}
    try:
        for kind in kinds:
            program = kind if programs is None or kind not in programs else programs[kind]
            if program is None:
                available, reason = False, "unsupported"
            else:
                if program not in found:
                    found[program] = which(program) is not None
                available = found[program]
                reason = "installed" if available else "not_installed"
            doc["entries"].append({"kind": kind, "available": available, "reason": reason})
    except OSError:
        doc["status"] = "error"
        doc["entries"] = []
    return doc
