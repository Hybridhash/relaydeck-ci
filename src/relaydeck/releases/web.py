"""Download and install signed web bundles only after signature, manifest and repair-marker
verification. Use allowlisted HTTPS downloads and private staging that is removed on failure.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
import tarfile
import threading
import time
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from relaydeck.releases import bundles as webbundle
from relaydeck.releases import minisign as minisign

RELEASE_BASE = "https://github.com/Hybridhash/relaydeck/releases/download"
ALLOWED_HOSTS = frozenset(
    {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
)
MAX_REDIRECTS = 5
TOTAL_DEADLINE_S = 120.0
READ_TIMEOUT_S = 20.0
MAX_SIGNATURE_BYTES = 4096
# Tar headers, padding and read-ahead the decompressed stream may carry beyond file contents.
TAR_SLACK_BYTES = 20 << 10
TAR_MEMBER_BYTES = 8 << 10
_COPY_CHUNK = 1 << 20
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


class ReleaseError(Exception):
    """A release fetch was refused; `reason` is one fixed code safe to show."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SignedBundle:
    version: str
    path: Path  # installed directory
    archive_sha256: str


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def chunks(self) -> Iterable[bytes]: ...

    def close(self) -> None: ...


class HttpGetter(Protocol):
    def get(self, url: str, *, timeout: float) -> HttpResponse: ...


class _HttpxResponse:
    def __init__(self, client: httpx.Client, response: httpx.Response) -> None:
        self._client = client
        self._response = response
        self.status_code = response.status_code
        self.headers: Mapping[str, str] = response.headers

    def chunks(self) -> Iterable[bytes]:
        # Raw pieces as the network delivers them, so every read reaches the deadline check.
        yield from self._response.iter_raw()

    def close(self) -> None:
        self._response.close()
        self._client.close()


class _Expired(httpcore.TimeoutException):
    """The download's absolute deadline passed."""


def _within(timeout: float | None, left: Callable[[], float]) -> float:
    """The wait a network operation may take: its own timeout, never more than the time left."""
    remaining = left()
    if remaining <= 0:
        raise _Expired("deadline")
    return remaining if timeout is None else min(timeout, remaining)


class _DeadlineStream(httpcore.NetworkStream):
    """Every read, write and handshake on a connection is capped by the time left."""

    def __init__(self, inner: httpcore.NetworkStream, left: Callable[[], float]) -> None:
        self._inner = inner
        self._left = left

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, timeout=_within(timeout, self._left))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, timeout=_within(timeout, self._left))

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        # This stream owns the connected socket until the handshake succeeds; httpcore has not
        # attached it to a connection yet, so nothing else would close it.
        try:
            secured = self._inner.start_tls(
                ssl_context, server_hostname, timeout=_within(timeout, self._left)
            )
        except BaseException:
            self._inner.close()
            raise
        return _DeadlineStream(secured, self._left)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _SocketStream(httpcore.NetworkStream):
    """A connected socket as an httpcore stream; each operation sets its own timeout. With
    `left`, every send inside a write is capped by the time left when it starts."""

    def __init__(self, sock: socket.socket, left: Callable[[], float] | None = None) -> None:
        self._sock = sock
        self._left = left

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            self._sock.settimeout(timeout)
            return self._sock.recv(max_bytes)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout(str(exc)) from exc
        except OSError as exc:
            raise httpcore.ReadError(str(exc)) from exc

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        view = memoryview(buffer)
        try:
            while view:
                wait = timeout if self._left is None else _within(timeout, self._left)
                self._sock.settimeout(wait)
                sent = self._sock.send(view)
                if sent <= 0:
                    raise httpcore.WriteError("the connection accepted no data")
                view = view[sent:]
        except TimeoutError as exc:
            raise httpcore.WriteTimeout(str(exc)) from exc
        except OSError as exc:
            raise httpcore.WriteError(str(exc)) from exc

    def close(self) -> None:
        self._sock.close()

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        try:
            self._sock.settimeout(timeout)
            return _SocketStream(
                ssl_context.wrap_socket(self._sock, server_hostname=server_hostname), self._left
            )
        except TimeoutError as exc:
            self.close()
            raise httpcore.ConnectTimeout(str(exc)) from exc
        except OSError as exc:
            self.close()
            raise httpcore.ConnectError(str(exc)) from exc

    def get_extra_info(self, info: str) -> Any:
        return self._sock if info == "socket" else None


