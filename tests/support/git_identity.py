"""One synthetic commit identity for tests that build throwaway repositories."""

from __future__ import annotations

AUTHOR_NAME = "Synthetic Author"
AUTHOR_EMAIL = "synthetic-author@users.noreply.example.com"
AUTHOR_LINE = f"{AUTHOR_NAME} <{AUTHOR_EMAIL}>"
AUTHOR_CONFIG = ("-c", f"user.name={AUTHOR_NAME}", "-c", f"user.email={AUTHOR_EMAIL}")
