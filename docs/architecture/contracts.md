# Shared wire and enrollment contracts

The shared kernel validates the formats below without third-party dependencies.

## Endpoint trust

Source: `src/relaydeck/contracts/endpoints.py`.

TLS is required except for loopback and explicitly allowed development hosts.

## Enrollment references

Source: `src/relaydeck/contracts/enrollment.py`.

Enrollment key encoding and message shapes shared by the gateway, the connector and the Mac side.

QR payloads carry a bounded enrollment reference plus public context only: gateway origin,
invitation id, claim code, expiry. Reusable credentials never enter the QR. The proof message is
the exact byte string a connector signs and the gateway checks; signing and checking live in
`relaydeck.host.security.enrollment`. Standard library only.

## Grapheme counting

Source: `src/relaydeck/contracts/graphemes.py`.

One cluster splitter implements Unicode UAX #29 extended grapheme clusters, including rule GB9c. It
serves the client's name counter and input clamp (`client/features/rename/rename_model.py`) and the
gateway's display-name check (`contracts/launch.py`, `gateway/features/commands/policy.py`), so the
counter, the clamp and the gateway policy always agree. Paste counts and previews use a different,
simpler rule described in [client input](client-input.md).

## Key-combo validation

Source: `src/relaydeck/contracts/keys.py`.

The key-combo grammar mirrors Herdr 0.9.1 `src/config/keybinds.rs::parse_key_combo` and `src/app/api_helpers.rs::normalize_api_key_alias` at commit `065ef9d6`. The gateway rejects invalid names before dispatch; Herdr parses again and remains authoritative. Terminal byte mappings come from Herdr's encoder, not this validator.

## Connector tunnel envelopes

Source: `src/relaydeck/contracts/tunnel.py`.

Connector tunnel envelopes do not introduce Herdr methods. Both ends validate the same input shape, and snapshot content uses the shared schema.

Tunnel messages are JSON text documents of at most 64 KiB (`TUNNEL_MAX_TEXT`, defined in
`connector/link.py` and `gateway/http/bounds.py`); both ends refuse a larger frame. The messages that
carry commands and output are:

- `command.dispatch` (gateway to connector) carries `command_id`, `principal`, `host_id`,
  `host_epoch`, `gateway_kind`, `target`, `payload`, `digest`, `generation` and `expires_ms`. The
  digest is the canonical-body hash; a mismatch is refused before acknowledgment or execution.
- `command.ack` (connector to gateway) carries `command_id` and `host_id`. It is sent after the
  dispatch passes its host and digest checks and before the command runs.
- `command.result` (connector to gateway) carries `command_id`, `host_id`, `status`,
  `result_digest` and optionally `error_code` and `result`. The status is `succeeded`, `failed`,
  `rejected` or `unknown`. A recorded `result` is at most 4 KiB and holds only identifiers and a
  type.
- `terminal.output` (connector to gateway) carries `host_id`, `terminal_id`, `epoch` and
  `data_b64`. The decoded bytes are at most 64 KiB per message. The gateway applies output in the
  order it arrives on the tunnel.

`session.close` runs `pane.close`: the upstream method allowlist has no agent close, and the
connector falls back to `tab.close` only after a `pane_not_found` refusal.

## Terminal WebSocket framing

Source: `src/relaydeck/contracts/wire/terminal.py`.

Gateway<->client terminal WebSocket framing v2.

Binary messages carry bytes; JSON text messages carry control documents
(validated against relaydeck/gateway/v1/terminal-control: ack, gap,
reset, bye and history_reset). The history_reset document requires epoch, offset and
incomplete fields. v1 (0x01) frames are rejected: no silent downgrade.

```text
Client -> gateway input (0x02):
  [0x02][u16 lease_len][lease utf8][u32 generation][u32 input_seq]
  [u8 kind][u32 len][payload]
  kind 0x01 text:   payload is UTF-8 (validated), <= 16 KiB
  kind 0x02 key:     payload is a Herdr key-combo name (validated
                    against relaydeck.contracts.keys), <= 64 bytes
  kind 0x03 resize:  payload is exactly [u16 cols][u16 rows], 1..1024

Gateway -> client output (0x03):
  [0x03][u16 epoch_len][epoch utf8][u64 offset][u32 len][bytes <= 64 KiB]
```
`offset` is the cumulative byte offset of this chunk. An epoch change
means reset; an offset gap means resync. Gap/reset markers themselves
ride the text control channel.

## Envelope identity

Source: `src/relaydeck/contracts/schemas.py`.

Write and event envelopes carry the gateway schema marker so they cannot be
confused with upstream Herdr documents. Authentication envelopes omit it.

## HTTP failures and command outcomes

Source: `src/relaydeck/contracts/schemas.py`.

| Condition | HTTP status and envelope |
| --- | --- |
| Malformed JSON, schema violation or unknown field | 400 `forbidden`, with `details.reason` |
| Unknown command kind | 400 `forbidden`, with `details.reason=malformed` |
| `Content-Length` that is repeated, is not a plain decimal, or comes with `Transfer-Encoding` | 400 `forbidden`, with `details.reason=body_framing` |
| Body over the 64 KiB JSON limit, declared or received | 413 `too_large`, with `details.reason=body_size` |
| API path that matches no route | 404 `not_found`, with `details.reason=route_not_found` |
| Missing capability | 403 `unsupported_capability` |
| Unknown host, command, agent, pane, tab or workspace | 404 `not_found` |
| Invalid, expired or forged cursor | 409 `stale_epoch`, with `details.reason=cursor`; resnapshot |
| Stale writer generation | 409 `stale_revision` |
| Herdr transport failure | 503 `host_offline`, retryable |
| Upstream protocol drift | 500 `upstream_incompatible`, not retryable |
| Admission capacity or body-receive budget exhausted | 429 `rate_limited`, retryable, with `details.reason` `capacity` or `body_timeout` and `Retry-After: 1` |
| Command or Git-read rate limit | 429 `rate_limited`, retryable, with `details.retry_after_s` and a `Retry-After` header when the limiter can name a delay |
| Unhandled gateway fault | 500 `internal`; request ID in `X-Request-ID` |

A 401 on a route that authenticates with a bearer token carries `WWW-Authenticate: Bearer`.
`retry_after_s` is whole seconds, from 1 to 3600, and the `Retry-After` header repeats it.

Herdr command outcomes such as `agent_blocked`, `agent_not_ready` and
`pane_not_found` belong in command records and event payloads as
`details.upstream_code`, rather than in the gateway envelope code set.