def _system_resolver(host: str, port: int) -> list[Any]:
    return socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)


def _system_socket(family: int, kind: int, proto: int) -> socket.socket:
    return socket.socket(family, kind, proto)


class _TcpBackend(httpcore.NetworkBackend):
    """Connects by hand so the deadline covers name resolution and every address tried: the
    lookup runs in a daemon thread that is abandoned when the time is up, and each attempt waits
    at most the time that is left when it starts."""

    def __init__(
        self,
        left: Callable[[], float],
        resolver: Callable[[str, int], list[Any]],
        make_socket: Callable[[int, int, int], socket.socket],
    ) -> None:
        self._left = left
        self._resolver = resolver
        self._make_socket = make_socket

    def _resolve(self, host: str, port: int, wait: float) -> list[Any]:
        outcome: list[Any] = []
        done = threading.Event()

        def lookup() -> None:
            try:
                outcome.append(self._resolver(host, port))
            except BaseException as exc:  # noqa: BLE001 - handed to the waiting thread
                outcome.append(exc)
            finally:
                done.set()

        threading.Thread(target=lookup, daemon=True).start()
        if not done.wait(wait):
            raise _Expired("resolve")
        if isinstance(outcome[0], BaseException):
            raise httpcore.ConnectError("name resolution failed")
        found: list[Any] = outcome[0]
        return found

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        addresses = self._resolve(host, port, _within(timeout, self._left))
        failure: Exception = httpcore.ConnectError("no address")
        for family, kind, proto, _name, address in addresses:
            wait = _within(timeout, self._left)
            sock = self._make_socket(family, kind, proto)
            try:
                if local_address is not None:
                    sock.bind((local_address, 0))
                for option in socket_options or ():
                    sock.setsockopt(*option)
                sock.settimeout(wait)
                sock.connect(address)
            except OSError as exc:
                sock.close()
                failure = (
                    httpcore.ConnectTimeout(str(exc))
                    if isinstance(exc, socket.timeout)
                    else httpcore.ConnectError(str(exc))
                )
                continue
            except BaseException:
                sock.close()
                raise
            return _SocketStream(sock, self._left)
        raise failure

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        raise _Expired("unix sockets are not used")


class _DeadlineBackend(httpcore.NetworkBackend):
    def __init__(self, inner: httpcore.NetworkBackend, left: Callable[[], float]) -> None:
        self._inner = inner
        self._left = left

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        stream = self._inner.connect_tcp(
            host,
            port,
            timeout=_within(timeout, self._left),
            local_address=local_address,
            socket_options=socket_options,
        )
        return _DeadlineStream(stream, self._left)

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        raise _Expired("unix sockets are not used")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(min(seconds, _within(None, self._left)))


class _Body(httpx.SyncByteStream):
    def __init__(self, inner: Iterable[bytes]) -> None:
        self._inner = inner

    def __iter__(self) -> Iterator[bytes]:
        yield from self._inner

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


class _PoolTransport(httpx.BaseTransport):
    """The smallest httpx transport over an httpcore pool: no proxies, no retries, no HTTP/2."""

    def __init__(self, pool: httpcore.ConnectionPool) -> None:
        self._pool = pool

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.SyncByteStream):
            raise TypeError("request body")
        sent = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        reply = self._pool.handle_request(sent)
        if not isinstance(reply.stream, Iterable):
            raise TypeError("response body")
        return httpx.Response(
            reply.status,
            headers=reply.headers,
            stream=_Body(reply.stream),
            extensions=reply.extensions,
        )

    def close(self) -> None:
        self._pool.close()


