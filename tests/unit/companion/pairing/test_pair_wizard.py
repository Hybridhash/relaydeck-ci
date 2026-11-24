"""`relaydeck pair`: a window, each request shown with an escaped label, and an approval only when
the owner types exactly the code shown on that device."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from relaydeck.companion.config import CompanionConfig, load_config
from relaydeck.companion.pairing.wizard import normalize_code, run_pair
from relaydeck.contracts.pubkeys import fingerprint_of_raw, openssh_to_raw
from relaydeck.gateway.features.access.hosts import HostRecord, HostStore
from relaydeck.gateway.features.access.identity import pkce_challenge
from relaydeck.gateway.features.access.pairing import PairingError, PairingStore
from relaydeck.host.security.enrollment import generate
from tests.support.companion.asgi_companion import config_file
from tests.support.setup.rig import healthy_body

VERIFIER = "synthetic-device-verifier-" + "x" * 43


class Wizard:
    def __init__(self, tmp_path: Path) -> None:
        root = tmp_path.resolve()
        root.chmod(0o700)
        (root / "state").mkdir(mode=0o700)
        self.config: CompanionConfig = load_config(config_file(root))
        _, public = generate()
        self.hosts = HostStore(path=self.config.state_dir / "hosts.json")
        self.hosts.enroll(
            self.config.host_id,
            "mac",
            "owner",
            public,
            fingerprint_of_raw(openssh_to_raw(public)),
        )
        self.record: HostRecord = self.hosts.hosts()[0]
        self.store = PairingStore(self.config.state_dir / "pairing.json")
        self.store.start(self.config.advertised_origin)
        self.t = 5_000.0
        self.said: list[str] = []
        self.asked: list[str] = []
        self.body = healthy_body(self.config)
        self.later: list[Callable[[], object]] = []

    def now_ms(self) -> int:
        return int(self.t * 1000)

    def sleep(self, seconds: float) -> None:
        """Time passes; the next queued device action happens, if any."""
        self.t += seconds
        if self.later:
            self.later.pop(0)()

    def device(self, label: str = "Synthetic phone") -> dict[str, Any]:
        return self.store.request(
            {
                "origin": self.config.advertised_origin,
                "host_id": self.config.host_id,
                "code_challenge": pkce_challenge(VERIFIER),
                "code_challenge_method": "S256",
                "device_label": label,
            },
            self.record,
            self.now_ms(),
        )

    def finish(self, request_id: str) -> None:
        self.store.redeem(
            request_id,
            {
                "origin": self.config.advertised_origin,
                "host_id": self.config.host_id,
                "verifier": VERIFIER,
            },
            self.record,
            self.now_ms(),
        )

    def window(self) -> dict[str, Any] | None:
        return self.store.window_info(self.record, self.config.advertised_origin, self.now_ms())

    def run(self, script: list[Callable[[], str]], *, ttl_s: int = 300, **over: Any) -> int:
        steps = iter(script)

        def prompt(text: str) -> str:
            self.asked.append(text)
            try:
                return next(steps)()
            except StopIteration:
                raise EOFError from None

        options: dict[str, Any] = {
            "prompt": prompt,
            "say": self.said.append,
            "ttl_s": ttl_s,
            "status": lambda _dir: self.body,
            "clock": lambda: self.t,
            "sleep": self.sleep,
        }
        return run_pair(self.config, **(options | over))

    @property
    def text(self) -> str:
        return "\n".join(self.said)

    def states(self) -> list[str]:
        try:
            rows = self.store.pending(self.record, self.config.advertised_origin, self.now_ms())
        except PairingError:
            return []  # no window is open
        return [row["state"] for row in rows]


@pytest.fixture
def wiz(tmp_path: Path) -> Wizard:
    return Wizard(tmp_path)


def test_one_match_approves_then_consumption_ends_the_wizard_and_closes_the_window(
    wiz: Wizard,
) -> None:
    seen: dict[str, Any] = {}

    def appear() -> None:
        seen["req"] = wiz.device()

    def redeem() -> None:
        wiz.finish(seen["req"]["request_id"])

    wiz.later = [appear, lambda: None, redeem]
    assert wiz.run([lambda: seen["req"]["matching_code"]]) == 0
    assert "Device paired" in wiz.text and wiz.window() is None
    assert (
        seen["req"]["matching_code"] not in wiz.text
    )  # the code is for the device, not this screen


@pytest.mark.parametrize("typed", ["", "12", "abc-def", "٣٣٣-٣٣٣", "1234567", "d 12"])
def test_wrong_or_empty_input_approves_nothing(wiz: Wizard, typed: str) -> None:
    wiz.later = [lambda: wiz.device()]
    assert wiz.run([lambda: typed], ttl_s=10) == 130
    assert wiz.states() == []  # the window closed with the wizard; nothing was ever approved
    assert "Device paired" not in wiz.text


def test_a_code_that_matches_no_request_approves_nothing(wiz: Wizard) -> None:
    seen: dict[str, Any] = {}
    wiz.later = [lambda: seen.update(req=wiz.device())]
    other = "000-000" if seen.get("req") is None else "999-999"

    def wrong() -> str:
        real = seen["req"]["matching_code"]
        return "111-111" if real != "111-111" else "222-222"

    assert wiz.run([wrong]) == 130
    assert "No single waiting request matches" in wiz.text and "Device paired" not in wiz.text
    assert other


def test_two_requests_only_the_typed_one_is_approved(wiz: Wizard) -> None:
    seen: dict[str, Any] = {}
    states: list[list[str]] = []

    def appear() -> None:
        seen.update(a=wiz.device("first"), b=wiz.device("second"))

    def redeem() -> None:
        states.append(sorted(wiz.states()))
        wiz.finish(seen["b"]["request_id"])

    wiz.later = [appear, lambda: None, redeem]
    assert wiz.run([lambda: seen["b"]["matching_code"]]) == 0
    assert states == [["approved", "pending"]]
    assert wiz.window() is None


def test_the_code_may_be_typed_with_or_without_the_hyphen() -> None:
    assert normalize_code("123456") == "123-456"
    assert normalize_code("123-456") == "123-456"
    assert normalize_code(" 123 456 ") == "123-456"
    for bad in ("", "12345", "1234567", "12a-456", "١٢٣-٤٥٦"):
        assert normalize_code(bad) is None


def test_d_denies_a_single_request(wiz: Wizard) -> None:
    wiz.later = [lambda: wiz.device("a")]
    assert wiz.run([lambda: "d"], ttl_s=6) == 1
    assert "Request denied." in wiz.text and wiz.window() is None


def test_d_with_a_code_denies_only_that_one_of_several(wiz: Wizard) -> None:
    seen: dict[str, Any] = {}
    wiz.later = [lambda: seen.update(a=wiz.device("a"), b=wiz.device("b"))]
    wiz.run([lambda: f"d {seen['a']['matching_code']}"], ttl_s=20)
    assert "Request denied." in wiz.text


def test_d_alone_with_two_waiting_denies_nothing(wiz: Wizard) -> None:
    wiz.later = [lambda: (wiz.device("a"), wiz.device("b")) and None]
    wiz.run([lambda: "d"], ttl_s=6)
    assert "No single waiting request matches" in wiz.text
    assert "Request denied." not in wiz.text


def test_an_expiry_while_the_owner_is_typing_approves_nothing(wiz: Wizard) -> None:
    seen: dict[str, Any] = {}
    wiz.later = [lambda: seen.update(req=wiz.device())]

    def type_late() -> str:
        wiz.t += 400  # the window (300 s at most) ends before the line is entered
        return str(seen["req"]["matching_code"])

    assert wiz.run([type_late]) == 1
    assert "ended" in wiz.text and wiz.window() is None


def test_ctrl_c_closes_the_window_it_opened(wiz: Wizard) -> None:
    wiz.later = [lambda: wiz.device()]

    def cancel() -> str:
        raise KeyboardInterrupt

    assert wiz.run([cancel]) == 130
    assert wiz.window() is None and "cancelled" in wiz.text


def test_end_of_input_closes_the_window_too(wiz: Wizard) -> None:
    wiz.later = [lambda: wiz.device()]
    assert wiz.run([]) == 130
    assert wiz.window() is None


def test_no_request_before_the_window_ends_reports_it_and_closes(wiz: Wizard) -> None:
    assert wiz.run([], ttl_s=3) == 1
    assert "ended" in wiz.text and wiz.window() is None


def test_an_open_window_is_reported_and_neither_reopened_nor_closed(wiz: Wizard) -> None:
    wiz.store.open_window(wiz.record, wiz.config.advertised_origin, wiz.now_ms(), 120)
    before = wiz.window()
    assert wiz.run([lambda: ""]) == 1
    assert "already open" in wiz.text and wiz.window() == before


def test_a_window_replaced_by_someone_else_is_not_closed_by_this_wizard(wiz: Wizard) -> None:
    wiz.later = [lambda: wiz.device()]

    def replace_window() -> str:
        wiz.t += 1
        wiz.store.open_window(wiz.record, wiz.config.advertised_origin, wiz.now_ms(), 200)
        return "123-456"

    assert wiz.run([replace_window]) == 1
    assert wiz.window() is not None  # theirs is still open


def test_a_wizard_that_loses_the_race_to_open_leaves_the_other_window_and_request(
    wiz: Wizard, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = PairingStore.open_window_if_absent
    requests: list[str] = []

    def other_wizard_opens_first(self: PairingStore, *args: Any) -> str | None:
        real(wiz.store, *args)
        requests.append(wiz.device()["request_id"])
        return real(self, *args)

    monkeypatch.setattr(PairingStore, "open_window_if_absent", other_wizard_opens_first)
    assert wiz.run([], ttl_s=3) == 1
    assert "already open" in wiz.text
    assert wiz.window() is not None
    assert len(wiz.states()) == 1


def test_cleanup_does_not_close_a_window_opened_after_its_ownership_check(
    wiz: Wizard, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = PairingStore.close_window_if_owned

    def replaced_just_before_the_close(self: PairingStore, window_id: str) -> bool:
        wiz.t += 1
        wiz.store.open_window(wiz.record, wiz.config.advertised_origin, wiz.now_ms(), 120)
        wiz.device("other wizard phone")
        return real(self, window_id)

    monkeypatch.setattr(PairingStore, "close_window_if_owned", replaced_just_before_the_close)
    wiz.later = [lambda: wiz.device()]
    assert wiz.run([]) == 130
    assert wiz.window() is not None
    assert wiz.states() == ["pending"]


def test_a_companion_restart_that_resets_the_pairing_state_ends_the_wizard(wiz: Wizard) -> None:
    wiz.later = [lambda: wiz.device()]

    def restart() -> str:
        wiz.store.start(wiz.config.advertised_origin)
        return "111-111"

    assert wiz.run([restart]) == 1
    assert "ended" in wiz.text


def test_it_needs_a_running_ready_companion_and_opens_no_window_otherwise(wiz: Wizard) -> None:
    wiz.body = {"state": "stopped"}
    assert wiz.run([lambda: ""]) == 1
    assert "not running" in wiz.text and wiz.window() is None
    wiz.said.clear()
    wiz.body = healthy_body(wiz.config, connector_ready=False)
    assert wiz.run([lambda: ""]) == 1 and wiz.window() is None


def test_an_unenrolled_mac_gets_the_setup_hint_and_no_window(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    root.chmod(0o700)
    (root / "state").mkdir(mode=0o700)
    config = load_config(config_file(root))
    said: list[str] = []
    code = run_pair(
        config,
        prompt=lambda _t: "",
        say=said.append,
        status=lambda _d: healthy_body(config),
        clock=lambda: 1.0,
        sleep=lambda _s: None,
    )
    assert code == 1 and "relaydeck setup" in "\n".join(said)


def test_labels_are_escaped_and_the_scope_is_shown(wiz: Wizard) -> None:
    wiz.later = [lambda: wiz.device("evil\u202ephone\u200b")]
    wiz.run([], ttl_s=5)
    assert "\u202e" not in wiz.text and "\u200b" not in wiz.text
    assert "\\u202e" in wiz.text and "sessions.read" in wiz.text and "untrusted" in wiz.text


def test_the_ttl_is_bounded(wiz: Wizard) -> None:
    for bad in (0, -1, 301):
        assert wiz.run([lambda: ""], ttl_s=bad) == 1
    assert wiz.window() is None
