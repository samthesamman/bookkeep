"""Lightweight in-process rate limiting for sensitive endpoints.

Single-instance deployment only (state lives in this process). Intended for the
auth endpoints to blunt credential-stuffing / brute-force attempts from the
internet. Keyed by client IP with a sliding window of recent hits; a successful
login clears the caller's bucket so a legitimate user is never locked out by
their own earlier typos.
"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(self, max_hits: int, window_seconds: int):
        self.max_hits = max_hits
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str) -> None:
        """Record a hit for ``key``; raise 429 if it exceeds the window budget."""
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            hits = [t for t in self._hits[key] if t > cutoff]
            if len(hits) >= self.max_hits:
                self._hits[key] = hits
                retry_after = max(int(hits[0] + self.window - now) + 1, 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many attempts. Try again later.",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 10_000:
                stale = [k for k, v in self._hits.items() if not any(t > cutoff for t in v)]
                for k in stale:
                    del self._hits[k]

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_ip(request: Request) -> str:
    """Best-effort client IP. Honors X-Forwarded-For (first entry) so that a
    single reverse proxy in front of the app doesn't collapse every client into
    one bucket. Deploy the app only behind a trusted proxy."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


# 10 failed attempts per 5 minutes per IP before the login endpoint starts
# returning 429s. A successful login resets the bucket (see auth router).
login_limiter = SlidingWindowLimiter(max_hits=10, window_seconds=300)


def login_rate_limit(request: Request) -> str:
    """FastAPI dependency: throttle repeated login attempts by IP.

    Returns the resolved client IP so the endpoint can clear the bucket on a
    successful authentication.
    """
    ip = client_ip(request)
    login_limiter.check(f"login:{ip}")
    return ip
