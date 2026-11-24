"""Whether the companion for one configuration is up and usable, from its published status."""

from __future__ import annotations

from typing import Any

from relaydeck.companion.config import CompanionConfig

NOT_RUNNING_STATES = ("failed", "stopping", "stopped", "starting", "unresponsive")


def ready(body: dict[str, Any], config: CompanionConfig) -> bool:
    """Require a fresh heartbeat, live API and connector, available pairing and the configured
    address without synthetic mode. The healthy published state is setup_required.
    """
    return (
        isinstance(body.get("instance"), str)
        and body.get("state") not in NOT_RUNNING_STATES
        and body.get("failed") is not True
        and body.get("api_ready") is True
        and body.get("connector_ready") is True
        and body.get("pairing_available") is True
        and body.get("synthetic") is False
        and body.get("advertised_origin") == config.advertised_origin
        and body.get("local_endpoint") == config.local_endpoint
    )


async def probe_private(url: str) -> dict[str, str]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            response = await client.get(url + "/healthz")
            body = response.json()
            if (
                response.status_code != 200
                or not isinstance(body, dict)
                or body.get("ok") is not True
            ):
                return {
                    "state": "failed",
                    "message": "HTTPS route did not reach the companion health endpoint.",
                }
    except (httpx.HTTPError, ValueError):
        return {
            "state": "failed",
            "message": (
                "Private route unavailable. Check Mac power/network, Tailscale login, "
                "HTTPS Serve and device access policy."
            ),
        }
    return {
        "state": "reachable",
        "message": (
            "HTTPS health reachable; application login and Android/mobile access "
            "still need separate verification."
        ),
    }
