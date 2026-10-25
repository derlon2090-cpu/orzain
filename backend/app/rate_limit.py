"""Atomic Redis fixed-window limits with a process-local development fallback."""
import hashlib
import os
import threading
import time

from fastapi import HTTPException, Request
from redis import Redis
from redis.exceptions import RedisError

_redis: Redis | None = None
_local_lock = threading.Lock()
_local_counts: dict[str, tuple[int, float]] = {}
_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return {n, redis.call('TTL', KEYS[1])}
"""


class RateLimitBackendUnavailable(Exception):
    pass


def _limiter() -> Redis | None:
    global _redis
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    if _redis is None:
        _redis = Redis.from_url(url, socket_connect_timeout=0.25, socket_timeout=0.25, decode_responses=True)
    return _redis


def _increment_local(key: str, window_seconds: int) -> tuple[int, int]:
    now = time.monotonic()
    with _local_lock:
        count, expires = _local_counts.get(key, (0, now + window_seconds))
        if expires <= now:
            count, expires = 0, now + window_seconds
        count += 1
        _local_counts[key] = (count, expires)
        if len(_local_counts) > 20_000:
            for item, (_, deadline) in list(_local_counts.items()):
                if deadline <= now:
                    _local_counts.pop(item, None)
        return count, max(1, int(expires - now))


def _hit(key: str, window_seconds: int) -> tuple[int, int]:
    client = _limiter()
    if client is not None:
        try:
            count, ttl = client.eval(_SCRIPT, 1, key, window_seconds)
            return int(count), max(1, int(ttl))
        except RedisError as exc:
            if os.getenv("APP_ENV", "development").lower() == "production":
                raise RateLimitBackendUnavailable from exc
    elif os.getenv("APP_ENV", "development").lower() == "production":
        raise RateLimitBackendUnavailable
    return _increment_local(key, window_seconds)


def _digest(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def enforce_rate_limits(request: Request, rules: list[tuple[str, str, int, int]]) -> None:
    """Rules are (scope, subject, limit, window_seconds); each key increments atomically."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    ip = forwarded or (request.client.host if request.client else "unknown")
    retry_after = 1
    exceeded = False
    for scope, subject, limit, window in rules:
        key = f"tradegen:rl:v1:{scope}:{_digest(subject)}"
        try:
            count, ttl = _hit(key, window)
        except RateLimitBackendUnavailable as exc:
            raise HTTPException(503, "Rate limiting is temporarily unavailable") from exc
        if count > limit:
            exceeded = True
            retry_after = max(retry_after, ttl)
    if exceeded:
        raise HTTPException(429, "Too many requests", headers={"Retry-After": str(retry_after)})


def limit_register(request: Request, email: str) -> None:
    enforce_rate_limits(request, [("register-ip-hour", _request_ip(request), 5, 3600)])


def limit_login(request: Request, email: str) -> None:
    enforce_rate_limits(request, [
        ("login-ip-minute", _request_ip(request), 10, 60),
        ("login-email-quarter", email, 5, 900),
    ])


def limit_resend(request: Request, email: str) -> None:
    enforce_rate_limits(request, [
        ("resend-ip-hour", _request_ip(request), 5, 3600),
        ("resend-email-hour", email, 3, 3600),
    ])


def limit_verify(request: Request, token: str) -> None:
    enforce_rate_limits(request, [("verify-ip-minute", _request_ip(request), 10, 60)])


def limit_password_reset(request: Request, *, completion: bool = False) -> None:
    limit, window = (10, 3600) if completion else (5, 3600)
    scope = "password-reset-complete-ip-hour" if completion else "password-reset-ip-hour"
    enforce_rate_limits(request, [(scope, _request_ip(request), limit, window)])


def limit_ai_interpretation(request: Request, user_id: str) -> None:
    enforce_rate_limits(request, [
        ("ai-interpret-user-minute", user_id, 20, 60),
        ("ai-interpret-ip-minute", _request_ip(request), 30, 60),
    ])


def _request_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")
