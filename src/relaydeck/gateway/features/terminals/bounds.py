"""Terminal identity, attachment-kind, lease-mode and default geometry bounds."""

# Attach/lease documents use inline shape checks and versioned replies.
ATTACHMENT_KINDS = ("agent", "shell")
MAX_TERMINAL_ID_CHARS = 128
MAX_ATTACHMENT_ID_CHARS = 128
MAX_LEASE_ID_CHARS = 128
DEFAULT_PTY_COLS = 80
DEFAULT_PTY_ROWS = 24
