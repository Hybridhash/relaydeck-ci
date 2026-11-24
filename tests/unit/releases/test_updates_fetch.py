"""Fetching the record: the redirect chain, the signature located from the validated version, the
release moving between requests, disallowed hops and the 20 second deadline. No network."""

from __future__ import annotations

from datetime import date

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from relaydeck.releases import records as release_records
from relaydeck.releases import web as webrelease
from tests.support.updates.signing import (
    COMMIT,
    Http,
    Response,
    doc,
    public_blob,
    raw_of,
    sign,
    signed,
)

PRIVATE = Ed25519PrivateKey.generate()
KEY = public_blob(PRIVATE)
TODAY = date(2026, 12, 2)
SIGNATURE_URL = f"{webrelease.RELEASE_BASE}/v0.3.1/latest.json.minisig"
VERSIONED = f"{webrelease.RELEASE_BASE}/v0.3.1/latest.json"
CDN = "https://release-assets.githubusercontent.com/asset/latest.json?token=secret"
SIGNATURE_CDN = "https://release-assets.githubusercontent.com/asset/latest.json.minisig?t=2"


def routes() -> tuple[Http, bytes]:
    raw, signature = signed(PRIVATE)
    http = Http(
        {
            release_records.LATEST_URL: Response(302, headers={"location": VERSIONED}),
            VERSIONED: Response(302, headers={"location": CDN}),
            CDN: Response(200, raw),
            SIGNATURE_URL: Response(302, headers={"location": SIGNATURE_CDN}),
            SIGNATURE_CDN: Response(200, signature),
        }
    )
    return http, raw


def fetch(http: Http, clock: object = None) -> release_records.Record:
    return release_records.fetch_record(
        http=http, clock=clock or (lambda: 0.0), key=KEY, today=TODAY
    )  # type: ignore[arg-type]


def outcome_of(http: Http, clock: object = None) -> str:
    with pytest.raises(release_records.UpdateError) as caught:
        fetch(http, clock)
    return caught.value.outcome


def test_the_three_hop_chain_succeeds_and_the_signature_url_comes_from_the_version() -> None:
    http, _raw = routes()
    record = fetch(http)
    assert record.version == "0.3.1" and record.commit == COMMIT
    assert http.calls == [
        release_records.LATEST_URL,
        VERSIONED,
        CDN,
        SIGNATURE_URL,
        SIGNATURE_CDN,
    ]


def test_a_version_that_is_not_a_plain_triple_never_reaches_a_url() -> None:
    raw = raw_of(doc(version="0.3.1/../../x"))
    http = Http({release_records.LATEST_URL: Response(200, raw)})
    assert outcome_of(http) == "record"
    assert http.calls == [release_records.LATEST_URL]


def test_a_signature_for_another_record_is_a_signature_outcome() -> None:
    http, _raw = routes()
    other_raw, other_signature = signed(PRIVATE, doc(notes=["Different notes."]))
    http.routes[SIGNATURE_CDN] = Response(200, other_signature)
    assert other_raw != _raw
    assert outcome_of(http) == "signature"


def test_a_release_that_moves_between_the_requests_fails_verification() -> None:
    http, _raw = routes()
    # latest.json was read while 0.3.1 was latest; by the signature request the file at the
    # versioned address holds the signature of a newer record
    _newer_raw, newer_signature = signed(PRIVATE, doc("0.3.2", commit="a" * 40))
    http.routes[SIGNATURE_CDN] = Response(200, newer_signature)
    assert outcome_of(http) == "signature"


def test_a_signature_whose_comment_names_another_version_is_refused() -> None:
    http, raw = routes()
    http.routes[SIGNATURE_CDN] = Response(
        200, sign(PRIVATE, raw, "relaydeck-latest 0.3.2 " + COMMIT)
    )
    assert outcome_of(http) == "signature"


def test_a_redirect_outside_the_allowed_hosts_is_a_network_outcome() -> None:
    http, _raw = routes()
    http.routes[VERSIONED] = Response(302, headers={"location": "https://evil.example/latest.json"})
    assert outcome_of(http) == "network"
    http.routes[VERSIONED] = Response(302, headers={"location": "http://github.com/latest.json"})
    assert outcome_of(http) == "network"


def test_http_failures_are_network_outcomes() -> None:
    http, _raw = routes()
    http.routes[CDN] = Response(503)
    assert outcome_of(http) == "network"


def test_an_oversize_record_or_signature_is_refused_as_it_streams() -> None:
    http, _raw = routes()
    http.routes[CDN] = Response(200, b"x" * (release_records.MAX_RECORD_BYTES + 1))
    assert outcome_of(http) == "record"
    http, _raw = routes()
    http.routes[SIGNATURE_CDN] = Response(200, b"x" * (webrelease.MAX_SIGNATURE_BYTES + 1))
    assert outcome_of(http) == "signature"


def test_both_downloads_share_a_twenty_second_deadline() -> None:
    http, _raw = routes()
    times = iter([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 15.0, 15.0, 25.0])
    assert outcome_of(http, lambda: next(times, 25.0)) == "network"
    assert release_records.DEADLINE_S == 20.0


def test_the_bundle_deadline_is_unchanged() -> None:
    assert webrelease.TOTAL_DEADLINE_S == 120.0
    download = webrelease._Download(Http({}), lambda: 0.0)
    assert download._deadline == 120.0
