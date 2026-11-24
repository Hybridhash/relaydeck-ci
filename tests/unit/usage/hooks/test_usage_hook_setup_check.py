import json
import os
from importlib.resources import files

import pytest

from relaydeck.usage.hooks.setup import (
    HOOK_SHA256,
    HookSetupCheck,
    bound_interpreter,
    managed_command,
)


@pytest.mark.parametrize(
    "state",
    [
        "not_configured",
        "script_missing",
        "script_outdated",
        "settings_missing",
        "statusline_missing",
        "conflict",
        "installed",
        "invalid",
        "unreadable",
    ],
)
def test_installation_states_are_positive_readonly_evidence(tmp_path, state, monkeypatch):
    root = tmp_path.resolve()
    script, settings, cache, interpreter = (
        root / x for x in ("hook.py", "settings", "cache", "python")
    )
    script.write_bytes(
        files("relaydeck.usage.resources").joinpath("statusline_limits_hook.py").read_bytes()
    )
    script.chmod(0o600)
    interpreter.write_text("not executed")
    interpreter.chmod(0o700)
    settings.write_text(
        json.dumps(
            {
                "statusLine": {
                    "type": "command",
                    "command": managed_command(script, cache, interpreter),
                },
                "unrelated": 3,
            }
        )
    )
    settings.chmod(0o600)
    check = HookSetupCheck(settings, cache, script, interpreter=interpreter)
    before = settings.read_bytes()
    if state == "not_configured":
        check.script = None
    elif state == "script_missing":
        script.unlink()
    elif state == "script_outdated":
        script.write_text("modified")
    elif state == "settings_missing":
        settings.unlink()
    elif state == "statusline_missing":
        settings.write_text('{"unrelated":3}')
    elif state == "conflict":
        settings.write_text('{"statusLine":{"type":"command","command":"never run"}}')
    elif state == "invalid":
        settings.write_text('{"statusLine":1,"statusLine":2}')
    elif state == "unreadable":

        def denied(*a, **k):
            raise PermissionError("secret")

        monkeypatch.setattr("relaydeck.usage.hooks.setup._read", denied)
    assert check.check(1234) == {"state": state, "checked_ms": 1234}
    if state == "installed":
        assert settings.read_bytes() == before
    assert len(HOOK_SHA256) == 64


def test_setup_age_and_unmanaged_are_separate_from_limits_freshness(tmp_path):
    now = [0.0]
    check = HookSetupCheck(tmp_path.resolve() / "settings", None, None, monotonic=lambda: now[0])
    assert check.check(100) == {"state": "not_configured", "checked_ms": 100}
    now[0] = 59.9
    assert check.check(999) == {"state": "not_configured", "checked_ms": 100}
    now[0] = 60
    assert check.check(1000) == {"state": "not_configured", "checked_ms": 1000}
    assert HookSetupCheck(None, None, None).check(100) is None


