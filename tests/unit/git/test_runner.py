"""GitRunner: allowlisted argv, scrubbed environment, bounded output, no helper execution."""

from __future__ import annotations

import os
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from relaydeck.git.runner import (
    DIFF_TIMEOUT_S,
    STATUS_TIMEOUT_S,
    GitFailed,
    GitRunner,
    GitUnavailable,
)
from tests.support.git_identity import AUTHOR_EMAIL, AUTHOR_NAME

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode, reason="git"
)


@pytest.mark.parametrize(
    "failure,reason",
    [
        (GitFailed("git_timeout"), "git_timeout"),
        (GitFailed("git_exit"), "git_execution_failed"),
        (FileNotFoundError("private detail"), "git_execution_failed"),
        (subprocess.TimeoutExpired("private detail", 1), "git_timeout"),
    ],
)
def test_version_probe_normalizes_expected_failures(
    monkeypatch: pytest.MonkeyPatch, failure: Exception, reason: str
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(GitRunner, "run", fail)
    with pytest.raises(GitUnavailable, match=f"^{reason}$"):
        GitRunner.create()


@pytest.mark.parametrize("failure", [GitFailed("git_cancelled"), RuntimeError("unexpected")])
def test_version_probe_preserves_cancellation_and_programming_failures(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(GitRunner, "run", fail)
    with pytest.raises(type(failure)) as caught:
        GitRunner.create()
    assert caught.value is failure


def _script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    for args in (
        ("init", "-q", "-b", "main"),
        ("config", "user.email", AUTHOR_EMAIL),
        ("config", "user.name", AUTHOR_NAME),
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "a.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "c"], check=True)
    return repo


def _marker_helper(root: Path) -> tuple[str, Path]:
    marker = root / "ran"
    return _script(root / "helper.sh", f'touch "{marker}"\ncat\n'), marker


def test_create_resolves_trusted_git_meeting_minimum_version() -> None:
    runner = GitRunner.create()
    assert runner.run(None, ("--version",), timeout_s=STATUS_TIMEOUT_S).stdout.startswith(
        b"git version"
    )


@pytest.mark.parametrize(
    "args",
    [(), ("push",), ("fetch",), ("-c", "x=y", "status"), ("status", "a\0b")],
)
def test_rejects_arguments_outside_the_allowlist(args: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        GitRunner.create().run(None, args, timeout_s=1)


def test_rejects_a_shell_string() -> None:
    with pytest.raises(ValueError):
        GitRunner.create().run(None, "status", timeout_s=1)  # type: ignore[arg-type]


def test_environment_is_scrubbed(tmp_path: Path) -> None:
    fake = _script(tmp_path / "git", "env\n")
    hostile = {
        "PATH": "/usr/bin:/bin",
        "GIT_DIR": "/x",
        "DYLD_INSERT_LIBRARIES": "/x",
        "HOME": "/h",
    }
    env = dict(
        line.split("=", 1)
        for line in GitRunner(fake, environ=hostile)
        .run(None, ("status",), timeout_s=5)
        .stdout.decode()
        .splitlines()
    )
    assert "GIT_DIR" not in env and "DYLD_INSERT_LIBRARIES" not in env
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert (
        env["GIT_NO_LAZY_FETCH"] == "1"
        and env["GIT_OPTIONAL_LOCKS"] == "0"
        and env["LC_ALL"] == "C"
    )


def test_output_is_capped_while_reading(tmp_path: Path) -> None:
    fake = _script(tmp_path / "git", "yes 0123456789\n")
    result = GitRunner(fake).run(None, ("status",), timeout_s=5, max_bytes=100_000)
    assert result.truncated and len(result.stdout) == 100_000


def test_timeout_kills_the_whole_process_group(tmp_path: Path) -> None:
    pidfile = tmp_path / "child"
    fake = _script(tmp_path / "git", f'sleep 30 &\necho $! > "{pidfile}"\nwait\n')
    started = time.monotonic()
    with pytest.raises(GitFailed, match="git_timeout"):
        GitRunner(fake).run(None, ("status",), timeout_s=0.5)
    assert time.monotonic() - started < 5
    child = int(pidfile.read_text())
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_cancel_stops_a_running_command(tmp_path: Path) -> None:
    fake = _script(tmp_path / "git", "sleep 30\n")
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(GitFailed, match="git_cancelled"):
        GitRunner(fake).run(None, ("status",), timeout_s=10, cancel=cancel)


def test_nonzero_exit_is_a_fixed_code(tmp_path: Path) -> None:
    fake = _script(tmp_path / "git", "echo secret >&2\nexit 3\n")
    with pytest.raises(GitFailed) as err:
        GitRunner(fake).run(None, ("status",), timeout_s=5)
    assert str(err.value) == "git_exit"


def test_repo_fsmonitor_and_external_diff_never_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    helper, marker = _marker_helper(tmp_path)
    subprocess.run(["git", "-C", str(repo), "config", "core.fsmonitor", helper], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "diff.external", helper], check=True)
    (repo / "a.txt").write_text("two\n")
    runner = GitRunner.create()
    runner.run(str(repo), ("status", "--porcelain=v2", "-z"), timeout_s=STATUS_TIMEOUT_S)
    out = runner.run(
        str(repo),
        ("diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", "a.txt"),
        timeout_s=DIFF_TIMEOUT_S,
    )
    assert b"+two" in out.stdout
    assert not marker.exists()


def test_textconv_and_hooks_never_run(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    helper, marker = _marker_helper(tmp_path)
    (repo / ".gitattributes").write_text("*.txt diff=conv\n")
    subprocess.run(["git", "-C", str(repo), "config", "diff.conv.textconv", helper], check=True)
    hooks = repo / ".git" / "hooks"
    for name in ("post-index-change", "reference-transaction"):
        _script(hooks / name, f'touch "{marker}"\n')
    (repo / "a.txt").write_text("two\n")
    runner = GitRunner.create()
    runner.run(str(repo), ("status", "--porcelain=v2", "-z"), timeout_s=STATUS_TIMEOUT_S)
    runner.run(
        str(repo), ("diff", "--no-ext-diff", "--no-textconv", "HEAD"), timeout_s=DIFF_TIMEOUT_S
    )
    assert not marker.exists()


def test_reads_do_not_write_the_repository(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("two\n")
    os.utime(repo / "a.txt", (1, 1))
    index = repo / ".git" / "index"
    before = (index.read_bytes(), index.stat().st_mtime_ns, sorted(os.listdir(repo / ".git")))
    runner = GitRunner.create()
    runner.run(
        str(repo),
        ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
        timeout_s=STATUS_TIMEOUT_S,
    )
    runner.run(str(repo), ("diff", "--numstat", "-z", "HEAD"), timeout_s=DIFF_TIMEOUT_S)
    assert (
        index.read_bytes(),
        index.stat().st_mtime_ns,
        sorted(os.listdir(repo / ".git")),
    ) == before


def test_pathspec_magic_names_are_literal(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("b\n")
    subprocess.run(["git", "-C", str(repo), "add", "b.txt"], check=True)
    (repo / "a.txt").write_text("two\n")
    (repo / "b.txt").write_text("bb\n")
    out = GitRunner.create().run(
        str(repo),
        ("diff", "--name-only", "-z", "HEAD", "--", ":(glob)*.txt"),
        timeout_s=DIFF_TIMEOUT_S,
    )
    assert out.stdout == b""
