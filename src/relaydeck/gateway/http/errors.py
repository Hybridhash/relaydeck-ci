"""The gateway's one error envelope: code, message, retryable and optional details."""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from inspect import iscoroutinefunction
from typing import Any, Literal, cast

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from relaydeck.contracts.paths import is_api_path
from relaydeck.contracts.schemas import ERROR_STATUS, validate
from relaydeck.gateway.http.middleware.request_id import HEADER, SCOPE_KEY
from relaydeck.host.observability.exceptions import exception_category
from relaydeck.host.observability.request_context import current_request_id
from relaydeck.host.persistence.contracts import StorageUnavailable

LOG = logging.getLogger("relaydeck.gateway.errors")


MAX_RETRY_AFTER_S = 3600


def _retry_details(details: dict[str, Any] | None) -> tuple[dict[str, Any] | None, int | None]:
    if details is None or "retry_after_s" not in details:
        return details, None
    copied = dict(details)
    value = copied.pop("retry_after_s")
    if (
        type(value) not in (int, float)
        or value <= 0
        or (type(value) is float and not math.isfinite(value))
    ):
        return copied, None
    delay = min(MAX_RETRY_AFTER_S, max(1, math.ceil(value)))
    copied["retry_after_s"] = delay
    return copied, delay


def error_response(
    code: str,
    message: str,
    retryable: bool = False,
    *,
    status: int | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    details, retry = _retry_details(details)
    actual_status = status or ERROR_STATUS[code]
    headers = {}
    if retry is not None:
        headers["Retry-After"] = str(retry)
    body: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if details is not None:
        body["details"] = details
    validate("relaydeck/gateway/v1/error", body)
    return JSONResponse(
        status_code=actual_status,
        content=body,
        headers=headers,
    )


class GatewayError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        *,
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        self.details = details


def _request_id(request: Request) -> str:
    return str(request.scope.get(SCOPE_KEY) or current_request_id())


def _route(request: Request) -> str:
    # The route template, never the raw URL (it can carry ids and query strings).
    return str(getattr(request.scope.get("route"), "path", "-"))


def _log_failure(request: Request, code: str, status: int) -> None:
    LOG.log(
        logging.ERROR if status >= 500 else logging.INFO,
        "gateway_error code=%s status=%s method=%s route=%s request_id=%s",
        code,
        status,
        request.method,
        _route(request),
        _request_id(request),
    )


async def gateway_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, GatewayError):
        raise exc
    response = error_response(
        exc.code, exc.message, exc.retryable, status=exc.status, details=exc.details
    )
    _log_failure(request, exc.code, response.status_code)
    return response


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Neither the body nor the validation messages are logged or returned.
    response = error_response("forbidden", "malformed request", details={"reason": "malformed"})
    _log_failure(request, "forbidden", response.status_code)
    return response


async def storage_unavailable_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StorageUnavailable):
        raise exc
    response = error_response(
        "unauthenticated",
        "Protected sign-in storage unavailable",
        status=503,
        details={"reason": "storage_unavailable", "storage_reason": exc.code},
    )
    _log_failure(request, "unauthenticated", response.status_code)
    return response


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    LOG.error(
        "unhandled_error method=%s route=%s request_id=%s category=%s",
        request.method,
        _route(request),
        _request_id(request),
        exception_category(exc),
    )
    response = error_response("internal", "Unexpected server error", status=500)
    # This handler runs outside the request-ID middleware, so it sets the header itself.
    response.headers[HEADER] = _request_id(request)
    return response


def framework_response(
    status: Literal[404, 405], headers: Mapping[str, str] | None = None
) -> JSONResponse:
    response = error_response(
        "forbidden" if status == 405 else "not_found",
        "method not allowed" if status == 405 else "unknown API route",
        status=status,
        details={"reason": "method_not_allowed" if status == 405 else "route_not_found"},
    )
    response.headers.update(headers or {})
    return response


_HTTPHandler = Callable[[Request, HTTPException], Response | Awaitable[Response]]


class _APIHTTPErrorHandler:
    def __init__(self, fallback: _HTTPHandler) -> None:
        self._fallback = fallback

    async def __call__(self, request: Request, exc: Exception) -> Response:
        if not isinstance(exc, HTTPException):
            raise exc
        if is_api_path(request.scope.get("path", "")) and exc.status_code in (404, 405):
            return framework_response(405 if exc.status_code == 405 else 404, exc.headers)
        if iscoroutinefunction(self._fallback) or iscoroutinefunction(
            cast(Any, self._fallback).__call__
        ):
            return await cast(Awaitable[Response], self._fallback(request, exc))
        return cast(Response, await run_in_threadpool(self._fallback, request, exc))


def install_framework_errors(app: FastAPI) -> None:
    fallback = app.exception_handlers.get(HTTPException, http_exception_handler)
    if not isinstance(fallback, _APIHTTPErrorHandler):
        app.add_exception_handler(HTTPException, _APIHTTPErrorHandler(cast(_HTTPHandler, fallback)))


def install_error_handlers(app: FastAPI) -> None:
    """Every app serving gateway routes registers the same handlers."""
    install_framework_errors(app)
    app.add_exception_handler(GatewayError, gateway_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StorageUnavailable, storage_unavailable_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