class _HttpxGetter:
    """One streamed GET per call: no environment proxies, no auto redirects, no content
    coding. `timeout` is the time left in the whole download, and it bounds the connection,
    the handshake, the response headers and every body read: each network operation waits at
    most the read timeout and never past that deadline."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        backend: httpcore.NetworkBackend | None = None,
        ssl_context: ssl.SSLContext | None = None,
        resolver: Callable[[str, int], list[Any]] = _system_resolver,
        make_socket: Callable[[int, int, int], socket.socket] = _system_socket,
    ) -> None:
        self._clock = clock
        self._backend = backend
        self._ssl_context = ssl_context
        self._resolver = resolver
        self._make_socket = make_socket

    def get(self, url: str, *, timeout: float) -> HttpResponse:
        deadline = self._clock() + timeout

        def left() -> float:
            return deadline - self._clock()

        pool = httpcore.ConnectionPool(
            ssl_context=self._ssl_context or httpx.create_ssl_context(trust_env=False),
            max_connections=1,
            http1=True,
            http2=False,
            retries=0,
            network_backend=_DeadlineBackend(
                self._backend or _TcpBackend(left, self._resolver, self._make_socket), left
            ),
        )
        client = httpx.Client(
            transport=_PoolTransport(pool),
            trust_env=False,
            follow_redirects=False,
            timeout=min(READ_TIMEOUT_S, timeout),
        )
        try:
            request = client.build_request("GET", url, headers={"Accept-Encoding": "identity"})
            response = client.send(request, stream=True)
        except BaseException:
            client.close()
            raise
        return _HttpxResponse(client, response)


def installed_signed(
    version: str,
    *,
    store: Path | None = None,
    compatible: Callable[[Path], bool] | None = None,
) -> SignedBundle | None:
    """Read-only: the verified signed bundle for exactly this version, or None.
    Writes nothing."""
    found = webbundle.signed_bundle_for(version, store, compatible=compatible)
    if found is None:
        return None
    return SignedBundle(version=version, path=found, archive_sha256=found.name)


def _check_version(version: str) -> str:
    if _VERSION.fullmatch(version) is None:
        raise ReleaseError("version")
    return version


def _check_url(url: str, hosts: frozenset[str] = ALLOWED_HOSTS) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in hosts
        or parts.port not in (None, 443)
        or parts.username is not None
        or parts.password is not None
    ):
        raise ReleaseError("network")
    return url


class _Download:
    """Manual redirect loop over an injected getter, with one absolute deadline that covers
    the headers and every body read."""

    def __init__(
        self,
        http: HttpGetter,
        clock: Callable[[], float],
        deadline_s: float = TOTAL_DEADLINE_S,
        hosts: frozenset[str] = ALLOWED_HOSTS,
    ) -> None:
        self._http = http
        self._clock = clock
        self._hosts = hosts
        self._deadline = clock() + deadline_s

    def _remaining(self) -> float:
        left = self._deadline - self._clock()
        if left <= 0:
            raise ReleaseError("network")
        return left

    def fetch_to(self, url: str, sink: Callable[[bytes], None]) -> None:
        current = _check_url(url, self._hosts)
        for _hop in range(MAX_REDIRECTS + 1):
            try:
                response = self._http.get(current, timeout=self._remaining())
            except ReleaseError:
                raise
            except Exception as exc:
                raise ReleaseError("network") from exc
            try:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location") or response.headers.get("Location")
                    if not location:
                        raise ReleaseError("network")
                    current = _check_url(urljoin(current, location), self._hosts)
                    continue
                if response.status_code != 200:
                    raise ReleaseError("network")
                if (response.headers.get("content-encoding") or "identity").lower() != "identity":
                    raise ReleaseError("network")
                try:
                    for chunk in response.chunks():
                        self._remaining()
                        if chunk:
                            sink(chunk)
                except ReleaseError:
                    raise
                except Exception as exc:
                    raise ReleaseError("network") from exc
                self._remaining()  # a body that ended after the deadline is not accepted
                return
            finally:
                response.close()
        raise ReleaseError("network")


class _Staging:
    """A random private directory under the store's pinned descriptor."""

    def __init__(self, store: Path) -> None:
        self._store_fd = webbundle._open_store(store)
        self._token = ".download-" + secrets.token_hex(8)
        os.mkdir(self._token, 0o700, dir_fd=self._store_fd)
        self.path = store.resolve() / self._token

    def close(self) -> None:
        shutil.rmtree(self._token, ignore_errors=True, dir_fd=self._store_fd)
        os.close(self._store_fd)

    def __enter__(self) -> _Staging:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _open_local(path: Path, reason: str) -> int:
    """A read-only descriptor on a regular file; never follows a final link."""
    try:
        fd = os.open(path, _READ_FLAGS)
    except OSError as exc:
        raise ReleaseError(reason) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ReleaseError(reason)
    except ReleaseError:
        os.close(fd)
        raise
    except OSError as exc:
        os.close(fd)
        raise ReleaseError(reason) from exc
    return fd


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _read_signature(path: Path) -> bytes:
    fd = _open_local(path, "signature")
    try:
        data = bytearray()
        while piece := os.read(fd, MAX_SIGNATURE_BYTES + 1 - len(data)):
            data.extend(piece)
            if len(data) > MAX_SIGNATURE_BYTES:
                raise ReleaseError("too_large")
        return bytes(data)
    except OSError as exc:
        raise ReleaseError("signature") from exc
    finally:
        os.close(fd)


