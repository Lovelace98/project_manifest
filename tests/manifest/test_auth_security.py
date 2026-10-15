import pytest
from starlette.testclient import TestClient
from app import app
from auth import clear_rate_limits
from notion_service import clear_manifest_cache

@pytest.fixture
def client():
    clear_rate_limits()
    clear_manifest_cache()
    return TestClient(app, follow_redirects=False)

def test_health_check(client):
    rv = client.get('/health')
    assert rv.status_code == 200
    assert rv.json()["status"] == "healthy"

def test_unauthorized_redirects_to_login(client):
    rv = client.get('/')
    assert rv.status_code == 303
    assert "/login" in rv.headers["location"]

def test_unauthorized_api_returns_401(client):
    rv = client.get('/api/notion/status')
    assert rv.status_code == 401
    assert "Authentication required" in rv.json()["error"]

def test_login_invalid_credentials(client):
    rv = client.post('/login', data={'username': 'admin', 'password': 'wrongpassword'}, follow_redirects=True)
    assert rv.status_code == 401
    assert b"Invalid username or password" in rv.content

def test_login_valid_credentials(client):
    rv = client.post('/login', data={'username': 'admin', 'password': 'manifest2026!'}, follow_redirects=True)
    assert rv.status_code == 200
    assert b"Manifest Batch Sync" in rv.content or b"Notion Manifest Batch" in rv.content

def test_login_rate_limiting():
    clear_rate_limits()
    client = TestClient(app, client=('192.168.1.100', 50000))
    # Attempt 5 failed logins
    for _ in range(5):
        client.post('/login', data={'username': 'admin', 'password': 'bad'})
    
    # 6th attempt should be rate limited
    rv = client.post('/login', data={'username': 'admin', 'password': 'bad'})
    assert rv.status_code == 429
    assert b"Too many login attempts" in rv.content

def test_logout(client):
    # Log in first
    client.post('/login', data={'username': 'admin', 'password': 'manifest2026!'})
    # Then logout
    rv = client.get('/logout', follow_redirects=True)
    assert rv.status_code == 200
    assert b"Sign In" in rv.content
    
    # Subsequent access to / should redirect back to login
    rv2 = client.get('/')
    assert rv2.status_code == 303
