from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status

from report_bot.config import Settings

_password_hasher = PasswordHasher()


class LoginThrottle:
    def __init__(self, limit: int = 5, window_seconds: int = 900):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.RLock()

    def _trim(self, key: str, now: float) -> deque[float]:
        attempts = self._attempts[key]
        while attempts and attempts[0] <= now - self.window_seconds:
            attempts.popleft()
        return attempts

    def allowed(self, key: str) -> bool:
        with self._lock:
            return len(self._trim(key, time.time())) < self.limit

    def failure(self, key: str) -> None:
        with self._lock:
            self._trim(key, time.time()).append(time.time())

    def success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def password_matches(candidate: str, settings: Settings) -> bool:
    if settings.app_password_hash:
        try:
            return _password_hasher.verify(settings.app_password_hash, candidate)
        except (VerifyMismatchError, InvalidHashError):
            return False
    if settings.environment.lower() == "production":
        return False
    return secrets.compare_digest(candidate, settings.dev_password)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def require_session(request: Request, settings: Settings) -> None:
    session = request.session
    if not session.get("authenticated"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    now = int(time.time())
    issued_at = int(session.get("issued_at", 0))
    last_seen = int(session.get("last_seen", 0))
    if (
        not issued_at
        or now - issued_at > settings.session_absolute_seconds
        or not last_seen
        or now - last_seen > settings.session_idle_seconds
    ):
        session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    session["last_seen"] = now


def validate_csrf(request: Request) -> None:
    expected = str(request.session.get("csrf_token", ""))
    supplied = request.headers.get("x-csrf-token", "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
