# SPDX-License-Identifier: AGPL-3.0-only
"""Proxy awareness for requests that arrive through the public portfolio domain.

Public traffic reaches this service as:

    browser -> chaitanyasingh.org (portfolio on Vercel)
            -> Apex Arena frontend origin
            -> this FastAPI service

Nothing in that chain is reachable directly by a browser, so the backend must
rebuild public URLs from forwarded metadata rather than the raw ``Host`` header,
and it must be able to reject traffic that did not come through the proxy.
"""

from __future__ import annotations

import hmac
import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_403_FORBIDDEN

from app.core.settings import Settings

logger = logging.getLogger(__name__)

PROXY_TOKEN_HEADER = "X-Apex-Proxy-Token"
REPLAY_OPERATOR_HEADER = "X-Apex-Replay-Password"
PUBLIC_HOST_HEADER = "X-Apex-Public-Host"
PUBLIC_PROTO_HEADER = "X-Apex-Public-Proto"
ORIGINAL_PATH_HEADER = "X-Apex-Original-Path"
REQUEST_ID_HEADER = "X-Request-ID"

# Container platforms probe liveness directly, without traversing the proxy, so
# this one path stays reachable without a token. It exposes no state.
UNPROTECTED_PATHS = frozenset({"/health/live"})


def replay_operator_password(settings: Settings) -> str | None:
    """Return the configured replay credential without normalizing its value."""
    configured = settings.admin_dashboard_password
    if configured is None:
        return None
    value = configured.get_secret_value()
    return value if value.strip() else None


def require_replay_operator(request: Request) -> None:
    """Authorize a replay mutation independently from the public proxy hop."""
    settings: Settings = request.app.state.services.settings
    configured = replay_operator_password(settings)
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Replay operator access is not configured",
        )

    header_name = REPLAY_OPERATOR_HEADER.lower().encode("ascii")
    supplied_values = [
        value for name, value in request.scope["headers"] if name.lower() == header_name
    ]
    supplied = supplied_values[0] if len(supplied_values) == 1 else None
    if (
        supplied is None
        or not supplied.strip()
        or not hmac.compare_digest(supplied, configured.encode("utf-8"))
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid replay operator credential",
        )


class ProxyContextMiddleware(BaseHTTPMiddleware):
    """Validate the proxy token and derive the public origin for each request."""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings
        self._enforcing = settings.proxy_enforcement_enabled and settings.app_env in {
            "staging",
            "production",
        }
        configured = settings.apex_arena_proxy_token
        configured_value = configured.get_secret_value() if configured is not None else None
        self._proxy_token = (
            configured_value
            if configured_value and configured_value == configured_value.strip()
            else None
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        if self._enforcing and request.url.path not in UNPROTECTED_PATHS:
            supplied_values = request.headers.getlist(PROXY_TOKEN_HEADER)
            supplied = supplied_values[0] if len(supplied_values) == 1 else None
            if (
                self._proxy_token is None
                or supplied is None
                or not supplied
                or supplied != supplied.strip()
                or not hmac.compare_digest(supplied, self._proxy_token)
            ):
                # Log the correlation id only; never the supplied token value.
                logger.warning(
                    "Rejected non-proxied request path=%s request_id=%s",
                    request.url.path,
                    request_id,
                )
                return JSONResponse(
                    status_code=HTTP_403_FORBIDDEN,
                    content={"detail": "Direct origin access is not permitted"},
                    headers={REQUEST_ID_HEADER: request_id},
                )

        request.state.public_host = self._public_host(request)
        request.state.public_proto = self._public_proto(request)
        request.state.public_base_path = self.settings.normalized_base_path
        request.state.original_path = request.headers.get(ORIGINAL_PATH_HEADER, request.url.path)

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    def _public_host(self, request: Request) -> str:
        """Resolve the browser-visible host.

        A forwarded host is only honoured when it is explicitly trusted, so a
        spoofed header cannot poison generated links.
        """
        configured = self.settings.public_proxy_host.strip()
        if configured:
            return configured
        forwarded = (
            (
                request.headers.get(PUBLIC_HOST_HEADER)
                or request.headers.get("X-Forwarded-Host")
                or ""
            )
            .split(",")[0]
            .strip()
        )
        trusted = self.settings.trusted_proxy_host_list
        if forwarded and (not trusted or forwarded in trusted):
            return forwarded
        return request.url.netloc

    def _public_proto(self, request: Request) -> str:
        forwarded = (
            (
                request.headers.get(PUBLIC_PROTO_HEADER)
                or request.headers.get("X-Forwarded-Proto")
                or ""
            )
            .split(",")[0]
            .strip()
        )
        if forwarded in {"http", "https"}:
            return forwarded
        return "https" if self.settings.app_env in {"staging", "production"} else request.url.scheme


def public_origin(request: Request) -> str:
    """Browser-visible origin, e.g. ``https://chaitanyasingh.org``."""
    proto = getattr(request.state, "public_proto", request.url.scheme)
    host = getattr(request.state, "public_host", request.url.netloc)
    return f"{proto}://{host}"


def public_url(request: Request, path: str = "/") -> str:
    """Browser-visible URL beneath the configured base path."""
    base_path = getattr(request.state, "public_base_path", "")
    suffix = path if path.startswith("/") else f"/{path}"
    if suffix == "/":
        suffix = ""
    return f"{public_origin(request)}{base_path}{suffix}"
