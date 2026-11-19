"""Bounded private service streams, including raw writes and active descriptors."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from relaydeck.host.files.descriptors import PrivateFileError
from relaydeck.host.observability.rotation import RotatingOutput

NAME = "companion.out.log"


def retained(directory: Path, name: str = NAME) -> bytes:
    return b"".join(
        p.read_bytes()
        for suffix in (".3", ".2", ".1", "")
        if (p := directory / (name + suffix)).exists()
    )


def test_rotation_and_reopen_keep_exact_bounded_tail(tmp_path: Path) -> None:
    data = bytes(range(256)) * 17
    writer = RotatingOutput(tmp_path, NAME, limit=64)
    try:
        for at in range(0, len(data), 19):
            writer.write(data[at : at + 19])
    finally:
        writer.close()
    assert retained(tmp_path) == data[-256:]
    again = RotatingOutput(tmp_path, NAME, limit=64)
    try:
        again.write(b"last")
    finally:
        again.close()
    assert retained(tmp_path) == data[-192:] + b"last"
    assert all(p.stat().st_size <= 64 and p.stat().st_mode & 0o077 == 0 for p in tmp_path.iterdir())


@pytest.mark.parametrize("suffix", ["", ".1", ".2", ".3", ".lock"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "permissions", "fifo"])
def test_unsafe_inventory_refused_without_modification(
    tmp_path: Path, suffix: str, kind: str
) -> None:
    victim = tmp_path / "victim"
    victim.write_bytes(b"unchanged")
    victim.chmod(0o600)
    target = tmp_path / (NAME + suffix)
    if kind == "symlink":
        target.symlink_to(victim)
    elif kind == "hardlink":
        os.link(victim, target)
    elif kind == "fifo":
        os.mkfifo(target, 0o600)
    else:
        target.write_bytes(b"unsafe")
        target.chmod(0o644)
    with pytest.raises(OSError):
        RotatingOutput(tmp_path, NAME, limit=8)
    assert victim.read_bytes() == b"unchanged"


def test_second_writer_and_replaced_active_file_are_refused(tmp_path: Path) -> None:
    first = RotatingOutput(tmp_path, NAME, limit=8)
    try:
        with pytest.raises(OSError):
            RotatingOutput(tmp_path, NAME, limit=8)
        (tmp_path / NAME).rename(tmp_path / "old")
        (tmp_path / NAME).write_bytes(b"replacement")
        with pytest.raises(OSError):
            first.write(b"no")
        assert (tmp_path / NAME).read_bytes() == b"replacement"
    finally:
        first.close()
        first.close()


def test_startup_bounds_oversized_logs_and_partial_writes(tmp_path: Path) -> None:
    for suffix in ("", ".1", ".2", ".3"):
        path = tmp_path / (NAME + suffix)
        path.write_bytes(b"old" * 100 + b"tailtail")
        path.chmod(0o600)
    writer = RotatingOutput(tmp_path, NAME, limit=8)
    real_write = os.write
    try:
        assert retained(tmp_path) == b"tailtail" * 4
        with patch("os.write", side_effect=lambda fd, data: real_write(fd, data[:2])):
            writer.write(b"newlast!")
        assert retained(tmp_path) == b"tailtail" * 3 + b"newlast!"
        with patch("os.write", return_value=0), pytest.raises(OSError):
            writer.write(b"failed")
    finally:
        writer.close()


def run_capture(tmp_path: Path, child: str) -> subprocess.CompletedProcess[str]:
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from relaydeck.companion.adapters.service_output import supervise_output\n"
        "raise SystemExit(supervise_output([sys.executable, '-c', sys.argv[2]], "
        "Path(sys.argv[1]), limit=128))"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), child],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_raw_output_without_newlines_is_captured_from_first_byte(tmp_path: Path) -> None:
    child = (
        "import os\nfor i in range(100):\n"
        " os.write(1,b'o'*128); os.write(2,b'e'*128)\nraise SystemExit(7)"
    )
    done = run_capture(tmp_path, child)
    assert done.returncode == 7, done.stderr
    assert done.stdout == done.stderr == ""
    assert retained(tmp_path) == b"o" * 512
    assert retained(tmp_path, "companion.err.log") == b"e" * 512


def test_child_exit_does_not_wait_for_inherited_pipe_forever(tmp_path: Path) -> None:
    # A grandchild retaining a pipe must not hold the service launcher open.
    done = run_capture(
        tmp_path,
        "import os,time\nif os.fork()==0:\n time.sleep(2); os._exit(0)\n"
        "os.write(1,b'parent'); os._exit(0)",
    )
    assert done.returncode == 0, done.stderr
    assert retained(tmp_path) == b"parent"


@pytest.mark.parametrize("stubborn", [False, True])
def test_termination_wakes_idle_capture_and_drains_final_bytes(
    tmp_path: Path, stubborn: bool
) -> None:
    # The child reports readiness on a private pipe; no sleep-based signal race.
    ready_read, ready_write = os.pipe()
    child = (
        "import os,signal\n"
        "def stop(sig, frame):\n os.write(1,b'last'); raise SystemExit(0)\n"
        f"signal.signal(signal.SIGTERM,{'signal.SIG_IGN' if stubborn else 'stop'})\n"
        "signal.signal(signal.SIGUSR1,lambda *_:os.write(1,b'dump'))\n"
        "os.write(1,b'ready'); signal.pause()\n"
        "while True: signal.pause()"
    )
    # Tell the test when the capture owner has actually read the child's readiness bytes.
    code = (
        "import os,sys,signal\nfrom pathlib import Path\n"
        "from relaydeck.companion.adapters.service_output import supervise_output\n"
        "import relaydeck.companion.adapters.service_output as output; output.STOP_SECONDS=0.1\n"
        "from relaydeck.host.observability.rotation import RotatingOutput\n"
        "original=RotatingOutput.write\n"
        "def write(self,data):\n original(self,data)\n"
        " if b'ready' in data or b'dump' in data: os.write(int(sys.argv[3]),b'1')\n"
        "RotatingOutput.write=write\n"
        "raise SystemExit(supervise_output([sys.executable,'-c',sys.argv[2]],Path(sys.argv[1])))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), child, str(ready_write)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(ready_write,),
    )
    os.close(ready_write)
    try:
        import select

        assert select.select([ready_read], [], [], 5)[0]
        assert os.read(ready_read, 1) == b"1"
        process.send_signal(signal.SIGUSR1)
        assert select.select([ready_read], [], [], 5)[0]
        assert os.read(ready_read, 1) == b"1"
        process.send_signal(signal.SIGTERM)
        _, error = process.communicate(timeout=5)
        assert process.returncode == (128 + signal.SIGKILL if stubborn else 0), error
        assert retained(tmp_path) == (b"readydump" if stubborn else b"readydumplast")
    finally:
        os.close(ready_read)
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.parametrize("phase", ["write", "rename"])
def test_trim_failure_preserves_original_and_is_recoverable(tmp_path: Path, phase: str) -> None:
    active = tmp_path / NAME
    active.write_bytes(b"old" * 100)
    active.chmod(0o600)
    target = "os.write" if phase == "write" else "os.replace"
    with patch(target, side_effect=OSError("synthetic full disk")), pytest.raises(OSError):
        RotatingOutput(tmp_path, NAME, limit=8)
    assert active.read_bytes() == b"old" * 100
    writer = RotatingOutput(tmp_path, NAME, limit=8)
    try:
        assert active.read_bytes() == (b"old" * 100)[-8:]
    finally:
        writer.close()


def test_service_state_requires_replacing_a_legacy_plist(tmp_path: Path) -> None:
    import plistlib

    from relaydeck.companion.adapters import launchd as service

    service.install(executable=Path("/x/relaydeck"), home=tmp_path, uid=501, yes=True, load=False)
    path = service.agent_path(tmp_path)
    document = plistlib.loads(path.read_bytes())
    document["ProgramArguments"] = service.program_arguments(Path("/x/relaydeck"))
    path.write_bytes(plistlib.dumps(document))
    assert service.agent_state(tmp_path, 501, Path("/x/relaydeck"), None, lambda _: 0) == "replace"


def test_service_bootstrap_hides_import_failures_before_application_load() -> None:
    code = (
        "import builtins,os\nos.environ['RELAYDECK_SERVICE_OUTPUT']='1'\n"
        "original=builtins.__import__\n"
        "def importing(name,*args,**kwargs):\n"
        " if name=='relaydeck.companion.cli': raise OSError('PRIVATE_BOOT_PATH')\n"
        " return original(name,*args,**kwargs)\n"
        "builtins.__import__=importing\nfrom relaydeck.cli import main\nmain(['start'])\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15, check=False
    )
    assert done.returncode != 0
    assert done.stderr == "service_failure category=unexpected\n"
    assert done.stdout == ""
    assert "PRIVATE_BOOT_PATH" not in done.stderr and "Traceback" not in done.stderr


def test_service_bootstrap_hides_handled_config_failures() -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; os.environ['RELAYDECK_SERVICE_OUTPUT']='1'; "
            "from relaydeck.cli import main; raise SystemExit(main())",
            "--config",
            "/synthetic/PRIVATE_CONFIG_MISSING",
            "start",
            "--fixture",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert done.returncode == 2
    assert "companion_failure category=" in done.stdout
    assert "PRIVATE_CONFIG" not in done.stdout + done.stderr


def test_killed_supervisor_does_not_leave_a_running_child(tmp_path: Path) -> None:
    import select
    import time

    ready_read, ready_write = os.pipe()
    marker = tmp_path / "stopped"
    child = (
        "import os,signal,time\nfrom pathlib import Path\n"
        "from relaydeck.companion.adapters.service_output import watch_parent\n"
        f"def stop(sig,frame):\n Path({str(marker)!r}).write_text('stopped'); os._exit(0)\n"
        "assert os.environ['RELAYDECK_SERVICE_OUTPUT']=='1'\n"
        "signal.signal(signal.SIGTERM,stop)\nwatch_parent()\n"
        "os.write(1,b'ready'); signal.pause()"
    )
    code = (
        "import os,sys\nfrom pathlib import Path\n"
        "from relaydeck.companion.adapters.service_output import supervise_output\n"
        "from relaydeck.host.observability.rotation import RotatingOutput\n"
        "original=RotatingOutput.write\n"
        "def write(self,data):\n original(self,data)\n"
        " if b'ready' in data: os.write(int(sys.argv[3]),b'1')\n"
        "RotatingOutput.write=write\n"
        "raise SystemExit(supervise_output([sys.executable,'-c',sys.argv[2]],"
        "Path(sys.argv[1]),watch_parent=True))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path), child, str(ready_write)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        pass_fds=(ready_write,),
    )
    os.close(ready_write)
    try:
        assert select.select([ready_read], [], [], 5)[0]
        assert os.read(ready_read, 1) == b"1"
        process.kill()
        process.communicate(timeout=5)
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.read_text() == "stopped"
    finally:
        os.close(ready_read)
        if process.poll() is None:
            process.kill()
            process.wait()


def test_sink_failure_stops_service_without_logging_the_os_error(tmp_path: Path) -> None:
    code = (
        "import sys\nfrom pathlib import Path\n"
        "import relaydeck.companion.adapters.service_output as output\n"
        "from relaydeck.host.observability.rotation import RotatingOutput\n"
        "def fail(self,data): raise OSError('PRIVATE_DISK_PATH')\n"
        "RotatingOutput.write=fail; output.STOP_SECONDS=0.1\n"
        'child="import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        "os.write(1,b'first'); time.sleep(60)\"\n"
        "raise SystemExit(output.supervise_output([sys.executable,'-c',child],Path(sys.argv[1])))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert done.returncode == 1
    assert done.stdout == done.stderr == ""
    assert retained(tmp_path) == b""


def test_installed_supervisor_does_not_import_application(tmp_path: Path) -> None:
    code = """
