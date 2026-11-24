"""Listener WebSocket frame and graceful stream-shutdown limits."""

# Largest WebSocket frame uvicorn accepts: above the 1 MiB snapshot, far above every input frame.
WS_MAX_SIZE = 2 * 1024 * 1024

# Seconds uvicorn waits for open streams before it closes them on shutdown.
GRACEFUL_SHUTDOWN_S = 5
