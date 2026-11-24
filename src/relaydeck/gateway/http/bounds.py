"""HTTP and connector-tunnel size, timing and admission limits."""

# Ordinary JSON body limit; authenticated picture uploads have a separate route budget.
MAX_JSON_BODY = 64 * 1024

# Per application instance. Streaming connections do not consume ordinary HTTP admission.
MAX_HTTP_REQUESTS = 64
MAX_EVENT_STREAMS = 32
MAX_WEBSOCKETS = 128
BODY_RECEIVE_SECONDS = 5.0

# 1 MiB bounded snapshot messages (compromised-connector DoS cap).
MAX_SNAPSHOT_BYTES = 1024 * 1024

# inventory page bound.
MAX_AGENTS_PER_PAGE = 200

# SSE keepalive: comment frames the parser skips.
EVENTS_HEARTBEAT_S = 15.0

# How often a live stream checks whether the gateway is still serving.
SHUTDOWN_POLL_S = 1.0

# Initial idle tunnel-pump wait; double it up to TUNNEL_POLL_MAX_S.
TUNNEL_POLL_S = 0.05
# Enqueue and tunnel-document wakeups drive dispatch; this is only the
# safety-net cadence for producers that cannot signal the pump.
TUNNEL_POLL_MAX_S = 5.0
# A live terminal viewer re-checks its lease at expiry; this caps how long
# it may go without any lease re-check when no expiry is known sooner.
TERMINAL_LEASE_CHECK_MAX_S = 30.0
TUNNEL_MAX_TEXT = 64 * 1024
TUNNEL_MAX_OUTPUT = 64 * 1024
