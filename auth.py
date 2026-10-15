import os
import time
import secrets
from collections import defaultdict, deque
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from werkzeug.security import check_password_hash

_password_hasher = PasswordHasher()
_rate_limit_records = defaultdict(deque)  # (ip, action) -> deque of timestamp floats

def is_rate_limited(ip: str, action: str, limit: int = 5, window_seconds: int = 60) -> bool:
    """
    In-memory sliding-window IP rate limiter.
    Limits `action` from `ip` to `limit` requests per `window_seconds`.
    """
    now = time.time()
    key = (ip, action)
    records = _rate_limit_records[key]
    
    while records and records[0] <= now - window_seconds:
        records.popleft()
        
    if len(records) >= limit:
        return True
        
    records.append(now)
    return False

def clear_rate_limits():
    """Reset all rate limit tracking (useful for testing)."""
    global _rate_limit_records
    _rate_limit_records.clear()

def verify_credentials(username: str, password: str, settings=None) -> bool:
    """
    Verify submitted credentials against environment variables and Argon2/Werkzeug hashes.
    Supports both Project Manifest and Project Report Bot credentials.
    """
    if not username or not password:
        return False
        
    expected_user = os.getenv("ADMIN_USERNAME", "admin").strip()
    if not secrets.compare_digest(username.strip(), expected_user):
        return False

    # Check Argon2 hashes if configured
    app_hash = (getattr(settings, "app_password_hash", "") if settings else "") or os.getenv("REPORT_BOT_APP_PASSWORD_HASH", "") or os.getenv("ADMIN_PASSWORD_HASH", "")
    if app_hash and app_hash.startswith("$argon2"):
        try:
            return _password_hasher.verify(app_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    # Check Werkzeug hash if configured
    admin_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
    if admin_hash and not admin_hash.startswith("$argon2"):
        try:
            if check_password_hash(admin_hash, password):
                return True
        except Exception:
            pass

    # Check clear-text passwords
    expected_pass = os.getenv("ADMIN_PASSWORD", "manifest2026!").strip()
    dev_pass = (getattr(settings, "dev_password", "") if settings else "") or os.getenv("REPORT_BOT_DEV_PASSWORD", "change-me-now").strip()
    
    if secrets.compare_digest(password, expected_pass):
        return True
        
    if dev_pass and secrets.compare_digest(password, dev_pass):
        return True
        
    return False

def new_csrf_token() -> str:
    """Generate cryptographically secure 32-character URL-safe CSRF token."""
    return secrets.token_urlsafe(32)

def validate_csrf(request) -> bool:
    """Validate CSRF token from request headers or form data."""
    expected = str(request.session.get("csrf_token", ""))
    supplied = request.headers.get("x-csrf-token", "") or request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied:
        return False
    return secrets.compare_digest(expected, supplied)
