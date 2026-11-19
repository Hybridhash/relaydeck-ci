# Notification extension boundary

The extension bridges native permission/token methods, notification taps and
application links to typed Flet events. It does not interpret terminal activity
or authorize application actions. Missing Firebase configuration leaves push
unavailable while the application stays usable. The native background entry
point remains top-level with its VM entry-point pragma; it does not execute
commands or persist transcripts.

Flet 1.0.3 reflects raw event-field annotations and unwraps ForwardRef rather
than arbitrary postponed annotation strings. Event-handler annotations therefore
evaluate when defined; adding postponed annotations can break live rendering.
The terminal extension follows the same framework constraint.

The payload guard accepts only notification_id, device_ref, host_ref, session_ref,
event_ref, category, created_at and expires_at. The required nonempty fields are
notification_id, event_ref and category; references and timestamps can be omitted
by a three-key forwarder. Values are capped at 512 characters and reject control
characters below U+0020 other than tab. Categories are attention or completion.
Unknown keys fail rather than becoming application data. This is shape/value
validation, not semantic inspection of the contents of an opaque string.

Tap-event conversion includes required fields and nonempty optional references.
The initial-tap method validates its returned dictionary with the same guard.
Consumers validate event data before resolving references through authenticated
gateway routes. Those routes enforce identity, device, host and expiry policy;
a notification tap never submits terminal input or an approval decision.

The gateway emits the full generic hint key set. Native forwarding may send its
required subset, which the guard still accepts. The lock-screen payload contract
keeps transcripts, repository paths, commands, secret fields and decisions out
of that key set. The native extension forwards application-link URIs without a host allowlist. Python routing
validates permitted callback/deep-link origins and paths before acting.
