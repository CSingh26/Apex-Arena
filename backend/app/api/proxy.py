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

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_403_FORBIDDEN

from app.core.settings import Settings

logger = logging.getLogger(__name__)

PROXY_TOKEN_HEADER = "X-Apex-Proxy-Token"
PUBLIC_HOST_HEADER = "X-Apex-Public-Host"
PUBLIC_PROTO_HEADER = "X-Apex-Public-Proto"
ORIGINAL_PATH_HEADER = "X-Apex-Original-Path"
REQUEST_ID_HEADER = "X-Request-ID"

# Container platforms probe liveness directly, without traversing the proxy, so
# this one path stays reachable without a token. It exposes no state.
UNPROTECTED_PATHS = frozenset({"/health/live"})


class ProxyContextMiddleware(BaseHTTPMiddleware):
    """Validate the proxy token and derive the public origin for each request."""

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.settings = settings

    @property
    def _enforcing(self) -> bool:
        return (
            self.settings.proxy_enforcement_enabled
            and self.settings.apex_arena_proxy_token is not None
            and self.settings.app_env in {"staging", "production"}
        )

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id

        if self._enforcing and request.url.path not in UNPROTECTED_PATHS:
            configured = self.settings.apex_arena_proxy_token
            assert configured is not None  # Narrowed by ``_enforcing``.
            supplied = request.headers.get(PROXY_TOKEN_HEADER)
            if supplied is None or not hmac.compare_digest(supplied, configured.get_secret_value()):
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
