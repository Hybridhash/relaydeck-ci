# Companion setup, hosted sessions and resource ownership

## One foreground owner

The companion composes gateway, connector, repository and usage services and
owns their startup and shutdown. A nonblocking private state-directory lock is
shared with setup. Stop requests are nonce-bound rather than PID signals;
shutdown never falls back to killing another process or listener. Published
heartbeats are liveness hints, while state transitions remain durable. Cleanup
keeps its heartbeat and lock until every started close settles, even if the
caller is cancelled. Herdr and its remote agent processes are not stopped.

The Herdr link owns at most one source, link and retry. A failed or cancelled
source is closed before a replacement can start; a failed close is retried
instead of creating another source. Enrollment is rechecked on retry so revoked
credentials stop reconnecting. Transient refusal/timeouts can retry with backoff;
protocol or malformed-response failures require operator action.

## Hosted web composition and cookies

Gateway API and authentication routes precede the Flet mount in one FastAPI
origin. A single Uvicorn worker owns session, lease and socket registries;
multiple workers would split those authorities. HTTPS session cookies use
__Host-rd_session with Secure, HttpOnly, SameSite=Lax, Path=/ and no Domain.
Explicit HTTP loopback development uses rd_dev_session, and verified legacy
cookies have a bounded acceptance bridge. Gateway access/refresh credentials
stay in the server-side session store, never in browser content.

A session cookie value that is not ASCII is treated as no session. A response that already sets
the session cookie is not given a second one. Cookie renewal wraps the request-size middleware, so
replies from that middleware pass through it, and only replies below status 400 renew a cookie.
If the stored first-serve instant cannot be read, the legacy-cookie window runs from this process's
first request until storage can be read; the recorded earliest instant then applies.

Verified live use can extend idle web expiry by thirty days, throttled to at
most hourly storage updates. Expiry never exceeds ninety days from pairing or
the associated refresh grant. Restoring a stored row does not count as live
use, and missing pairing metadata follows the fixed legacy lifetime. Cookies
use the server session's remaining lifetime; renewal can reflect an already
extended live session without independently granting access.

A pure-ASGI guard checks exact Origin for the Flet WebSocket handshake, allows
an anonymous login screen and requires exactly one initial REGISTER_CLIENT.
It rejects an unauthorized reconnect ID before Flet sees it. Registration claims
ownership before awaits and rechecks local denial fences after private reads;
a delayed callback cannot undo logout, retarget a socket or publish over its
successor. A valid handoff waits for the old registered socket's cleanup before
attaching the successor. The boundary follows pinned Flet 1.0.3 framing, action
codes and session-key construction.

The authentication callback exchanges a state-bound single-use code on the
server. Pairing attempts belong to a cookie-bound page and flow generation;
concurrent callers share only that exact attempt. An anonymous page can adopt
another page's held attempt only when one unambiguous browser candidate exists.
Authenticated pages do not reach another page's attempt.

Logout checks exact Origin, fences local writers, revokes gateway credentials,
severs device terminal sockets, closes the session's Flet sockets and resources,
and clears its cookie. Storage failure cannot leave local writers alive; durable
retry metadata remains separate from access authorization. The in-app action
may keep its live page to render sign-in, while HTTP logout keeps no page.
Signature-verified revocation retains origin/account/device scope even for
expired or denied credentials. Missing data proves success only after a
successful durable read/commit, not after an unavailable lookup.

## Browser uploads, caching and recovery

Flet browser uploads cannot add bearer headers, so an authenticated exact-origin
cookie is translated into upload proof. Native uploads still need their bearer
credential; a grant URL alone is insufficient. Headers/CSP wrap the completed
application, including guard refusals, using verified build-bound metadata.

Static assets normally revalidate through ETag/Last-Modified because stable
paths can change across framework upgrades. Only bundled fonts whose requested
content fingerprint matches the actual file receive long freshness. API,
WebSocket-handshake and health responses are untouched by that cache policy.
Compression applies to page/static GET responses rather than API, SSE or sockets.

An independent browser recovery script remains usable when Flutter never gets
a page and the pinned client keeps retrying policy closures. Only a deliberate
click removes this tab's Flet session ID; cookies and other tabs remain intact.
The script is injected only into application index/client-route responses, with
conditional index responses disabled so cached pages cannot omit recovery.
A packaged signed bundle must match this installed application version unless
FLET_WEB_PATH explicitly selects a developer override; stock Flet and unrelated
version bundles are not silent fallbacks.

## Read-only readiness and explicit setup

