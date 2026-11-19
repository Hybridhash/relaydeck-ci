# Compatibility and credential lifetime

## Supported surfaces

The latest 0.3.x release is the supported line. Installed command names and
documented flags, HTTP and WebSocket contracts, enrollment and pairing, protected
storage schemas, update records and release assets are compatibility boundaries.
Internal Python module paths are not a supported external API.

Wire schemas reject fields they do not understand. Matching application versions
are the safest combination; the 0.x version number is not a promise that arbitrary
client and server versions interoperate.

## HTTP method and route refusals

An API response with HTTP status 405 uses error code `forbidden` and
`details.reason` of `method_not_allowed`; the frozen error schema has no separate
method code. The status remains 405 and the `Allow` header retains the supported
methods. An API path that matches no route answers 404 with error code `not_found`
and `details.reason` of `route_not_found`, in the same error envelope.

## Metrics version skew

A current server-metrics reader accepts older snapshots that omit the newer
diagnostic fields. The reverse does not hold: an older sideloaded native client
can reject a newer server snapshot because its schema forbids additional
properties. Update the native client when updating the companion; do not assume
that an additive server field is compatible with every older client. A working
browser session does not establish native-client compatibility.

## Internal compatibility imports

These are the complete set of temporary Python import paths; each re-exports the
canonical objects without loading a UI framework.

| Compatibility path | Canonical owner | Removal |
| --- | --- | --- |
| `client/adapters/ports.py` | `contracts/storage.py` | 0.3.2 |
| `client/components/design_system/font_versions.py` | `client/design_system/font_versions.py` | 0.3.2 |

## Changes and deprecation

Describe public behavior changes, removals, migration steps and incompatible
combinations in the changelog before release. Prefer a transition period for a
documented interface; there is no fixed deprecation duration promised for the
0.3 series. Security fixes may require an earlier change with explicit release
notes. Reinstallation or re-pairing must be stated when required.

Storage readers migrate only recognized schemas. A newer or corrupt database is
not downgraded or silently replaced to make it load. Updates are manual and have
no automatic rollback. Consult the [recovery guide](recovery.md) before restoring
state.

## Credentials and sessions

| Material | Lifetime |
| --- | --- |
| Gateway access token | 15 minutes. |
| Gateway refresh token | 30 days; each successful refresh rotates the pair and invalidates the presented refresh token. Continued use can renew this period. |
| Connector host token | No fixed expiry; explicit revocation or re-enrollment invalidates it. Each host has one active token. |
| Hosted browser sign-in | Up to 30 days without verified use, with an absolute limit of 90 days after pairing. It cannot outlive the associated refresh grant; renewal is limited to once per hour. |
| WebSocket ticket | Ten seconds and bound to its authorized lease; consumption is single-use. |
| Pairing window | Five minutes by default, with a maximum of five minutes through the pairing command. Approval still requires the matching code on the Mac. |

The browser lifetime is a separate limit from the rotating gateway credentials.
Legacy stored browser sessions do not gain a new full lifetime just by being
restored. Cookies are HttpOnly and SameSite=Lax, with Secure on HTTPS.

The legacy `rd_session` cookie is accepted for one 24-hour window from the
origin's first serve. Restarts and application upgrades reuse that instant;
existing version-specific records contribute their earliest valid instant.
The legacy cookie bridge is removed in 0.3.2.

If the stored first-serve instant cannot be read, the window runs from the
process's first request until storage can be read; the recorded earliest instant
then applies. A session cookie value that is not ASCII counts as no session.

Sign-ins survive a restart only when durable sign-ins are enabled and the key
provider is available. Revocation, host re-enrollment and the external restore
generation can invalidate credentials before their nominal expiry. Restoring a
database does not authorize reviving a consumed or revoked credential.

See the [client storage policy](storage.md), [security reporting](../../SECURITY.md) and
[release verification](../decisions/signed-releases.md).