import sys
from pathlib import Path
from relaydeck.companion.adapters import service_output
seen = []
def supervise(command, directory, **kwargs):
    seen.append((command, directory, kwargs))
    return 0
service_output.supervise_output = supervise
sys.argv[0] = '/synthetic/bin/relaydeck'
from relaydeck.cli import main
assert main(['--config', '/synthetic/profile', 'service-run', '--log-directory', sys.argv[1]]) == 0
expected = ['/synthetic/bin/relaydeck', '--config', '/synthetic/profile',
            'start', '--connect-herdr']
assert seen == [(expected, Path(sys.argv[1]), {'watch_parent': True})]
assert not any(name == 'flet' or name.startswith('flet.') for name in sys.modules)
assert 'relaydeck.companion.app.runtime' not in sys.modules
assert 'relaydeck.companion.cli' not in sys.modules
"""
    done = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == done.stderr == ""


@pytest.mark.parametrize("phase", ["inventory", "spawn"])
def test_supervisor_failure_has_one_content_free_syslog_line(tmp_path: Path, phase: str) -> None:
    from relaydeck.companion.adapters import service_output

    target = "RotatingOutput" if phase == "inventory" else "subprocess.Popen"
    with (
        patch(
            f"relaydeck.companion.adapters.service_output.{target}",
            side_effect=OSError("PRIVATE_PATH"),
        ),
        patch.object(service_output.syslog, "syslog") as emit,
    ):
        assert (
            service_output.main(
                ["service-run", "--log-directory", str(tmp_path)],
                executable=Path("/synthetic/bin/relaydeck"),
            )
            == 1
        )
    emit.assert_called_once_with(
        service_output.syslog.LOG_ERR, "relaydeck service_output_unavailable category=io"
    )


def test_child_configuration_is_idempotent() -> None:
    code = """
