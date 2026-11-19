# Synthetic development boundaries

Development fixtures provide explicit synthetic data without a real host or PTY.

## Local development modes

Source: `src/relaydeck/devtools/dev_server.py`.

Development data and loopback login require explicit local-run flags. They provide synthetic empty/demo inventory and a backend with no PTY or connector. Device builds exclude this package.

## Authenticated empty snapshots

Source: `src/relaydeck/devtools/dev_server.py`.

The local runner has no connector to enroll a host, so its synthetic host needs a schema-valid seed to synchronize. Even an empty inventory needs a nonempty authenticated sequence-zero HMAC cursor: the event route rejects an unauthenticated cursor, and `encode_cursor` rejects an empty secret.

## In-memory connector

Source: `src/relaydeck/devtools/fakes/connector.py`.

FixtureAdapter implements the journal dispatch protocol with an in-memory daemon. Scripted panes, agents, calls and terminal bytes are deterministic. Snapshot publication replaces the gateway projection, and events deduplicate by event_id on reconnect. Terminal bytes are drained once because a second append would duplicate output.

## Synthetic lease replacement

Source: `src/relaydeck/devtools/fakes/gateway.py`.

The synthetic backend can pin a replacement control lease without checking
ownership. Production authorization uses attachment creation followed by control
acquisition or view issuance.