Detection keeps four facts separate: Herdr support, installed executable,
available usage reader and configured entry. It runs one bounded read-only
Herdr status command and otherwise checks metadata without running agent CLIs.
Prerequisites never install, log in, alter Serve, request certificates or start
services. Reports use fixed guidance, versions and port numbers rather than
raw stderr, private hostnames or auth URLs. Non-ok prerequisites block core
setup; optional usage-root problems are warnings.

The twelve readiness checks take external reports as inputs and do not prompt
or write. Secret files are described by presence, size and permissions, never
values. Companion readiness requires a fresh heartbeat, API and connector up,
pairing available, matching configured address and nonsynthetic mode; its
healthy published status is setup_required. Optional usage, limits, notifications and login-service
checks do not block pairing.

Interactive setup asks before changes, holds the companion state lock while
writing private configuration/secrets/enrollment, and uses one backed-up,
receipted config writer. --yes can answer the private-route and prerequisite
remedy questions, not usage sources, limits, folder trust, launchable kinds,
hooks, enrollment, login service or pairing. Declining the login service means
configure-only: no startup, readiness wait or pairing. A core blocked/conflicted/
failed step stops dependent work; optional failures are reported without stopping
core setup. Device pairing still requires the owner to type the device's code,
with one wizard/window owner and cleanup only of the window that wizard opened.

The login service plist runs `relaydeck service-run --log-directory ~/Library/Logs/RelayDeck`, and
that command keeps each log stream to the size and archive count in the
[recovery guide](../development/recovery.md); launchd's own output goes to `/dev/null`. An
installed agent whose plist runs another command reads as different: setup plans to replace it,
`setup --check` warns "the login service runs a different command than this install", and its log
files are not rotated until `relaydeck setup` replaces it.

Private-file inspection reports present/create/rewrite/replace/conflict/refused
without creating directories. Existing unsafe files are refused. Config changes
preserve unrelated TOML comments/spacing/tables, validate by parsing, and mark
managed usage blocks with body digests; undo removes only still-matching bodies.
The writer pins no-follow directory descriptors, holds a stable sibling lock,
and compares expected bytes before staging and immediately before rename.
Noncooperating editors can still race those checks, so other settings writers
must stop. A committed rename with failed parent sync is uncertain and must not
be reported as unchanged or restored from backup without consent.

## Private routing, remedies and update work

Tailscale routing is opt-in and tailnet-only. Funnel and conflicting mappings
are refused; only a mapping created by this run is removed. Setup uses the
configured private HTTPS port, never 8443/8444. launchd installation is per-user
and requires its explicit confirmation; loading/unloading also requires the
corresponding option. No secrets enter the launch environment, and diagnostics
discard launchctl output that could contain paths/environment values.

A prerequisite remedy performs one consented action, such as running the bounded
official installer or opening Tailscale for the user. Reports retain fixed lines
rather than installer output. A probe-folder trust question remains in the tool's
own terminal; setup does not type an answer or write the trust store. Relative
state directories resolve against the config directory so real-path trust is
consistent with loading. Limits-hook installation and repair retain their own consent and private-file boundaries.

One daily-update task schedules bounded network work on a daemon thread. Shutdown
can stop waiting for that thread, whose own deadline still applies; its result
cannot publish or write after stop. Failed checks do not affect terminal service.
Stored attempt/deadline state prevents immediate restart storms; clock rollback
is clamped and an impossible future attempt is reconsidered in ten minutes,
with the kept deadline no later than twenty-five hours after the last attempt.
Manual update instructions are quoted command text, never executed by their
builder. Install/fetch/restart are chained so failure stops the sequence, and
inputs with newlines or nonprinting characters are refused. The two reminder
lines are `printf` commands whose text is one quoted argument, never a shell
comment: a newline would end a comment and run what follows.

The config editor requires exactly one matching top-level line or raises ValueError. Its locked
replacement reports ConcurrentEdit for changed bytes, ConfigLocked for a cooperating writer,
CommitUncertain after a committed rename whose parent sync failed, and OSError for a failed commit.
The comparison is not atomic against noncooperating editors, which must be stopped during setup.

CLI catalog data separates detected executable names from launch programs. Missing program mappings
use the kind name; a null launch mapping means Herdr cannot start that kind. Reader roots are setup
inputs, while default roots are detection hints; no catalogue probe executes the discovered program.
Reader tiers distinguish supported formats, JSON/JSONL or SQLite reader gaps, unavailable reliable
token totals, and unsupported sources.