def test_default_interpreter_keeps_the_absolute_tool_environment_symlink(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    base = root / "base-python"
    base.write_text("never executed")
    base.chmod(0o700)
    interpreter = root / "tool-env" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(base)
    script, settings, cache = (root / name for name in ("hook.py", "settings", "cache"))
    script.write_bytes(
        files("relaydeck.usage.resources").joinpath("statusline_limits_hook.py").read_bytes()
    )
    script.chmod(0o600)
    settings.write_text(
        json.dumps(
            {
                "statusLine": {
                    "type": "command",
                    "command": managed_command(script, cache, interpreter),
                }
            }
        )
    )
    settings.chmod(0o600)
    monkeypatch.setattr("relaydeck.usage.hooks.setup.sys.executable", str(interpreter))
    check = HookSetupCheck(settings, cache, script)
    assert check.check(123) == {"state": "installed", "checked_ms": 123}
    assert check.interpreter == interpreter
    # A resolved base-interpreter command is not the installed tool environment.
    settings.write_text(
        json.dumps(
            {"statusLine": {"type": "command", "command": managed_command(script, cache, base)}}
        )
    )
    assert HookSetupCheck(settings, cache, script).check(124)["state"] == "conflict"


@pytest.mark.parametrize("unsafe", ["symlink", "directory", "oversize", "mode", "nonobject"])
def test_setup_rejects_unsafe_or_unparseable_settings(tmp_path, unsafe):
    root = tmp_path.resolve()
    script = root / "hook.py"
    script.write_bytes(
        files("relaydeck.usage.resources").joinpath("statusline_limits_hook.py").read_bytes()
    )
    script.chmod(0o600)
    settings = root / "settings"
    settings.write_text("{}")
    settings.chmod(0o600)
    if unsafe == "symlink":
        settings.unlink()
        settings.symlink_to(script)
    elif unsafe == "directory":
        settings.unlink()
        settings.mkdir()
    elif unsafe == "oversize":
        settings.write_bytes(b"x" * (1024 * 1024 + 1))
    elif unsafe == "mode":
        settings.chmod(0o666)
    elif unsafe == "nonobject":
        settings.write_text("[]")
    assert HookSetupCheck(settings, root / "cache", script).check(123)["state"] == "invalid"


# -- the interpreter reached through a linked folder --------------------------------------


def _linked_install(root, spelled=None):
    """A current script, an executable reached through a linked folder, and a settings line."""
    real = root / "tool-env" / "bin" / "python"
    real.parent.mkdir(parents=True)
    real.write_text("never executed")
    real.chmod(0o700)
    (root / "alias").symlink_to(root / "tool-env", target_is_directory=True)
    script, settings, cache = (root / name for name in ("hook.py", "settings", "cache"))
    script.write_bytes(
        files("relaydeck.usage.resources").joinpath("statusline_limits_hook.py").read_bytes()
    )
    script.chmod(0o600)
    recorded = spelled or root / "alias" / "bin" / "python"
    settings.write_text(
        json.dumps(
            {
                "statusLine": {
                    "type": "command",
                    "command": managed_command(script, cache, recorded),
                }
            }
        )
    )
    settings.chmod(0o600)
    return real, script, settings, cache


def _state(settings, cache, script, real):
    return HookSetupCheck(settings, cache, script, interpreter=real).check(1)["state"]


def test_our_command_through_a_linked_folder_is_installed(tmp_path):
    real, script, settings, cache = _linked_install(tmp_path.resolve())
    before = settings.read_bytes()
    assert _state(settings, cache, script, real) == "installed"
    assert settings.read_bytes() == before


def test_the_exact_command_never_looks_at_the_filesystem_for_a_spelling(tmp_path, monkeypatch):
    real, script, settings, cache = _linked_install(tmp_path.resolve(), spelled=None)
    settings.write_text(
        json.dumps(
            {"statusLine": {"type": "command", "command": managed_command(script, cache, real)}}
        )
    )

    def never(*_a, **_k):
        raise AssertionError("must not be consulted")

    monkeypatch.setattr("relaydeck.usage.hooks.setup.os.lstat", never)
    assert _state(settings, cache, script, real) == "installed"


def _line(command):
    return json.dumps({"statusLine": {"type": "command", "command": command}})


def test_a_sibling_environment_or_the_base_interpreter_is_a_conflict(tmp_path):
    root = tmp_path.resolve()
    base, script, settings, cache = _linked_install(root)
    current = root / "env" / "bin" / "python"
    sibling = root / "sibling" / "bin" / "python"
    for link in (current, sibling):
        link.parent.mkdir(parents=True)
        link.symlink_to(base)  # both environments link to the same base file
    for spelled in (sibling, base):
        settings.write_text(_line(managed_command(script, cache, spelled)))
        assert _state(settings, cache, script, current) == "conflict"
    (root / "env-alias").symlink_to(root / "env", target_is_directory=True)
    settings.write_text(_line(managed_command(script, cache, root / "env-alias/bin/python")))
    assert _state(settings, cache, script, current) == "installed"


def test_only_the_interpreter_may_differ_and_the_quoting_stays_canonical(tmp_path):
    root = tmp_path.resolve()
    real, script, settings, cache = _linked_install(root)
    alias = root / "alias" / "bin" / "python"
    canonical = managed_command(script, cache, alias)
    for command in (
        canonical + " extra",
        canonical + "; true",
        canonical.replace("--cache-path", "--cache"),
        canonical.replace(str(cache), str(cache) + "x"),
        canonical.replace(str(script), str(script) + "x"),
        f"'{alias}' '{script}' --cache-path '{cache}'",  # parses to ours, but not canonical
        "'" + canonical,  # shlex error
        "",
    ):
        settings.write_text(_line(command))
        assert _state(settings, cache, script, real) == "conflict", command
    settings.write_text(
        json.dumps({"statusLine": {"type": "command", "command": canonical, "extra": 1}})
    )
    assert _state(settings, cache, script, real) == "conflict"
    settings.write_text(json.dumps({"statusLine": {"type": "shell", "command": canonical}}))
    assert _state(settings, cache, script, real) == "conflict"


def test_a_linked_spelling_of_a_group_writable_or_missing_entry_is_a_conflict(tmp_path):
    root = tmp_path.resolve()
    real, script, settings, cache = _linked_install(root)
    real.chmod(0o770)
    assert _state(settings, cache, script, real) == "invalid"  # the current one fails first
    real.chmod(0o700)
    (root / "alias").unlink()
    assert _state(settings, cache, script, real) == "conflict"


def test_bound_interpreter_keeps_the_recorded_spelling_only_for_the_same_entry(tmp_path):
    root = tmp_path.resolve()
    real = root / "env" / "bin" / "python"
    real.parent.mkdir(parents=True)
    real.write_text("never executed")
    real.chmod(0o700)
    (root / "alias").symlink_to(root / "env", target_is_directory=True)
    spelled = root / "alias" / "bin" / "python"
    assert bound_interpreter(str(spelled), real) == spelled
    assert bound_interpreter(str(real), real) == real

    hardlink = root / "env" / "bin" / "python3"
    os.link(real, hardlink)  # same entry data, another name
    other_name = root / "alias" / "bin" / "python3"
    assert bound_interpreter(str(other_name), real) == real

    for bad in (
        None,
        42,
        b"/x",
        "relative/python",
        "",
        str(root / "alias" / ".." / "env" / "bin" / "python"),
        str(spelled) + "\0",
        str(root / "missing" / "bin" / "python"),
    ):
        assert bound_interpreter(bad, real) == real, bad

    real.chmod(0o770)
    assert bound_interpreter(str(spelled), real) == real
    real.chmod(0o600)
    assert bound_interpreter(str(spelled), real) == real
