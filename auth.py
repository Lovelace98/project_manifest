import os
import time
import secrets
from functools import wraps
from flask import session, request, redirect, url_for, jsonify
from werkzeug.security import check_password_hash

_rate_limit_records = {}  # (ip, action) -> list of timestamp floats

def is_rate_limited(ip, action, limit=5, window_seconds=60):
    """
    In-memory sliding-window IP rate limiter.
    Limits `action` from `ip` to `limit` requests per `window_seconds`.
    """
    now = time.time()
    key = (ip, action)
    records = _rate_limit_records.get(key, [])
    
    # Filter out entries outside the window
    valid_records = [t for t in records if now - t < window_seconds]
    if len(valid_records) >= limit:
        _rate_limit_records[key] = valid_records
        return True
        
    valid_records.append(now)
    _rate_limit_records[key] = valid_records
    return False

def clear_rate_limits():
    """Reset all rate limit tracking (useful for testing)."""
    global _rate_limit_records
    _rate_limit_records = {}

def verify_credentials(username, password):
    """Verify submitted credentials against .env."""
    expected_user = os.getenv("ADMIN_USERNAME", "admin").strip()
    expected_pass = os.getenv("ADMIN_PASSWORD", "manifest2026!")
    expected_hash = os.getenv("ADMIN_PASSWORD_HASH")
    
    if not username or not password:
        return False
        
    if not secrets.compare_digest(username.strip(), expected_user):
        return False
        
    if expected_hash:
        return check_password_hash(expected_hash, password)
        
    return secrets.compare_digest(password, expected_pass)

def login_required(f):
    """Decorator ensuring valid user session before accessing views or APIs."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Authentication required. Please log in.'}), 401
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function
