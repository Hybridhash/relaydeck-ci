"""Setup on a synthetic fresh home, end to end with faked Tailscale, Herdr probes, web fetch,
launchd and clock, then the companion's own connect check on what setup wrote."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from relaydeck.companion.app.runtime import CompanionRuntime
from relaydeck.companion.inspection import prereqs, readiness
from relaydeck.connector.enrollment import load_creds, load_keys
from relaydeck.devtools.fakes.connector import FixtureAdapter
from relaydeck.gateway.features.access.hosts import HostStore
from tests.support.client.defaults import (
    _open_hold_released_unless_opted_in as _open_hold_released_unless_opted_in,
)
from tests.support.client.defaults import (
    _watch_first_unless_opted_in as _watch_first_unless_opted_in,
)
from tests.support.setup.prereqs import probes
from tests.support.setup.rig import SetupRig

pytestmark = pytest.mark.usefixtures("hosted_web_bundle")

ANSWERS = {
    "Create these private files": True,
    "Apply this serve": True,
    "Install the web app": True,
    "Start RelayDeck at login": True,
    "Pair a device now": False,
}
HOSTPORT = "mac-a.synthetic-tailnet.example.ts.net:8445"


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SetupRig:
    made = SetupRig(tmp_path, monkeypatch)
    made.answers.rules = dict(ANSWERS)
    return made


def mapped(rig: SetupRig) -> None:
    """After `serve --bg` the real Tailscale reports the mapping; the fake must too."""
    rig.serve.serve = {
        "TCP": {"8445": {"HTTPS": True}},
        "Web": {HOSTPORT: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8125"}}}},
    }


def check_report(rig: SetupRig) -> readiness.Readiness:
    config = rig.config()
    prereq = prereqs.run_prereqs(
        socket=str(config.herdr_socket),
        https_port=config.tailscale_https_port,
        probes=probes(rig.serve),
        expected_target="http://127.0.0.1:8125",
    )
    return readiness.run_check(
        rig.config_path,
        prereq_report=prereq,
        companion_state=lambda _d: "running",
        web_signed=lambda _v: rig.web.is_installed,
        key_file=rig.home / ".config" / "relaydeck" / "connector_key.json",
        login_state=lambda: "done" if rig.launchd.loaded else "install",
        environ={},
    )


def test_a_fresh_home_ends_fully_ready_and_the_enrollment_is_accepted(rig: SetupRig) -> None:
    report = rig.run()
    assert report.exit_code == 0, rig.text
    mapped(rig)
    check = check_report(rig)
    assert check.exit_code == 0, [(i.number, i.state, i.reason) for i in check.items]
    assert check.ready_to_pair
    config = rig.config()
    creds = load_creds(str(config.connector_credentials))
    assert creds is not None
    assert HostStore(path=config.state_dir / "hosts.json").lookup(creds.host_token) is not None

    async def accepted() -> str:
        runtime = CompanionRuntime(
            config,
            connect_herdr=True,
            source_factory=FixtureAdapter,
            durable_signins=False,
        )
        runtime.gateway = SimpleNamespace(hosts=HostStore(path=config.state_dir / "hosts.json"))
        runtime.stop = asyncio.Event()
        await runtime.herdr._connect()
        if runtime.herdr.link_task is not None:
            runtime.herdr.link_task.cancel()
            await asyncio.gather(runtime.herdr.link_task, return_exceptions=True)
        return runtime.herdr.issue

    assert asyncio.run(accepted()) == "Waiting for authenticated connector synchronization."
    assert load_keys(str(rig.home / ".config/relaydeck/connector_key.json")) is not None


def test_a_second_run_writes_nothing_and_asks_nothing(rig: SetupRig) -> None:
    assert rig.run().exit_code == 0, rig.text
    mapped(rig)
    before = rig.snapshot()
    rig.said.clear()
    rig.answers.asked.clear()
    again = rig.run()
    assert again.exit_code == 0, rig.text
    assert rig.snapshot() == before
    assert rig.answers.asked == ["Pair a device now? [y/N] "]  # asked on every run
    assert rig.web.fetched == ["0.3.0"] or len(rig.web.fetched) == 1


def test_nothing_secret_or_home_specific_is_printed_by_a_whole_run(rig: SetupRig) -> None:
    assert rig.run().exit_code == 0
    config = rig.config()
    creds = load_creds(str(config.connector_credentials))
    keys = load_keys(str(rig.home / ".config/relaydeck/connector_key.json"))
    assert creds is not None and keys is not None
    cookie, cursor = config.secrets()
    for value in (creds.host_token, keys.seed_b64, cookie.decode(), cursor.decode()):
        assert value not in rig.text
    assert str(rig.home) not in rig.text and "<plist" not in rig.text


def test_the_whole_run_makes_the_expected_launchd_calls_only(rig: SetupRig) -> None:
    assert rig.run().exit_code == 0
    kinds = [call[1] for call in rig.launchd.calls]
    assert kinds.count("bootstrap") == 1 and "bootout" not in kinds
    assert any(
        call[:2] == ["serve", "--bg"] or call[:3] == ["serve", "--bg", "--https=8445"]
        for call in rig.serve.mutations
    )
