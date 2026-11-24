# Client storage and lifecycle

## Credentials and drafts

Native credentials and persisted composer drafts use the `CredentialStore` port,
backed by `FletSecureCredentialStore`. Its `relaydeck.` key prefix namespaces the
platform secure storage; the Android adapter uses EncryptedSharedPreferences.

| Logical key | Content |
| --- | --- |
| `access_token` | Gateway access token. |
| `refresh_token` | Rotating gateway refresh token. |
| `oauth_verifier` | PKCE verifier. |
| `oauth_state` | Pending authorization state. |
| `access_expires_ms` | Access-token expiry. |
| `identity_origin` | The origin the stored credentials belong to; selecting another origin clears them first. |
| `pairing_pending_key` | The name of the pending pairing verifier key. |
| `pairing_verifier_<random>` | The verifier of a pending pairing; deleted when the pairing ends or is cancelled, and at the next start. |
| `draft-<digest>` | Pause-persisted composer snapshot. |

Native `ScopedDrafts` hashes the account and terminal identity into an
adapter-safe `draft-<digest>` key. Draft snapshots can contain commands, paths,
picture references and pasted secrets, so they never fall back to ordinary
preferences. A configured secure-store outage keeps the draft in memory and
reports a capability failure. Without configured persistence, page-owned
`SessionDrafts` is explicitly RAM-only and is cleared on account retirement.

Ordinary preferences contain presentation choices, scoped selection identifiers
and the configured endpoint, not credentials or draft text. Terminal appearance,
workflow, keyboard, pin and acknowledged-update choices use
`relaydeck.terminal_prefs.v1`. Credential persistence and ordinary preferences
have separate ports and owners.

## Process and platform limits

Process death drops in-memory writers, queues, cursors and snapshots. Unconfirmed
input stays unknown and is never automatically replayed. Re-entry refetches
state and explicitly reacquires control; persisted drafts and credentials do not
restore a live writer. An opaque notification reference is revalidated after
sign-in before fetching content.

Android force-stop prevents application work until a manual launch. Background
execution and notification delivery remain subject to platform, battery and
provider limits; no reboot receiver or guaranteed background delivery is claimed.
The session inbox remains the source of truth. Headless tests of storage keys and
failure handling do not establish device persistence or notification delivery.