def _stage_archive(source: Path, staged: Path) -> tuple[str, bytes]:
    """Copy a local archive into private staging while hashing it: (SHA-256 hex, BLAKE2b-512
    digest) of exactly the bytes written. Later steps read only the staged copy."""
    sha = hashlib.sha256()
    blake = hashlib.blake2b(digest_size=64)
    total = 0
    src = _open_local(source, "archive")
    try:
        out = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            while chunk := os.read(src, _COPY_CHUNK):
                total += len(chunk)
                if total > webbundle.MAX_BYTES:
                    raise ReleaseError("too_large")
                sha.update(chunk)
                blake.update(chunk)
                _write_all(out, chunk)
        finally:
            os.close(out)
    except OSError as exc:
        raise ReleaseError("archive") from exc
    finally:
        os.close(src)
    return sha.hexdigest(), blake.digest()


class _BudgetReader:
    """The decompressed tar stream with a hard size budget, so tar metadata cannot grow
    past it before any member is validated. The budget is the file-data cap plus a fixed
    allowance per member."""

    def __init__(self, stream: gzip.GzipFile) -> None:
        self._stream = stream
        self._used = 0
        self.members = 1

    def read(self, size: int = -1) -> bytes:
        room = webbundle.MAX_BYTES + TAR_SLACK_BYTES + TAR_MEMBER_BYTES * self.members - self._used
        want = room + 1 if size < 0 else min(size, room + 1)
        data = self._stream.read(max(want, 0))
        self._used += len(data)
        if len(data) > room:
            raise ReleaseError("too_large")
        return data


def _extract_members(tar: tarfile.TarFile, budget: _BudgetReader, into: Path) -> None:
    seen: set[str] = set()
    total = 0
    for index, member in enumerate(tar):
        budget.members = index + 2
        if index >= webbundle.MAX_FILES:
            raise ReleaseError("archive")
        name = member.name
        parts = PurePosixPath(name).parts
        normalized = "/".join(parts)
        if (
            not name
            or name.startswith("/")
            or ".." in parts
            or len(parts) > webbundle.MAX_DEPTH
            or normalized in seen
        ):
            raise ReleaseError("archive")
        seen.add(normalized)
        if member.isdir():
            tar.extract(member, path=into, filter="data")
            continue
        if not member.isfile():
            raise ReleaseError("archive")
        total += member.size
        if total > webbundle.MAX_BYTES:
            raise ReleaseError("too_large")
        tar.extract(member, path=into, filter="data")


def _extract(archive: Path, into: Path) -> None:
    """Extract regular files and directories only, bounded, with no escapes,
    duplicates, absolute names or `..`; on top of tarfile's data filter."""
    into.mkdir(mode=0o700)
    fd = _open_local(archive, "archive")
    try:
        with os.fdopen(fd, "rb") as raw, gzip.GzipFile(fileobj=raw, mode="rb") as stream:
            budget = _BudgetReader(stream)
            with tarfile.open(fileobj=budget, mode="r|") as tar:  # type: ignore[call-overload]
                _extract_members(tar, budget, into)
    except ReleaseError:
        raise
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        raise ReleaseError("archive") from exc


def _manifest_version(extracted: Path, base: str) -> str:
    manifest = extracted / f"{base}.build-manifest.json"
    try:
        if manifest.is_symlink() or manifest.stat().st_size > webbundle.MAX_MANIFEST_BYTES:
            raise ReleaseError("manifest")
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except ReleaseError:
        raise
    except (OSError, ValueError) as exc:
        raise ReleaseError("manifest") from exc
    version = data.get("version") if isinstance(data, dict) else None
    if not isinstance(version, str):
        raise ReleaseError("manifest")
    return version