import os,sys
os.environ['RELAYDECK_SERVICE_OUTPUT'] = '1'
from relaydeck.companion.adapters import service_output
seen=[]
service_output.watch_parent=lambda: seen.append('watch')
from relaydeck.cli import main
service_output.configure_child()
assert seen == ['watch']
assert sys.stdout.line_buffering and sys.stderr.line_buffering
assert sys.excepthook is service_output._uncaught
"""
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15, check=False
    )
    assert done.returncode == 0, done.stderr


def test_installer_and_log_supervisor_agree_on_a_shared_log_directory(tmp_path: Path) -> None:
    from relaydeck.companion.adapters import launchd

    logs = launchd.log_dir(tmp_path)
    for folder in (logs.parent.parent, logs.parent, logs):
        folder.mkdir(mode=0o700)
        folder.chmod(0o700)
    logs.chmod(0o755)
    with pytest.raises(PrivateFileError):
        RotatingOutput(logs, NAME)
    outcome = launchd.install(
        executable=Path("/usr/bin/true"), home=tmp_path, uid=os.getuid(), yes=True, load=False
    )
    assert outcome.exit_code == 1
    assert not outcome.changed
    assert not launchd.agent_path(tmp_path).exists()
    assert any("log directory" in line for line in outcome.lines)


def test_installer_accepts_the_private_log_directory_the_supervisor_accepts(tmp_path: Path) -> None:
    from relaydeck.companion.adapters import launchd

    outcome = launchd.install(
        executable=Path("/usr/bin/true"), home=tmp_path, uid=os.getuid(), yes=True, load=False
    )
    assert outcome.exit_code == 0
    logs = launchd.log_dir(tmp_path)
    assert logs.stat().st_mode & 0o077 == 0
    RotatingOutput(logs, NAME).close()


def test_supervisor_stop_grace_covers_the_companion_stop_bound() -> None:
    import inspect

    from relaydeck.companion.adapters import service_output
    from relaydeck.companion.app.lifecycle import request_stop

    bound = inspect.signature(request_stop).parameters["timeout"].default
    launchd_default_exit_timeout = 20.0
    assert bound < service_output.STOP_SECONDS < launchd_default_exit_timeout
    assert service_output.PARENT_GONE_STOP_SECONDS < service_output.STOP_SECONDS
