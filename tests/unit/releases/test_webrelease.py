"""Signed web release fetch: download policy, verification, extraction, install.

The HTTP getter and the signing key are injected fakes built in the test; no
network and no real keys are touched.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import socket
import ssl
import tarfile
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import httpcore
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from relaydeck.releases import bundles as webbundle
from relaydeck.releases import web as webrelease
from tests.support.companion.web_assets import csp_record

GOOD_JS = (
    '"touchend";' * 4
    + '"touchcancel";' * 2
    + '"abort";' * 3
    + '"flet_reconnect_merge_rd1";'
    + '"flet_reconnect_wake_rd1";'
    + '"item_extents";'
    + "var app = 1;"
)
VERSION = "1.2.3"
BASE = f"relaydeck-web-{VERSION}"
ARCHIVE_NAME = f"{BASE}.tar.gz"
URL = f"{webrelease.RELEASE_BASE}/v{VERSION}/{ARCHIVE_NAME}"
KEY_ID = b"\x01\x02\x03\x04\x05\x06\x07\x08"


def _public_blob(private: Ed25519PrivateKey) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(b"Ed" + KEY_ID + raw)


def _sign(private: Ed25519PrivateKey, data: bytes, comment: str = ARCHIVE_NAME) -> bytes:
    digest = hashlib.blake2b(data, digest_size=64).digest()
    signature = private.sign(digest)
    global_sig = private.sign(signature + comment.encode())
    return b"\n".join(
        [
            b"untrusted comment: signature from minisign secret key",
            base64.b64encode(b"ED" + KEY_ID + signature),
            f"trusted comment: {comment}".encode(),
            base64.b64encode(global_sig),
            b"",
        ]
    )


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def _archive(
    *,
    js: str = GOOD_JS,
    manifest_version: str | None = VERSION,
    entries: list[tuple[str, bytes]] | None = None,
) -> bytes:
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            files = (
                entries
                if entries is not None
                else [
                    (f"{BASE}/main.dart.js", js.encode()),
                    (f"{BASE}/index.html", b"<html>app</html>"),
                    (f"{BASE}/assets/font.bin", b"\x00\x01"),
                ]
            )
            for name, data in files:
                _add(tar, name, data)
            if manifest_version is not None:
                manifest = {
                    "schema": "relaydeck.build-manifest/1",
                    "version": manifest_version,
                    "app": {"revision": "b" * 40},
                    "web": {"csp": csp_record(dict(files).get(f"{BASE}/index.html", b""))},
                }
                _add(tar, f"{BASE}.build-manifest.json", json.dumps(manifest).encode())
    return raw.getvalue()


class _Response:
    def __init__(self, status_code: int, body: bytes = b"", headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.headers: Mapping[str, str] = headers or {}
        self._body = body

    def chunks(self) -> Iterable[bytes]:
        yield self._body

    def close(self) -> None:
        pass


class _Http:
    def __init__(self, routes: dict[str, _Response]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, *, timeout: float) -> _Response:
        self.calls.append(url)
        return self.routes[url]


def _setup(
    tmp_path: Path,
    archive: bytes | None = None,
    *,
    comment: str = ARCHIVE_NAME,
) -> tuple[_Http, bytes, Ed25519PrivateKey, bytes]:
    private = Ed25519PrivateKey.generate()
    data = archive if archive is not None else _archive()
    routes = {
        URL: _Response(200, data),
        f"{URL}.minisig": _Response(200, _sign(private, data, comment)),
    }
    return _Http(routes), _public_blob(private), private, data


def test_a_signed_release_downloads_verifies_and_installs(tmp_path: Path) -> None:
    store = tmp_path / "store"
    http, key, _private, data = _setup(tmp_path)
    # One allowed redirect hop, as GitHub serves release assets.
    final = "https://objects.githubusercontent.com/asset?sig=secret"
    http.routes[final] = http.routes.pop(URL)
    http.routes[URL] = _Response(302, headers={"location": final})
    result = webrelease.fetch_release(VERSION, store=store, http=http, key=key)
    assert result.version == VERSION
    assert result.archive_sha256 == hashlib.sha256(data).hexdigest()
    assert result.path == store / "signed" / result.archive_sha256
    assert (result.path / "index.html").is_file()
    installed = json.loads((result.path / "installed.json").read_text())
    assert installed["kind"] == "signed" and installed["source"] == URL
    assert webrelease.installed_signed(VERSION, store=store) == result
    assert not [p for p in store.iterdir() if p.name.startswith(".download")]


@pytest.mark.parametrize("version", ["1.2", "v1.2.3", "../1.2.3", "1.2.3-rc1", ""])
def test_a_bad_version_never_reaches_a_url(tmp_path: Path, version: str) -> None:
    http, key, _private, _data = _setup(tmp_path)
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(version, store=tmp_path / "store", http=http, key=key)
    assert caught.value.reason == "version" and http.calls == []


def test_a_wrong_trusted_comment_is_refused_and_the_previous_bundle_stays(
    tmp_path: Path,
) -> None:
    store = tmp_path / "store"
    http, key, _private, _data = _setup(tmp_path)
    good = webrelease.fetch_release(VERSION, store=store, http=http, key=key)
    bad_http, bad_key, _p, _d = _setup(tmp_path, comment="something-else.tar.gz")
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(VERSION, store=store, http=bad_http, key=bad_key)
    assert caught.value.reason == "signature"
    assert webrelease.installed_signed(VERSION, store=store) == good


def test_a_one_bit_change_is_refused(tmp_path: Path) -> None:
    http, key, _private, data = _setup(tmp_path)
    tampered = bytearray(data)
    tampered[len(tampered) // 2] ^= 1
    http.routes[URL] = _Response(200, bytes(tampered))
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(VERSION, store=tmp_path / "store", http=http, key=key)
    assert caught.value.reason == "signature"
    assert webrelease.installed_signed(VERSION, store=tmp_path / "store") is None


def test_redirect_policy_and_http_failures_are_network_refusals(tmp_path: Path) -> None:
    http, key, _private, _data = _setup(tmp_path)
    http.routes[URL] = _Response(302, headers={"location": "https://example.com/x"})
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(VERSION, store=tmp_path / "s1", http=http, key=key)
    assert caught.value.reason == "network"

    http, key, _p, _d = _setup(tmp_path)
    http.routes[URL] = _Response(302, headers={"location": URL})
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(VERSION, store=tmp_path / "s2", http=http, key=key)
    assert caught.value.reason == "network"

    http, key, _p, _d = _setup(tmp_path)
    http.routes[URL] = _Response(404)
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(VERSION, store=tmp_path / "s3", http=http, key=key)
    assert caught.value.reason == "network"


def test_an_oversize_archive_and_signature_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, key, _private, _data = _setup(tmp_path)
    monkeypatch.setattr(webbundle, "MAX_BYTES", 16)
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(VERSION, store=tmp_path / "store", http=http, key=key)
    assert caught.value.reason == "too_large"

    monkeypatch.undo()
    http, key, _p, _d = _setup(tmp_path)
    http.routes[f"{URL}.minisig"] = _Response(200, b"x" * (webrelease.MAX_SIGNATURE_BYTES + 1))
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(VERSION, store=tmp_path / "store2", http=http, key=key)
    assert caught.value.reason == "too_large"


def _fetch_signed_custom(tmp_path: Path, archive: bytes, store_name: str = "store") -> None:
    private = Ed25519PrivateKey.generate()
    http = _Http(
        {URL: _Response(200, archive), f"{URL}.minisig": _Response(200, _sign(private, archive))}
    )
    webrelease.fetch_release(
        VERSION, store=tmp_path / store_name, http=http, key=_public_blob(private)
    )


def test_archive_escapes_duplicates_and_links_are_refused(tmp_path: Path) -> None:
    escapes = _archive(entries=[("../escape.txt", b"x")], manifest_version=VERSION)
    with pytest.raises(webrelease.ReleaseError) as caught:
        _fetch_signed_custom(tmp_path, escapes, "s1")
    assert caught.value.reason == "archive"

    duplicates = _archive(
        entries=[(f"{BASE}/main.dart.js", GOOD_JS.encode()), (f"{BASE}/main.dart.js", b"again")]
    )
    with pytest.raises(webrelease.ReleaseError) as caught:
        _fetch_signed_custom(tmp_path, duplicates, "s2")
    assert caught.value.reason == "archive"

    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            info = tarfile.TarInfo(f"{BASE}/link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    with pytest.raises(webrelease.ReleaseError) as caught:
        _fetch_signed_custom(tmp_path, raw.getvalue(), "s3")
    assert caught.value.reason == "archive"


def test_a_wrong_manifest_version_and_missing_markers_are_refused(tmp_path: Path) -> None:
    with pytest.raises(webrelease.ReleaseError) as caught:
        _fetch_signed_custom(tmp_path, _archive(manifest_version="9.9.9"), "s1")
    assert caught.value.reason == "version"
    with pytest.raises(webrelease.ReleaseError) as caught:
        _fetch_signed_custom(tmp_path, _archive(js="var app = 1;"), "s2")
    assert caught.value.reason == "install"


def test_the_total_deadline_stops_a_slow_download(tmp_path: Path) -> None:
    http, key, _private, _data = _setup(tmp_path)
    times = iter([1000.0, 1000.0, 5000.0])
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.fetch_release(
            VERSION,
            store=tmp_path / "store",
            http=http,
            key=key,
            clock=lambda: next(times, 5000.0),
        )
    assert caught.value.reason == "network"


def test_the_offline_form_installs_with_the_same_checks(tmp_path: Path) -> None:
    private = Ed25519PrivateKey.generate()
    data = _archive()
    archive = tmp_path / ARCHIVE_NAME
    archive.write_bytes(data)
    signature = tmp_path / f"{ARCHIVE_NAME}.minisig"
    signature.write_bytes(_sign(private, data))
    result = webrelease.install_archive(
        archive, signature, VERSION, store=tmp_path / "store", key=_public_blob(private)
    )
    assert result.path.is_dir()
    assert webrelease.installed_signed(VERSION, store=tmp_path / "store") == result


def _offline(tmp_path: Path, data: bytes) -> tuple[Path, Path, bytes, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    archive = tmp_path / ARCHIVE_NAME
    archive.write_bytes(data)
    signature = tmp_path / f"{ARCHIVE_NAME}.minisig"
    signature.write_bytes(_sign(private, data))
    return archive, signature, _public_blob(private), private


def _install(tmp_path: Path, archive: Path, signature: Path, key: bytes) -> webrelease.SignedBundle:
    return webrelease.install_archive(
        archive, signature, VERSION, store=tmp_path / "store", key=key
    )


def test_a_source_replaced_after_staging_installs_the_signed_bytes_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = _archive()
    altered = _archive(js=GOOD_JS + "\nUNSIGNED_PAYLOAD = true;")
    archive, signature, key, _private = _offline(tmp_path, good)
    real = webrelease._stage_archive

    def replace_the_pathname(source: Path, staged: Path) -> tuple[str, bytes]:
        result = real(source, staged)
        other = tmp_path / "replacement.tar.gz"
        other.write_bytes(altered)
        other.replace(source)
        return result

    monkeypatch.setattr(webrelease, "_stage_archive", replace_the_pathname)
    installed = _install(tmp_path, archive, signature, key)
    assert "UNSIGNED_PAYLOAD" not in (installed.path / "main.dart.js").read_text()
    assert installed.archive_sha256 == hashlib.sha256(good).hexdigest()
    assert webbundle.verify_signed(installed.path)


def test_a_source_changed_in_place_after_staging_installs_the_signed_bytes_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = _archive()
    altered = _archive(js=GOOD_JS + "\nUNSIGNED_PAYLOAD = true;")
    archive, signature, key, _private = _offline(tmp_path, good)
    real = webrelease._stage_archive

    def write_into_the_same_file(source: Path, staged: Path) -> tuple[str, bytes]:
        result = real(source, staged)
        with source.open("r+b") as handle:
            handle.write(altered)
            handle.truncate()
        return result

    monkeypatch.setattr(webrelease, "_stage_archive", write_into_the_same_file)
    installed = _install(tmp_path, archive, signature, key)
    assert "UNSIGNED_PAYLOAD" not in (installed.path / "main.dart.js").read_text()


def test_the_offline_form_refuses_links_pipes_and_oversize_signatures(tmp_path: Path) -> None:
    archive, signature, key, _private = _offline(tmp_path, _archive())
    linked = tmp_path / "linked.tar.gz"
    linked.symlink_to(archive)
    with pytest.raises(webrelease.ReleaseError) as caught:
        _install(tmp_path, linked, signature, key)
    assert caught.value.reason == "archive"
    pipe = tmp_path / "pipe.tar.gz"
    os.mkfifo(pipe)
    with pytest.raises(webrelease.ReleaseError) as caught:
        _install(tmp_path, pipe, signature, key)
    assert caught.value.reason == "archive"
    signature_link = tmp_path / "link.minisig"
    signature_link.symlink_to(signature)
    with pytest.raises(webrelease.ReleaseError) as caught:
        _install(tmp_path, archive, signature_link, key)
    assert caught.value.reason == "signature"
    signature_pipe = tmp_path / "pipe.minisig"
    os.mkfifo(signature_pipe)
    with pytest.raises(webrelease.ReleaseError) as caught:
        _install(tmp_path, archive, signature_pipe, key)
    assert caught.value.reason == "signature"
    signature.write_bytes(b"x" * (webrelease.MAX_SIGNATURE_BYTES + 1))
    with pytest.raises(webrelease.ReleaseError) as caught:
        _install(tmp_path, archive, signature, key)
    assert caught.value.reason == "too_large"
    assert not (tmp_path / "store").exists() or not list((tmp_path / "store").glob(".download-*"))


def test_a_fetched_archive_is_extracted_from_the_copy_that_was_hashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, key, _private, data = _setup(tmp_path)
    seen: list[tuple[str, bytes]] = []
    real = webrelease._extract

    def spy(archive: Path, into: Path) -> None:
        seen.append((archive.parent.name, archive.read_bytes()))
        real(archive, into)

    monkeypatch.setattr(webrelease, "_extract", spy)
    result = webrelease.fetch_release(VERSION, store=tmp_path / "store", http=http, key=key)
    assert result.archive_sha256 == hashlib.sha256(data).hexdigest()
    assert [(name.startswith(".download-"), body == data) for name, body in seen] == [(True, True)]


# -- the download deadline and encoding ----------------------------------------------------------


class _Pieces(httpx.SyncByteStream):
    """One byte per network read; each read takes `step` simulated seconds."""

    def __init__(self, clock: list[float], step: float, count: int) -> None:
        self.clock, self.step, self.count = clock, step, count
        self.reads = 0

    def __iter__(self) -> Iterable[bytes]:  # type: ignore[override]
        for _ in range(self.count):
            self.clock[0] += self.step
            self.reads += 1
            yield b"x"


class _PiecesGetter:
    def __init__(self, stream: _Pieces, headers: dict[str, str] | None = None) -> None:
        self.stream = stream
        self.headers = headers or {}
        self.timeouts: list[float] = []

    def get(self, url: str, *, timeout: float) -> Any:
        self.timeouts.append(timeout)
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        response = httpx.Response(200, headers=self.headers, stream=self.stream)
        return webrelease._HttpxResponse(client, response)


def test_the_deadline_is_checked_on_every_network_read_not_every_megabyte() -> None:
    clock = [0.0]
    stream = _Pieces(clock, 59.0, 10)  # each read arrives just inside a read timeout
    download = webrelease._Download(_PiecesGetter(stream), lambda: clock[0])
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"
    assert stream.reads <= 3
    assert clock[0] < 2 * webrelease.TOTAL_DEADLINE_S


def test_each_attempt_is_given_the_time_left_not_a_fresh_allowance() -> None:
    clock = [0.0]
    getter = _PiecesGetter(_Pieces(clock, 1.0, 3))
    download = webrelease._Download(getter, lambda: clock[0])
    clock[0] = 100.0
    download.fetch_to("https://github.com/example", lambda chunk: None)
    assert getter.timeouts == [pytest.approx(webrelease.TOTAL_DEADLINE_S - 100.0)]


def test_a_content_coded_download_is_refused() -> None:
    clock = [0.0]
    getter = _PiecesGetter(_Pieces(clock, 0.0, 1), {"content-encoding": "gzip"})
    download = webrelease._Download(getter, lambda: clock[0])
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"


class _Wire(httpcore.NetworkStream):
    """A connection whose reads each take simulated time and time out when given less."""

    def __init__(self, clock: list[float], pieces: list[tuple[float, bytes]]) -> None:
        self.clock = clock
        self.pieces = pieces
        self.written: list[bytes] = []
        self.timeouts: list[float | None] = []
        self.closed = False

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        self.timeouts.append(timeout)
        if not self.pieces:
            return b""
        cost, data = self.pieces[0]
        if timeout is not None and cost > timeout:
            self.clock[0] += timeout
            raise httpcore.ReadTimeout("slow")
        self.pieces.pop(0)
        self.clock[0] += cost
        return data

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self.written.append(buffer)

    def close(self) -> None:
        self.closed = True

    def start_tls(
        self, ssl_context: Any, server_hostname: str | None = None, timeout: float | None = None
    ) -> httpcore.NetworkStream:
        return self

    def get_extra_info(self, info: str) -> Any:
        return None


class _Backend(httpcore.NetworkBackend):
    def __init__(self, *wires: _Wire) -> None:
        self.wires = list(wires)
        self.connects: list[tuple[str, int, float | None]] = []

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        self.connects.append((host, port, timeout))
        return self.wires.pop(0)


HEAD = b"HTTP/1.1 200 OK\r\n"
FRAGMENT = b"X-Piece: x\r\n"


def _download(clock: list[float], *wires: _Wire) -> tuple[webrelease._Download, _Backend]:
    backend = _Backend(*wires)
    getter = webrelease._HttpxGetter(
        clock=lambda: clock[0],
        backend=backend,
        ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
    )
    return webrelease._Download(getter, lambda: clock[0]), backend


def test_the_production_getter_asks_for_the_host_with_no_content_coding() -> None:
    clock = [0.0]
    wire = _Wire(clock, [(0.1, HEAD + b"Content-Length: 2\r\n\r\nok")])
    download, backend = _download(clock, wire)
    got: list[bytes] = []
    download.fetch_to("https://github.com/example/file?x=1", got.append)
    sent = b"".join(wire.written).lower()
    assert sent.startswith(b"get /example/file?x=1 http/1.1\r\n")
    assert b"accept-encoding: identity" in sent and b"host: github.com" in sent
    assert b"".join(got) == b"ok"
    assert backend.connects[0][:2] == ("github.com", 443)
    assert wire.closed


def test_slow_response_headers_cannot_outlast_the_total_deadline() -> None:
    clock = [0.0]
    pieces = [(19.0, HEAD)] + [(19.0, FRAGMENT)] * 9 + [(19.0, b"Content-Length: 0\r\n\r\n")]
    wire = _Wire(clock, pieces)
    download, _backend = _download(clock, wire)
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"
    assert clock[0] <= webrelease.TOTAL_DEADLINE_S
    assert wire.closed
    assert all(t is not None and t <= webrelease.READ_TIMEOUT_S for t in wire.timeouts)


def test_a_body_that_trickles_past_the_deadline_is_cut_off_and_closed() -> None:
    clock = [0.0]
    pieces = [(1.0, HEAD + b"Content-Length: 50\r\n\r\n")] + [(19.0, b"x")] * 40
    wire = _Wire(clock, pieces)
    download, _backend = _download(clock, wire)
    taken: list[bytes] = []
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", taken.append)
    assert caught.value.reason == "network"
    assert clock[0] <= webrelease.TOTAL_DEADLINE_S
    assert 1 <= len(taken) <= 7 and wire.closed


def test_a_reply_that_finishes_exactly_at_the_deadline_is_not_accepted() -> None:
    clock = [0.0]
    wire = _Wire(clock, [(1.0, HEAD + b"Content-Length: 0\r\n\r\n")])
    download, _backend = _download(clock, wire)
    clock[0] = webrelease.TOTAL_DEADLINE_S - 1.0
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"


def test_a_redirect_shares_the_one_deadline() -> None:
    clock = [0.0]
    hop = HEAD.replace(b"200 OK", b"302 Found") + (
        b"Location: https://objects.githubusercontent.com/a\r\nContent-Length: 0\r\n\r\n"
    )
    first = _Wire(clock, [(18.0, hop[:20]), (18.0, hop[20:60]), (18.0, hop[60:])])
    second = _Wire(clock, [(19.0, HEAD)] + [(19.0, FRAGMENT)] * 20)
    download, backend = _download(clock, first, second)
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"
    assert clock[0] <= webrelease.TOTAL_DEADLINE_S
    assert [host for host, _port, _t in backend.connects] == [
        "github.com",
        "objects.githubusercontent.com",
    ]
    assert first.closed and second.closed


class _Sock:
    """A socket whose connect costs simulated time; once connected it serves one reply."""

    def __init__(self, clock: list[float], cost: float, error: type[OSError] | None) -> None:
        self.clock, self.cost, self.error = clock, cost, error
        self.timeout: float | None = None
        self.connect_timeout: float | None = None
        self.sent: list[bytes] = []
        self.reply = HEAD + b"Content-Length: 2\r\n\r\nok"
        self.closed = False

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def connect(self, address: Any) -> None:
        self.connect_timeout = self.timeout
        if self.error is socket.timeout:
            self.clock[0] += self.timeout or 0.0
        else:
            self.clock[0] += self.cost
        if self.error is not None:
            raise self.error("synthetic")

    def send(self, data: Any) -> int:
        self.sent.append(bytes(data))
        return len(data)

    def recv(self, size: int) -> bytes:
        data, self.reply = self.reply, b""
        return data

    def close(self) -> None:
        self.closed = True


class _Tls:
    """The handshake succeeds and keeps the socket it was given."""

    def set_alpn_protocols(self, protocols: list[str]) -> None:
        pass

    def wrap_socket(self, sock: Any, server_hostname: str | None = None) -> Any:
        return sock


ADDRESSES = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 443)),
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.3", 443)),
]


def _by_hand(
    clock: list[float],
    socks: list[_Sock],
    resolver: Any = None,
) -> webrelease._Download:
    pending = list(socks)
    getter = webrelease._HttpxGetter(
        clock=lambda: clock[0],
        ssl_context=cast("ssl.SSLContext", _Tls()),
        resolver=resolver or (lambda host, port: ADDRESSES[: len(socks)]),
        make_socket=lambda *args: cast("socket.socket", pending.pop(0)),
    )
    return webrelease._Download(getter, lambda: clock[0])


def test_failing_addresses_near_expiry_share_the_one_deadline() -> None:
    clock = [0.0]
    socks = [_Sock(clock, 0, socket.timeout), _Sock(clock, 0, socket.timeout)]
    download = _by_hand(clock, socks)
    clock[0] = 110.0  # ten seconds left after earlier work
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"
    assert clock[0] <= webrelease.TOTAL_DEADLINE_S
    assert [sock.connect_timeout for sock in socks[:1]] == [10.0]
    assert all(sock.closed for sock in socks[:1])


def test_each_address_attempt_gets_the_time_left_when_it_starts() -> None:
    clock = [0.0]
    socks = [_Sock(clock, 8.0, ConnectionRefusedError), _Sock(clock, 0, None)]
    download = _by_hand(clock, socks)
    clock[0] = 100.0
    got: list[bytes] = []
    download.fetch_to("https://github.com/example", got.append)
    assert socks[0].connect_timeout == 20.0  # the read-timeout cap, 20 s being left
    assert socks[1].connect_timeout == 12.0  # 120 - 108
    assert socks[0].closed and b"".join(got) == b"ok"
    assert b"GET /example HTTP/1.1" in b"".join(socks[1].sent)


def test_a_resolver_that_never_answers_is_abandoned_at_the_deadline() -> None:
    clock = [0.0]
    release = threading.Event()
    download = _by_hand(
        clock, [_Sock(clock, 0, None)], resolver=lambda host, port: release.wait(30) and []
    )
    clock[0] = webrelease.TOTAL_DEADLINE_S - 0.05
    try:
        with pytest.raises(webrelease.ReleaseError) as caught:
            download.fetch_to("https://github.com/example", lambda chunk: None)
    finally:
        release.set()
    assert caught.value.reason == "network"


def test_a_failed_lookup_is_a_network_refusal() -> None:
    clock = [0.0]

    def refuse(host: str, port: int) -> list[Any]:
        raise socket.gaierror("synthetic")

    download = _by_hand(clock, [_Sock(clock, 0, None)], resolver=refuse)
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"


class _SlowSender:
    """Accepts `step` bytes per send and takes `cost` simulated seconds each time."""

    def __init__(self, clock: list[float], cost: float, step: int) -> None:
        self.clock, self.cost, self.step = clock, cost, step
        self.timeouts: list[float | None] = []
        self.sent = b""

    def settimeout(self, timeout: float | None) -> None:
        self.timeouts.append(timeout)

    def send(self, data: Any) -> int:
        wait = self.timeouts[-1]
        if wait is not None and wait < self.cost:
            self.clock[0] += wait
            raise TimeoutError("synthetic")
        self.clock[0] += self.cost
        taken = bytes(data[: self.step])
        self.sent += taken
        return len(taken)


def _writer(clock: list[float], sock: Any, start: float) -> webrelease._DeadlineStream:
    clock[0] = start

    def left() -> float:
        return webrelease.TOTAL_DEADLINE_S - clock[0]

    return webrelease._DeadlineStream(
        webrelease._SocketStream(cast("socket.socket", sock), left), left
    )


def test_a_write_in_several_sends_succeeds_inside_the_deadline_with_shrinking_waits() -> None:
    clock = [0.0]
    sock = _SlowSender(clock, 1.0, 2)
    _writer(clock, sock, 100.0).write(b"abcdefg", timeout=20)
    assert sock.sent == b"abcdefg"
    assert sock.timeouts == [20.0, 19.0, 18.0, 17.0]  # the cap first, then the time left


def test_a_write_in_several_sends_cannot_outlast_the_deadline() -> None:
    clock = [0.0]
    sock = _SlowSender(clock, 9.0, 1)
    stream = _writer(clock, sock, 110.0)
    with pytest.raises(httpcore.TimeoutException):
        stream.write(b"ab", timeout=20)
    assert clock[0] <= webrelease.TOTAL_DEADLINE_S
    assert sock.timeouts == [10.0, 1.0] and sock.sent == b"a"


def test_a_send_that_accepts_nothing_is_an_error_not_a_spin() -> None:
    clock = [0.0]
    sock = _SlowSender(clock, 0.0, 0)
    with pytest.raises(httpcore.WriteError):
        _writer(clock, sock, 0.0).write(b"abc", timeout=20)


def test_a_connect_that_completes_at_the_deadline_closes_its_socket() -> None:
    clock = [0.0]
    sock = _Sock(clock, 0, None)
    sock.connect = lambda address: clock.__setitem__(0, clock[0] + (sock.timeout or 0.0))  # type: ignore[method-assign]
    getter = webrelease._HttpxGetter(
        clock=lambda: clock[0],
        ssl_context=cast("ssl.SSLContext", _Tls()),
        resolver=lambda host, port: ADDRESSES[:1],
        make_socket=lambda *args: cast("socket.socket", sock),
    )
    download = webrelease._Download(getter, lambda: clock[0])
    clock[0] = 110.0
    with pytest.raises(webrelease.ReleaseError) as caught:
        download.fetch_to("https://github.com/example", lambda chunk: None)
    assert caught.value.reason == "network"
    assert clock[0] == webrelease.TOTAL_DEADLINE_S
    assert sock.closed


def test_a_failed_or_cancelled_upgrade_closes_the_stream_it_owns() -> None:
    class Inner(httpcore.NetworkStream):
        def __init__(self, error: BaseException | None) -> None:
            self.error, self.closed = error, False

        def start_tls(
            self, ssl_context: Any, server_hostname: str | None = None, timeout: float | None = None
        ) -> httpcore.NetworkStream:
            if self.error is not None:
                raise self.error
            return Inner(None)

        def close(self) -> None:
            self.closed = True

    for error in (httpcore.ConnectError("synthetic"), KeyboardInterrupt()):
        inner = Inner(error)
        stream = webrelease._DeadlineStream(inner, lambda: 50.0)
        with pytest.raises(type(error)):
            stream.start_tls(cast("ssl.SSLContext", _Tls()), "github.com", timeout=5)
        assert inner.closed
    expired = Inner(None)
    with pytest.raises(httpcore.TimeoutException):
        webrelease._DeadlineStream(expired, lambda: 0.0).start_tls(
            cast("ssl.SSLContext", _Tls()), "github.com", timeout=5
        )
    assert expired.closed
    kept = Inner(None)
    secured = webrelease._DeadlineStream(kept, lambda: 50.0).start_tls(
        cast("ssl.SSLContext", _Tls()), "github.com", timeout=5
    )
    assert not kept.closed and secured is not None


# -- the decompression budget covers tar metadata ------------------------------------------------


def _signed_files(tmp_path: Path, data: bytes) -> tuple[Path, Path, bytes]:
    archive, signature, key, _private = _offline(tmp_path, data)
    return archive, signature, key


def _metadata_archive(*, pax: int = 0, long_name: int = 0) -> bytes:
    raw = io.BytesIO()
    kwargs: dict[str, Any] = (
        {"format": tarfile.PAX_FORMAT, "pax_headers": {"comment": "x" * pax}}
        if pax
        else {"format": tarfile.GNU_FORMAT}
    )
    with tarfile.open(fileobj=raw, mode="w:gz", **kwargs) as tar:
        _add(tar, f"{BASE}/main.dart.js", GOOD_JS.encode())
        _add(tar, f"{BASE}/index.html", b"<html>app</html>")
        if long_name:
            _add(tar, f"{BASE}/" + "n" * long_name, b"x")
        manifest = json.dumps({"version": VERSION}).encode()
        _add(tar, f"{BASE}.build-manifest.json", manifest)
    return raw.getvalue()


@pytest.mark.parametrize("shape", [{"pax": 1 << 20}, {"long_name": 1 << 20}])
def test_oversized_tar_metadata_is_refused_before_it_is_read_into_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: dict[str, int]
) -> None:
    data = _metadata_archive(**shape)
    assert len(data) < 8192  # small on the wire, large once inflated
    archive, signature, key = _signed_files(tmp_path, data)
    monkeypatch.setattr(webbundle, "MAX_BYTES", 4096)
    with pytest.raises(webrelease.ReleaseError) as caught:
        webrelease.install_archive(archive, signature, VERSION, store=tmp_path / "store", key=key)
    assert caught.value.reason == "too_large"
    assert not list((tmp_path / "store").glob(".download-*"))


def test_an_ordinary_archive_fits_a_tight_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, signature, key = _signed_files(tmp_path, _archive())
    monkeypatch.setattr(webbundle, "MAX_BYTES", 1024)
    installed = webrelease.install_archive(
        archive, signature, VERSION, store=tmp_path / "store", key=key
    )
    assert installed.path.is_dir()


def test_a_truncated_or_corrupt_stream_is_an_archive_refusal(tmp_path: Path) -> None:
    for bad in (_archive()[:40], b"\x1f\x8b" + b"\x00" * 40, b"not gzip at all"):
        archive, signature, key = _signed_files(tmp_path, bad)
        with pytest.raises(webrelease.ReleaseError) as caught:
            webrelease.install_archive(
                archive, signature, VERSION, store=tmp_path / "store", key=key
            )
        assert caught.value.reason == "archive"
