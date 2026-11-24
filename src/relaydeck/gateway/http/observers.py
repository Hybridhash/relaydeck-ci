"""Install outer observers shared by bare and hosted gateway applications."""

from fastapi import FastAPI

from relaydeck.gateway.http.errors import framework_response
from relaydeck.gateway.http.middleware.api_routing import install_api_boundary
from relaydeck.gateway.http.middleware.auth_challenge import BearerChallengeMiddleware
from relaydeck.gateway.http.middleware.metrics import Metrics, install_metrics


def install_observers(app: FastAPI, metrics: Metrics) -> None:
    install_metrics(app, metrics)
    install_api_boundary(app, framework_response)
    app.add_middleware(BearerChallengeMiddleware)
