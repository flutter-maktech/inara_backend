"""
app/core/service_key_middleware.py
==================================
Pure-ASGI middleware that enforces read-only Service API Keys.

It only acts on requests that carry the service-key header (default
`X-API-Key`). Every other request, including dashboard and mobile-app traffic
with Bearer tokens, passes through unchanged.

For a request with a service key, checks run in this order and stop at the
first failure. All of them run BEFORE routing, request-body parsing or any
database write, so no write can succeed silently:

  1. Both X-API-Key and Authorization sent       → 400
  2. Key unknown / expired / revoked / wrong env  → 401
  3. Method not GET/HEAD                           → 403  (method gate)
  4. Path not in allowlist                         → 403  (endpoint allowlist)
  5. Caller IP not in allowlist (if configured)    → 403
  6. Rate limit exceeded                           → 429 + Retry-After

On success the resolved principal is placed in `scope["state"]
["service_principal"]`, where `get_current_user` (dependencies.py) picks it up.
Every successful response gets X-RateLimit-* headers, and JSON response
bodies are passed through `redact_personal_data()` (e-mail, phone,
date-of-birth, address removed) before they leave the server.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.core.config import settings
from app.core.service_keys import (
    READ_ONLY_METHODS,
    is_ip_allowed,
    is_path_allowed,
    key_fingerprint,
    rate_limiter,
    redact_personal_data,
    resolve_service_principal,
)

logger = logging.getLogger("inara.service_keys")


def _header(scope, name: str) -> Optional[str]:
    target = name.lower().encode("latin-1")
    for k, v in scope.get("headers") or []:
        if k.lower() == target:
            return v.decode("latin-1")
    return None


def _client_ip(scope) -> Optional[str]:
    """
    Behind Traefik, the real client IP is the LAST entry in X-Forwarded-For
    (Traefik appends the address it saw). Earlier entries are client-supplied
    and can't be trusted.
    """
    xff = _header(scope, "x-forwarded-for")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    client = scope.get("client")
    return client[0] if client else None


async def _send_json(send, status: int, body: dict, extra_headers: Optional[list] = None):
    payload = json.dumps(body).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
        (b"cache-control", b"no-store"),
    ] + (extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class ServiceKeyMiddleware:
    def __init__(self, app):
        self.app = app
        self.header_name = settings.SERVICE_KEY_HEADER_NAME

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not settings.SERVICE_KEY_ENABLED:
            return await self.app(scope, receive, send)

        raw_key = _header(scope, self.header_name)
        if raw_key is None:
            return await self.app(scope, receive, send)          # normal traffic: untouched

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        raw_key = raw_key.strip()

        # 1. Ambiguous credentials
        if _header(scope, "authorization"):
            return await _send_json(send, 400, {
                "detail": f"Send either {self.header_name} or Authorization, not both."
            })

        # 2. Authenticate
        principal = await resolve_service_principal(raw_key)
        if principal is None:
            logger.warning("service-key rejected (invalid) path=%s", path)
            return await _send_json(
                send, 401,
                {"detail": "Invalid, expired or revoked API key."},
                [(b"www-authenticate", self.header_name.encode())],
            )
        fp = key_fingerprint(principal.key_hash)

        # 3. Method gate: read-only, always
        if method not in READ_ONLY_METHODS:
            logger.warning("service-key write blocked key=%s %s %s", fp, method, path)
            return await _send_json(send, 403, {
                "detail": "This API key is read-only. Only GET requests are permitted.",
                "method": method,
            }, [(b"allow", b"GET, HEAD")])

        # 4. Endpoint allowlist
        if not is_path_allowed(path):
            logger.warning("service-key path blocked key=%s %s", fp, path)
            return await _send_json(send, 403, {
                "detail": "This API key is not authorised for this endpoint.",
                "path": path,
            })

        # 5. Optional IP allowlist
        ip = _client_ip(scope)
        if not is_ip_allowed(ip):
            logger.warning("service-key IP blocked key=%s ip=%s", fp, ip)
            return await _send_json(send, 403, {
                "detail": "Requests with this API key are not permitted from this IP address.",
            })

        # 6. Rate limit
        allowed, remaining, retry_after = rate_limiter.hit(principal.key_hash)
        limit_headers = [
            (b"x-ratelimit-limit", str(rate_limiter.limit).encode()),
            (b"x-ratelimit-remaining", str(remaining).encode()),
        ]
        if not allowed:
            return await _send_json(send, 429, {
                "detail": "Rate limit exceeded. Retry after the number of seconds in Retry-After.",
                "limit_per_minute": rate_limiter.limit,
            }, limit_headers + [(b"retry-after", str(retry_after).encode())])

        # Hand the principal to the dependency layer
        scope.setdefault("state", {})
        scope["state"]["service_principal"] = principal.user
        scope["state"]["service_key_fingerprint"] = fp

        redact = settings.SERVICE_KEY_REDACT_PERSONAL_DATA
        held_start: dict = {}
        body_chunks: list = []

        async def send_with_headers(message):
            mtype = message.get("type")

            if mtype == "http.response.start":
                headers = list(message.get("headers") or []) + limit_headers
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if redact and ctype.startswith(b"application/json"):
                    # Hold the start message until the full body is redacted,
                    # so Content-Length can be recalculated.
                    held_start.update(message)
                    held_start["headers"] = headers
                    return
                message["headers"] = headers
                return await send(message)

            if mtype == "http.response.body" and held_start:
                body_chunks.append(message.get("body", b""))
                if message.get("more_body", False):
                    return
                raw = b"".join(body_chunks)
                try:
                    payload = json.dumps(
                        redact_personal_data(json.loads(raw)), default=str
                    ).encode("utf-8")
                except (ValueError, TypeError):
                    payload = raw                      # not valid JSON: pass through unchanged
                headers = [(k, v) for k, v in held_start["headers"] if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(payload)).encode()))
                held_start["headers"] = headers
                await send(held_start)
                return await send({"type": "http.response.body", "body": payload})

            return await send(message)

        return await self.app(scope, receive, send_with_headers)


__all__ = ["ServiceKeyMiddleware"]
