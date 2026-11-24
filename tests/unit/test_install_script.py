"""The bootstrap installer, run end to end with fake tools and a fake home.

The real curl/uv/installers never run: the template's test-only override
points every tool at scripts this file writes, and the terminal is a file.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "install.sh.in"
VERSION = "1.2.3"
WHEEL_NAME = f"relaydeck-{VERSION}-py3-none-any.whl"
WHEEL_BYTES = b"synthetic wheel bytes"
WHEEL_SHA = hashlib.sha256(WHEEL_BYTES).hexdigest()


def render(sha: str = WHEEL_SHA) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    for token, value in (
        ("@VERSION@", VERSION),
        ("@WHEEL@", WHEEL_NAME),
        ("@WHEEL_SHA256@", sha),
        ("@UV_VERSION@", "9.9.9"),
    ):
        text = text.replace(token, value)
        assert token not in text
    return text


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    for parent in path.parents:  # the directories this created default to group-writable here
        if not parent.is_dir() or not os.access(parent, os.W_OK):
            break
        mode = stat.S_IMODE(parent.stat().st_mode)
        if not mode & 0o020:
            break
        parent.chmod(0o755)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


@dataclass
class Rig:
    home: Path
    tools: Path
    log: Path
    tty: Path
    tool_bin: Path
    script: Path
    env: dict[str, str] = field(default_factory=dict)

    def run(self, *args: str, text: str | None = None) -> subprocess.CompletedProcess[str]:
        target = self.script
        if text is not None:
            target = target.with_name("install-truncated.sh")
            target.write_text(text, encoding="utf-8")
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin",
            "RELAYDECK_INSTALL_TEST": "1",
            "RELAYDECK_INSTALL_TOOLS_DIR": str(self.tools),
            "RELAYDECK_INSTALL_TTY": str(self.tty),
            **self.env,
        }
        return subprocess.run(
            ["/bin/sh", str(target), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    @property
    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    @property
    def relaydeck_args(self) -> list[str]:
        marker = self.home / "relaydeck-args.txt"
        return marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []


def _fake_uv(rig: Rig, dest: Path) -> Path:
    return _script(
        dest,
        f"""printf 'uv %s\\n' "$*" >>"{rig.log}"
if [ "$1" = "--version" ]; then
  printf 'uv 9.9.9\\n'
  exit 0
fi
if [ "$1" = "tool" ] && [ "$2" = "dir" ]; then
  printf '%s\\n' "{rig.tool_bin}"
  exit 0
fi
if [ "$1" = "tool" ] && [ "$2" = "install" ]; then
  /bin/mkdir -p "{rig.tool_bin}"
  printf '#!/bin/sh\\nprintf "%%s\\\\n" "$@" >"{rig.home}/relaydeck-args.txt"\\n' \\
    >"{rig.tool_bin}/relaydeck"
  /bin/chmod +x "{rig.tool_bin}/relaydeck"
  exit 0
fi
exit 9
""",
    )


def _rig(tmp_path: Path, *, sha: str = WHEEL_SHA, plant_uv: bool = True) -> Rig:
    # The installer's ancestry rule refuses executables under a group- or
    # world-writable directory, so the rig's own chain must not be one: a
    # basetemp created group-writable by the surrounding run would make every
    # happy path refuse for a reason nothing to do with the product.
    for owned in (tmp_path, tmp_path.parent):
        try:
            if stat.S_IMODE(owned.stat().st_mode) & 0o022:
                owned.chmod(0o755)
        except OSError:
            pass
    home = tmp_path / "home"
    home.mkdir(mode=0o755, parents=True)
    tools = tmp_path / "tools"
    rig = Rig(
        home=home,
        tools=tools,
        log=tmp_path / "calls.log",
        tty=tmp_path / "tty",
        tool_bin=home / "toolbin",
        script=tmp_path / "install.sh",
    )
    rig.tty.write_text("", encoding="utf-8")
    wheel = tmp_path / "wheel.bin"
    wheel.write_bytes(WHEEL_BYTES)
    uv_source = tmp_path / "fake-uv"
    _fake_uv(rig, uv_source)
    installer = tmp_path / "uv-installer.sh"
    _script(
        installer,
        f'/bin/mkdir -p "$HOME/.local/bin"\n/bin/cp "{uv_source}" "$HOME/.local/bin/uv"\n',
    )
    _script(
        tools / "curl",
        f"""printf 'curl %s\\n' "$*" >>"{rig.log}"