def _install_staged(
    staging: _Staging,
    archive: Path,
    archive_sha256: str,
    digest: bytes,
    signature: bytes,
    version: str,
    *,
    store: Path,
    key: bytes | None,
    release_url: str,
) -> SignedBundle:
    """`archive` is the staged copy whose bytes `digest` and `archive_sha256` describe."""
    base = f"relaydeck-web-{version}"
    try:
        public = minisign.parse_public_key(key) if key is not None else None
        minisign.verify_prehashed(digest, signature, public, trusted_comment=f"{base}.tar.gz")
    except minisign.MinisignError as exc:
        raise ReleaseError("signature") from exc
    extracted = staging.path / "extracted"
    _extract(archive, extracted)
    bundle = extracted / base
    top = {child.name for child in extracted.iterdir()}
    if top != {base, f"{base}.build-manifest.json"} or not bundle.is_dir():
        raise ReleaseError("archive")
    if _manifest_version(extracted, base) != version:
        raise ReleaseError("version")
    try:
        record = webbundle.install_signed(
            bundle,
            archive_sha256=archive_sha256,
            version=version,
            release_url=release_url,
            root=store,
        )
    except webbundle.BundleError as exc:
        reason = "csp_metadata_required" if str(exc) == "csp_metadata_required" else "install"
        raise ReleaseError(reason) from exc
    return SignedBundle(version=version, path=record.path, archive_sha256=archive_sha256)


def download_bytes(
    url: str,
    *,
    hosts: frozenset[str],
    limit: int,
    deadline_s: float,
    http: HttpGetter | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """One small file under the release download rules (TLS, redirects only to `hosts`, one
    absolute deadline) and a size cap. Raises ReleaseError("network" or "too_large")."""
    out = bytearray()

    def take(chunk: bytes) -> None:
        out.extend(chunk)
        if len(out) > limit:
            raise ReleaseError("too_large")

    _Download(http or _HttpxGetter(), clock, deadline_s, hosts).fetch_to(url, take)
    return bytes(out)


def fetch_release(
    version: str,
    *,
    store: Path | None = None,
    http: HttpGetter | None = None,
    key: bytes | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SignedBundle:
    """Download, verify, install; raises ReleaseError(reason) with a fixed reason code."""
    version = _check_version(version)
    root = store or webbundle.default_root()
    base = f"relaydeck-web-{version}.tar.gz"
    url = f"{RELEASE_BASE}/v{version}/{base}"
    download = _Download(http or _HttpxGetter(), clock)
    with _Staging(root) as staging:
        archive = staging.path / base
        sha = hashlib.sha256()
        blake = hashlib.blake2b(digest_size=64)
        total = 0
        try:
            out = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise ReleaseError("install") from exc
        try:

            def take(chunk: bytes) -> None:
                nonlocal total
                total += len(chunk)
                if total > webbundle.MAX_BYTES:
                    raise ReleaseError("too_large")
                sha.update(chunk)
                blake.update(chunk)
                try:
                    _write_all(out, chunk)
                except OSError as exc:
                    raise ReleaseError("install") from exc

            download.fetch_to(url, take)
        finally:
            os.close(out)
        signature = bytearray()

        def take_signature(chunk: bytes) -> None:
            signature.extend(chunk)
            if len(signature) > MAX_SIGNATURE_BYTES:
                raise ReleaseError("too_large")

        download.fetch_to(f"{url}.minisig", take_signature)
        return _install_staged(
            staging,
            archive,
            sha.hexdigest(),
            blake.digest(),
            bytes(signature),
            version,
            store=root,
            key=key,
            release_url=url,
        )


def install_archive(
    archive: Path,
    signature_path: Path,
    version: str,
    *,
    store: Path | None = None,
    key: bytes | None = None,
) -> SignedBundle:
    """The offline form of fetch: a local archive and signature, same checks. The archive
    is copied into private staging first and only that copy is verified and extracted."""
    version = _check_version(version)
    root = store or webbundle.default_root()
    signature = _read_signature(signature_path)
    with _Staging(root) as staging:
        staged = staging.path / "archive.tar.gz"
        archive_sha256, digest = _stage_archive(archive, staged)
        return _install_staged(
            staging,
            staged,
            archive_sha256,
            digest,
            signature,
            version,
            store=root,
            key=key,
            release_url="",
        )
