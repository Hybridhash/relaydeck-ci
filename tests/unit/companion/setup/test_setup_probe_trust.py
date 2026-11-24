"""Setup offers the usage probe folders for trusting, and never chooses for the owner.

Every process, path lookup, prompt and terminal is injected: nothing here can launch an installed
tool or read the real trust files.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion import setup
from relaydeck.companion.config import load_config
from relaydeck.companion.inspection.detect import DetectReport
from relaydeck.companion.setup.probes import load_probe_targets
from relaydeck.usage.limits_plane.probe import ProbeTarget
from tests.support.companion.asgi_companion import config_file
from tests.support.setup.legacy import run_legacy
from tests.support.setup.prereqs import Serve, probes

BLOCK = '[[usage.limits]]\nkind = "{kind}"\nformat = "tui-usage-pane-v1"\n{extra}'
NO_CLIS = DetectReport((), "ok", "ok")


class Rig:
    """Records every say, ask and spawn in order, and holds a fake trust record."""

    def __init__(self, root: Path, *, answers: Sequence[bool] = (True, True)) -> None:
        self.root = root
        self.events: list[tuple[Any, ...]] = []
        self.answers = list(answers)
        self.store = root / "trust.json"
        self.trusted: list[Path] = []
        self.installed = True
        self.fail: BaseException | None = None
        self.trust_on_spawn = True
        self.exit_code = 0
        self.serve = Serve()
        self._write()

    def _write(self) -> None:
        projects = {str(f): {"decision": "trusted"} for f in self.trusted}
        self.store.write_text(json.dumps({"schema_version": 1, "projects": projects}))

    def target(self, name: str = "muse", folder: Path | None = None) -> ProbeTarget:
        return ProbeTarget(
            name, name, folder or self.root / "state" / f"{name}-probe", str(self.store)
        )

    def trust(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.trusted.append(folder)
        self._write()

    def say(self, line: str) -> None:
        self.events.append(("say", line))

    def ask(self, prompt: str) -> bool:
        self.events.append(("ask", prompt))
        return self.answers.pop(0) if self.answers else False

    def spawn(self, argv: Sequence[str], cwd: Path) -> int:
        self.events.append(("spawn", tuple(argv), cwd))
        if self.fail is not None:
            raise self.fail
        if self.trust_on_spawn:
            self.trust(cwd)
        return self.exit_code

    def which(self, name: str) -> str | None:
        return f"/synthetic/{name}" if self.installed else None

    def targets(self, config_path: Path, state_dir: Path) -> tuple[ProbeTarget, ...]:
        """The real loader, with the trust record swapped for the fake one."""
        found = load_probe_targets(config_path, state_dir)
        return tuple(replace(t, trust_store=str(self.store)) for t in found)

    def of(self, kind: str) -> list[tuple[Any, ...]]:
        return [e for e in self.events if e[0] == kind]

    @property
    def said(self) -> list[str]:
        return [e[1] for e in self.of("say")]


def profile(root: Path, *blocks: str) -> Path:
    socket = root / "socket"
    socket.touch()
    path = config_file(root, herdr_socket=str(socket), agent_kinds=["kind-a"])
    if blocks:
        with path.open("a") as out:
            out.write('\n[usage]\ntimezone = "UTC"\n' + "".join(blocks))
    return path


def limits(kind: str, extra: str = "") -> str:
    return BLOCK.format(kind=kind, extra=extra)


def run(
    rig: Rig, path: Path, targets: Callable[..., Sequence[ProbeTarget]] | None = None, **over: Any
) -> setup.SetupReport:
    options: dict[str, Any] = {
        "config_path": path,
        "assume_yes": False,
        "confirm": lambda _prompt: False,
        "source_confirm": rig.ask,
        "source_detect": lambda _path: NO_CLIS,
        "probes": probes(rig.serve),
        "runner": rig.serve,
        "probe_targets": targets or rig.targets,
        "trust_spawn": rig.spawn,
        "trust_which": rig.which,
        "emit_line": rig.say,
    }
    return run_legacy(**(options | over))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path.resolve()


def last_trust_line(rig: Rig) -> str:
    return next(line for line in reversed(rig.said) if line.startswith("muse probe folder is now"))


def baseline(root: Path) -> int:
    """The exit code of the same setup with no probe target at all."""
    other = root / "baseline"
    other.mkdir()
    return run(Rig(other), profile(other), targets=lambda *_: ()).exit_code


# -- the owner's choice -------------------------------------------------------------------


def test_a_trusted_folder_gets_one_line_and_no_question(root: Path) -> None:
    rig = Rig(root)
    target = rig.target()
    rig.trust(target.folder)
    run(rig, profile(root), lambda *_: (target,))
    assert rig.of("ask") == [] and rig.of("spawn") == []
    assert rig.said.count("muse probe folder: trusted") == 1


def test_yes_opens_the_tool_once_in_the_probe_folder_and_reports_its_state(root: Path) -> None:
    rig = Rig(root)
    target = rig.target()
    run(rig, profile(root), lambda *_: (target,))
    assert [e[1] for e in rig.of("ask")] == ["Open muse there now so you can choose Trust? [y/N] "]
    assert rig.of("spawn") == [("spawn", ("muse",), target.folder)]
    assert last_trust_line(rig) == "muse probe folder is now trusted."


def test_no_means_no_spawn_a_later_line_and_the_exit_code_unchanged(root: Path) -> None:
    rig = Rig(root, answers=(False,))
    target = rig.target()
    report = run(rig, profile(root), lambda *_: (target,))
    assert rig.of("spawn") == []
    assert any(line.startswith("Skipped. Later:") for line in rig.said)
    assert report.exit_code == baseline(root)
    assert json.loads(rig.store.read_text())["projects"] == {}


def test_assume_yes_prints_the_instruction_only_even_if_the_callback_would_say_yes(
    root: Path,
) -> None:
    rig = Rig(root, answers=(True, True, True))
    target = rig.target()
    report = run(rig, profile(root), lambda *_: (target,), assume_yes=True)
    assert rig.of("ask") == [] and rig.of("spawn") == []
    trust_lines = [line for line in rig.said if "Trust" in line]
    assert trust_lines == [
        f"Trust RelayDeck's muse folder once: run `muse` in {target.folder}, choose Trust."
    ]
    assert report.exit_code == 0 and target.folder.is_dir()


def test_a_missing_tool_gets_one_line_and_no_folder(root: Path) -> None:
    rig = Rig(root)
    rig.installed = False
    target = rig.target()
    report = run(rig, profile(root), lambda *_: (target,))
    assert rig.of("ask") == [] and rig.of("spawn") == []
    assert rig.said.count("muse not installed; its plan limits stay off until it is.") == 1
    assert not target.folder.exists() and report.exit_code == baseline(root)


def test_the_question_never_goes_through_the_general_confirm(root: Path) -> None:
    rig = Rig(root, answers=(False,))

    def general(prompt: str) -> bool:
        rig.events.append(("general", prompt))
        return True

    run(rig, profile(root), lambda *_: (rig.target(),), confirm=general)
    assert not any("Trust" in str(e[1]) for e in rig.of("general"))
    assert len(rig.of("ask")) == 1


# -- failures never launch the tool and never fail setup ----------------------------------


def test_a_folder_that_cannot_be_made_is_advice_without_a_question(root: Path) -> None:
    rig = Rig(root)
    blocker = root / "blocker"
    blocker.write_text("")
    target = rig.target(folder=blocker / "muse-probe")
    report = run(rig, profile(root), lambda *_: (target,))
    assert rig.of("ask") == [] and rig.of("spawn") == []
    assert any("Could not prepare the muse probe folder" in line for line in rig.said)
    assert report.exit_code == baseline(root)


def test_a_symlinked_probe_folder_is_refused_and_the_project_stays_empty(root: Path) -> None:
    rig = Rig(root)
    project = root / "project"
    project.mkdir()
    link = root / "state" / "muse-probe"
    link.parent.mkdir()
    link.symlink_to(project)
    run(rig, profile(root), lambda *_: (rig.target(folder=link),))
    assert rig.of("ask") == [] and rig.of("spawn") == []
    assert list(project.iterdir()) == []
    assert any("Could not prepare" in line for line in rig.said)


def test_a_spawn_error_is_advice_with_no_raw_error_text(root: Path) -> None:
    rig = Rig(root)
    rig.fail = FileNotFoundError(2, "No such file", "/synthetic/leaked/path")
    target = rig.target()
    report = run(rig, profile(root), lambda *_: (target,))
    text = "\n".join(rig.said)
    assert "Could not open muse" in text and f"in {target.folder}" in text
    assert "leaked" not in text and "No such file" not in text
    assert report.exit_code == baseline(root)


def test_an_interrupt_during_the_tool_ends_setup_with_130_and_the_report_so_far(
    root: Path,
) -> None:
    rig = Rig(root)
    rig.fail = KeyboardInterrupt()
    report = run(rig, profile(root), lambda *_: (rig.target(),))
    assert report.exit_code == 130
    assert any("Interrupted during" in line for line in rig.said)


@pytest.mark.parametrize("code", [0, 1, 130])
def test_the_exit_code_of_the_tool_is_not_proof_of_trust(root: Path, code: int) -> None:
    rig = Rig(root)
    rig.trust_on_spawn = False
    rig.exit_code = code
    report = run(rig, profile(root), lambda *_: (rig.target(),))
    assert last_trust_line(rig) == "muse probe folder is now not trusted."
    assert report.exit_code == baseline(root)


# -- ordering and the single output sink --------------------------------------------------


def test_two_targets_follow_each_other_in_order_with_nothing_printed_twice(root: Path) -> None:
    rig = Rig(root)
    first, second = rig.target("muse"), rig.target("kiro")
    report = run(rig, profile(root), lambda *_: (first, second))
    steps = [e[0] if e[0] != "say" else e[1] for e in rig.events]
    start = steps.index(
        "RelayDeck reads muse usage in its own empty folder; muse asks once whether to trust it."
    )
    assert steps[start : start + 6] == [
        "RelayDeck reads muse usage in its own empty folder; muse asks once whether to trust it.",
        "ask",
        "Choose Trust in muse, then type /quit.",
        "spawn",
        "muse probe folder is now trusted.",
        "RelayDeck reads kiro usage in its own empty folder; kiro asks once whether to trust it.",
    ]
    assert [e[2] for e in rig.of("spawn")] == [first.folder, second.folder]
    assert len(rig.said) == len(set(rig.said))
    assert not set(rig.said) & set(report.lines)


def test_trust_lines_land_before_the_hook_and_serve_lines_when_nothing_is_emitted(
    root: Path,
) -> None:
    rig = Rig(root, answers=(False,))
    target = rig.target()
    path = profile(root)
    report = run(rig, path, lambda *_: (target,), emit_line=None)
    skipped = next(i for i, line in enumerate(report.lines) if line.startswith("Skipped."))
    assert skipped < next(i for i, line in enumerate(report.lines) if "Serve command" in line)
    assert rig.said == []


# -- which targets, and when -------------------------------------------------------------


def test_a_target_added_by_the_limits_step_in_the_same_run_is_offered(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(root, answers=(False,))
    path = profile(root)

    def add(config_path: Path, **_kw: Any) -> tuple[list[str], int]:
        with config_path.open("a") as out:
            out.write('\n[usage]\ntimezone = "UTC"\n' + limits("muse"))
        return ["Plan limits: added."], 0

    monkeypatch.setattr(setup.usage_steps, "offer_limits", add)
    run(rig, path)
    assert [e[1] for e in rig.of("ask")] == ["Open muse there now so you can choose Trust? [y/N] "]


def test_disabled_and_repeated_blocks_make_one_offer_per_enabled_kind(root: Path) -> None:
    rig = Rig(root, answers=(False, False))
    path = profile(
        root,
        limits("muse", "enabled = false\n"),
        limits("kiro"),
        limits("kiro"),
    )
    run(rig, path)
    asked = [e[1] for e in rig.of("ask")]
    assert asked == ["Open kiro-cli there now so you can choose Trust? [y/N] "]


@pytest.mark.parametrize("stage", ["offer_sources", "offer_limits"])
def test_a_failed_optional_step_is_reported_and_the_later_steps_still_run(
    root: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    rig = Rig(root)
    monkeypatch.setattr(setup.usage_steps, stage, lambda *_a, **_k: (["stopped"], 1))
    calls: list[object] = []

    def targets(*args: object) -> tuple[ProbeTarget, ...]:
        calls.append(args)
        return (rig.target(),)

    report = run(rig, profile(root), targets)
    assert report.exit_code == 1 and calls != []


def test_without_a_target_setup_reads_exactly_as_it_did(root: Path) -> None:
    first, second = root / "a", root / "b"
    first.mkdir()
    second.mkdir()
    none, real = Rig(first), Rig(second)
    left = run(none, profile(first), lambda *_: ())
    right = run(real, profile(second))  # the real loader on a config with no probe block

    def bare(lines: Sequence[str], base: Path) -> list[str]:
        return [line.replace(str(base), "") for line in lines]

    assert bare(left.lines, first) == bare(right.lines, second)
    assert left.exit_code == right.exit_code
    assert bare(none.said, first) == bare(real.said, second)


def test_hook_and_serve_failures_keep_their_exit_codes(root: Path) -> None:
    rig = Rig(root, answers=(False,))
    path = profile(root)
    hook = run(rig, path, lambda *_: (rig.target(),), install_limits_hook=True)
    assert hook.exit_code == 1 and any("Limits hook: not_configured" in x for x in rig.said)


# -- the same folder as the companion ---------------------------------------------------


def test_a_relative_state_dir_names_the_companions_folder_from_any_directory(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = profile(root, limits("muse"))
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    companion = load_config(path).state_dir / "muse-probe"
    assert companion == root / "state" / "muse-probe"

    rig = Rig(root, answers=(False,))
    run(rig, path)
    assert rig.of("spawn") == [] and companion.is_dir()
    assert not (elsewhere / "state").exists()
    # `usage install` and `usage repair` read the same folder from the config alone
    assert [t.folder for t in load_probe_targets(path)] == [companion]


def test_the_step_uses_the_loaded_configs_state_dir(root: Path) -> None:
    rig = Rig(root, answers=(False,))
    path = profile(root, limits("muse"))
    seen: list[Path] = []

    def targets(config_path: Path, state_dir: Path) -> tuple[ProbeTarget, ...]:
        seen.append(state_dir)
        return ()

    run(rig, path, targets)
    assert seen == [load_config(path).state_dir]


def test_the_real_defaults_are_the_real_loader_and_the_real_launcher() -> None:
    params = inspect.signature(setup.run_setup).parameters
    assert params["probe_targets"].default is load_probe_targets
    assert params["trust_spawn"].default is None and params["trust_which"].default is None