out=""
url=""
while [ $# -gt 0 ]; do
  case $1 in
    -o) out=$2; shift 2 ;;
    *) url=$1; shift ;;
  esac
done
case $url in
  *astral.sh*) /bin/cp "{installer}" "$out" ;;
  *.whl) /bin/cp "{wheel}" "$out" ;;
  *) echo "unexpected url" >&2; exit 3 ;;
esac
""",
    )
    if plant_uv:
        _fake_uv(rig, home / ".local/bin" / "uv")
    rig.script.write_text(render(sha), encoding="utf-8")
    return rig


def test_the_happy_path_installs_and_hands_over_to_setup(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    result = rig.run("--web-archive", "a file.tar.gz", "--web-signature", "sig file")
    assert result.returncode == 0, result.stderr
    assert "uv tool install" in rig.calls and WHEEL_NAME in rig.calls
    assert "--force" in rig.calls  # an existing relaydeck tool is replaced
    assert rig.relaydeck_args == [
        "setup",
        "--web-archive",
        "a file.tar.gz",
        "--web-signature",
        "sig file",
    ]
    tty_text = rig.tty.read_text(encoding="utf-8")
    assert "open a new terminal window" in tty_text
    assert str(rig.tool_bin) not in result.stdout + result.stderr


def test_missing_uv_is_installed_by_its_installer(tmp_path: Path) -> None:
    rig = _rig(tmp_path, plant_uv=False)
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert "astral.sh/uv/9.9.9/install.sh" in rig.calls
    assert rig.relaydeck_args == ["setup"]


def test_a_hash_mismatch_refuses_before_uv_install(tmp_path: Path) -> None:
    rig = _rig(tmp_path, sha="0" * 64)
    result = rig.run()
    assert result.returncode != 0
    assert "tool install" not in rig.calls
    assert rig.relaydeck_args == []


def test_no_terminal_refuses_before_any_change(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.env["RELAYDECK_INSTALL_TTY"] = str(tmp_path / "no" / "tty")
    result = rig.run()
    assert result.returncode != 0
    assert rig.calls == ""


def test_root_is_refused(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _script(rig.tools / "id", "printf '0\\n'\n")
    result = rig.run()
    assert result.returncode != 0
    assert rig.calls == ""


def test_an_unsafe_existing_uv_is_refused_not_replaced(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    (rig.home / ".local" / "bin").chmod(0o777)
    result = rig.run()
    assert result.returncode != 0
    assert rig.calls == ""  # neither the unsafe uv nor curl ever ran


def test_a_truncated_script_does_nothing(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    text = render()
    rig.run(text=text[: len(text) // 2])
    assert rig.calls == ""
    assert rig.relaydeck_args == []
    assert not (rig.tool_bin / "relaydeck").exists()


def test_unsafe_optional_brew_candidate_is_skipped(tmp_path: Path) -> None:
    rig = _rig(tmp_path, plant_uv=False)
    _fake_uv(rig, rig.tools / "brew" / "uv")
    (rig.tools / "brew").chmod(0o775)
    result = rig.run()
    assert result.returncode == 0, (result.stderr, rig.calls)
    assert "astral.sh" in rig.calls  # the pinned uv was installed instead


def test_absent_uv_under_unsafe_destination_is_refused_before_download(tmp_path: Path) -> None:
    rig = _rig(tmp_path, plant_uv=False)
    dest = rig.home / ".local" / "bin"
    dest.mkdir(parents=True)
    dest.chmod(0o777)
    result = rig.run()
    assert result.returncode != 0
    assert rig.calls == "", rig.calls


def test_uv_destination_checks_unsafe_original_ancestor_before_following_safe_link(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path, plant_uv=False)
    target = rig.home / "trusted-bin"
    target.mkdir(mode=0o755)
    local = rig.home / ".local"
    local.mkdir()
    (local / "bin").symlink_to(target, target_is_directory=True)
    local.chmod(0o777)
    result = rig.run()
    assert result.returncode != 0 and rig.calls == "", (result.returncode, rig.calls)


def test_uv_destination_validates_intermediate_link_target_ancestry(tmp_path: Path) -> None:
    # .local/bin -> unsafe-middle/redirect -> safe-final: the middle hop's
    # parent is replaceable, so the destination is refused even though the
    # first and last directories are safe.
    rig = _rig(tmp_path, plant_uv=False)
    final = rig.home / "safe-final"
    final.mkdir(mode=0o755)
    unsafe = rig.home / "unsafe-middle"
    unsafe.mkdir(mode=0o755)
    intermediate = unsafe / "redirect"
    intermediate.symlink_to(final, target_is_directory=True)
    intermediate.lchmod(0o755)  # normal non-writable macOS link; isolate ancestor validation
    local = rig.home / ".local"
    local.mkdir(mode=0o755)
    (local / "bin").symlink_to(intermediate, target_is_directory=True)
    (local / "bin").lchmod(0o755)
    unsafe.chmod(0o777)
    result = rig.run()
    assert result.returncode != 0 and rig.calls == "", (
        result.returncode,
        rig.calls,
        oct((local / "bin").lstat().st_mode),
        oct(intermediate.lstat().st_mode),
    )


def test_safe_symlinked_relaydeck_launcher_still_hands_over(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    uv = rig.home / ".local" / "bin" / "uv"
    body = uv.read_text()
    line = f'  /bin/chmod +x "{rig.tool_bin}/relaydeck"'
    assert line in body
    body = body.replace(
        line,
        line + f'\n  /bin/mv "{rig.tool_bin}/relaydeck" "{rig.tool_bin}/relaydeck-real"'
        f'\n  /bin/ln -s relaydeck-real "{rig.tool_bin}/relaydeck"',
    )
    uv.write_text(body)
    result = rig.run()
    assert result.returncode == 0, (result.stderr, (rig.tool_bin / "relaydeck").lstat().st_mode)
    assert rig.relaydeck_args == ["setup"]


@pytest.mark.parametrize("tool", ["shasum", "uv-dir", "mktemp"])
def test_machine_output_command_stderr_is_not_sent_to_redirected_logs(
    tmp_path: Path, tool: str
) -> None:
    rig = _rig(tmp_path)
    if tool == "shasum":
        _script(
            rig.tools / "shasum",
            f"printf 'PRIVATE-DIAGNOSTIC /synthetic/private-path\\n' >&2\n"
            f"printf '{WHEEL_SHA}  wheel\\n'\n",
        )
    elif tool == "mktemp":
        _script(
            rig.tools / "mktemp",
            "printf 'PRIVATE-DIAGNOSTIC /synthetic/private-path\\n' >&2\n"
            'exec /usr/bin/mktemp "$@"\n',
        )
    else:
        uv = rig.home / ".local" / "bin" / "uv"
        body = uv.read_text().replace(
            'if [ "$1" = "tool" ] && [ "$2" = "dir" ]; then',
            'if [ "$1" = "tool" ] && [ "$2" = "dir" ]; then\n'
            '  printf "PRIVATE-DIAGNOSTIC /synthetic/private-path\\n" >&2',
        )
        uv.write_text(body)
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert "PRIVATE-DIAGNOSTIC" not in result.stderr, result.stderr
    assert "PRIVATE-DIAGNOSTIC" not in result.stdout


def test_absent_uv_under_a_symlinked_unsafe_destination_is_refused(tmp_path: Path) -> None:
    rig = _rig(tmp_path, plant_uv=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    elsewhere.chmod(0o777)
    (rig.home / ".local").mkdir()
    (rig.home / ".local" / "bin").symlink_to(elsewhere, target_is_directory=True)
    result = rig.run()
    assert result.returncode != 0
    assert rig.calls == "", rig.calls


def _fake_uv_with_setup(rig: Rig, *, noisy: bool = False, version: str = "9.9.9") -> None:
    _script(
        rig.home / ".local" / "bin" / "uv",
        f"""printf 'uv %s\\n' "$*" >>"{rig.log}"
