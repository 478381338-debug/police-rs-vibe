"""轻量安全中间件：安全响应头 + 进程内限流。

不引入 Redis 或额外依赖，适合单机部署。多副本部署时应把限流移到
Nginx/网关或 Redis，否则每个副本各自计数。
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

TRUST_PROXY = os.getenv("POLICE_TRUST_PROXY", "0").strip().lower() in {"1", "true", "yes", "on"}
HSTS = os.getenv("POLICE_HSTS", "0").strip().lower() in {"1", "true", "yes", "on"}

LOGIN_LIMIT = int(os.getenv("POLICE_RATE_LOGIN_PER_MIN", "10"))
API_LIMIT = int(os.getenv("POLICE_RATE_API_PER_MIN", "300"))
EXPENSIVE_LIMIT = int(os.getenv("POLICE_RATE_EXPENSIVE_PER_MIN", "30"))
REFRESH_LIMIT = int(os.getenv("POLICE_RATE_REFRESH_PER_5MIN", "6"))

CSP = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; "
    "img-src 'self' data: blob: https:; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self' https:; font-src 'self' data:; "
    "worker-src 'self' blob:; child-src 'self' blob:; manifest-src 'self'"
)

_RATE_LOCK = threading.Lock()
_HITS: dict[tuple[str, str], deque[float]] = {}
_LAST_SWEEP = 0.0


def _client_ip(request: Request) -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rule(method: str, path: str) -> tuple[str, int, int]:
    if method == "POST" and path == "/api/auth/login":
        return ("login", LOGIN_LIMIT, 60)
    if method == "POST" and (path.endswith("/arrival-circle") or path.endswith("/drone-plan")):
        return ("expensive", EXPENSIVE_LIMIT, 60)
    if method == "POST" and path in {"/api/refresh/start", "/api/imagery/watch/refresh"}:
        return ("refresh", REFRESH_LIMIT, 300)
    if path.startswith("/api/"):
        return ("api", API_LIMIT, 60)
    return ("", 0, 0)


def _allow(key: tuple[str, str], limit: int, window: int) -> tuple[bool, int]:
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _HITS.setdefault(key, deque())
        while bucket and now - bucket[0] >= window:
            bucket.popleft()
        if len(bucket) >= limit:
            retry = max(1, int(window - (now - bucket[0])) + 1)
            return False, retry
        bucket.append(now)
    return True, 0


def _sweep(force: bool = False) -> None:
    global _LAST_SWEEP
    now = time.monotonic()
    if not force and now - _LAST_SWEEP < 300:
        return
    _LAST_SWEEP = now
    with _RATE_LOCK:
        stale = []
        for key, bucket in _HITS.items():
            while bucket and now - bucket[0] >= 300:
                bucket.popleft()
            if not bucket:
                stale.append(key)
        for key in stale:
            _HITS.pop(key, None)


def _headers(request: Request) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(self), camera=(self), microphone=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Content-Security-Policy": CSP,
    }
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if HSTS and (request.url.scheme == "https" or forwarded_proto == "https"):
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for key, value in _headers(request).items():
            response.headers.setdefault(key, value)
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        bucket, limit, window = _rule(request.method, request.url.path)
        if limit:
            _sweep()
            allowed, retry = _allow((_client_ip(request), bucket), limit, window)
            if not allowed:
                return JSONResponse(
                    {"detail": "请求过于频繁，请稍后再试"},
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)