"""The update section of `relaydeck setup --check`: the check's state and, when a newer signed
release is known, how to update. Read-only; it never fetches."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from relaydeck.companion.adapters import launchd as service
from relaydeck.companion.config import CompanionConfig
from relaydeck.companion.updates import instructions as update_instructions
from relaydeck.companion.updates import schedule as update_schedule
from relaydeck.companion.updates.worker import read_notice


def update_lines(
    config: CompanionConfig,
    config_path: Path,
    *,
    now: Callable[[], float] = time.time,
    home: Path | None = None,
    uid: int | None = None,
    executable: Path | None = None,
    runner: service.Runner = service.quiet_runner,
) -> list[str]:
    notice = read_notice(config)
    if not config.updates.check:
        return ["Update check: off"]
    if notice.outcome == "version":
        return ["Update check: skipped (a developer run or an unknown version)"]
    if notice.last_success is None:
        lines = ["Update check: on (never checked yet)"]
    else:
        verified = (update_schedule.moment(notice.last_success) or "")[:10]
        lines = [f"Update check: on (last verified {verified})"]
        if int(now()) - notice.last_success > update_schedule.STALE_AFTER_S:
            lines.append(
                f"  No successful check since {verified}; look for a newer release by hand."
            )
    found = notice.available
    if found is not None:
        lines.append(f"RelayDeck {found.version} is available (you have {notice.installed}).")
        commands = update_instructions.instructions(
            found,
            config_path=config_path,
            home=home or Path.home(),
            uid=os.getuid() if uid is None else uid,
            executable=executable or Path(sys.argv[0]).expanduser().resolve(),
            runner=runner,
        )
        if commands:
            lines.extend(f"  {line}" for line in commands)
        else:
            lines.append(
                "  The update commands are not shown: the profile path has odd characters."
            )
    return lines