if [ "$1" = "--version" ]; then
  printf 'uv {version}\\n'
  exit 0
fi
if [ "$1 $2" = "tool dir" ]; then
  printf '%s\\n' '{rig.tool_bin}'
  exit 0
fi
if [ "$1 $2" = "tool install" ]; then
  {"printf 'PRIVATE-INSTALLER-MARKER /synthetic/private-path\\n' >&2" if noisy else ":"}
  /bin/mkdir -p '{rig.tool_bin}'
  /bin/cat >'{rig.tool_bin}/relaydeck' <<'END'
#!/bin/sh
printf '%s\\n' "$PATH" >'{rig.home}/setup-path.txt'
END
  /bin/chmod 755 '{rig.tool_bin}/relaydeck'
  exit 0
fi
exit 9
""",
    )


def test_bootstrap_hands_a_usable_herdr_path_to_setup(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _fake_uv_with_setup(rig)
    result = rig.run()
    assert result.returncode == 0, result.stderr
    path = (rig.home / "setup-path.txt").read_text().strip().split(":")
    assert str(rig.home / ".local" / "bin") in path, path
    assert path[-4:] == ["/usr/bin", "/bin", "/usr/sbin", "/sbin"], path


def test_bootstrap_does_not_send_raw_installer_output_to_redirected_stderr(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    _fake_uv_with_setup(rig, noisy=True)
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert "PRIVATE-INSTALLER-MARKER" not in result.stderr, result.stderr
    assert "PRIVATE-INSTALLER-MARKER" not in result.stdout
    assert "PRIVATE-INSTALLER-MARKER" in rig.tty.read_text(encoding="utf-8")


def test_an_uv_older_than_the_minimum_is_skipped_for_the_pinned_one(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _fake_uv_with_setup(rig, version="0.7.9")
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert "astral.sh" in rig.calls  # the pinned uv was installed instead
    assert rig.relaydeck_args == ["setup"]


def test_the_readme_recipe_never_runs_after_a_failed_download(tmp_path: Path) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = readme.split("```sh\n")
    recipe = next(
        block.split("```", 1)[0]
        for block in blocks
        if "releases/download/" in block and "install.sh" in block
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    curl = tools / "curl"
    curl.write_text("#!/bin/sh\nexit 22\n", encoding="utf-8")
    curl.chmod(0o700)
    (tmp_path / "install.sh").write_text("printf executed > stale-executed\n", encoding="utf-8")
    subprocess.run(
        ["/bin/sh", "-c", recipe],
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": f"{tools}:/usr/bin:/bin"},
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert not (tmp_path / "stale-executed").exists(), (
        "failed curl still executes the old install.sh"
    )


def test_a_local_wheel_skips_the_download_and_is_still_hash_checked(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    local = tmp_path / "local.whl"
    local.write_bytes(WHEEL_BYTES)
    rig.env["RELAYDECK_WHEEL"] = str(local)
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert ".whl" not in rig.calls.split("uv tool install")[0]
    bad = _rig(tmp_path / "second")
    bad.env["RELAYDECK_WHEEL"] = str(local)
    bad.env["RELAYDECK_WHEEL_SHA256"] = "0" * 64
    refused = bad.run()
    assert refused.returncode != 0
    assert "tool install" not in bad.calls


def test_release_texts_agree_on_the_version_and_install_line() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    line = (
        'd=$(mktemp -d) && curl -fsSL -o "$d/install.sh" '
        f"https://github.com/Hybridhash/relaydeck/releases/download/v{version}/install.sh"
        ' && /bin/sh "$d/install.sh"'
    )
    assert line in (ROOT / "README.md").read_text(encoding="utf-8")
    plugin = (ROOT / "plugin/bin/relaydeck-plugin.sh").read_text(encoding="utf-8")
    assert f'TAG="v{version}"' in plugin
    assert "mktemp -d" in plugin and "releases/download/$TAG/install.sh" in plugin
    template = TEMPLATE.read_text(encoding="utf-8")
    for placeholder in ("@VERSION@", "@WHEEL@", "@WHEEL_SHA256@", "@UV_VERSION@"):
        assert placeholder in template
    mode = stat.S_IMODE((ROOT / "scripts/release_web_bundle.sh").stat().st_mode)
    assert mode & 0o111
