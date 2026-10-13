import pytest
import os
from app import app
from auth import clear_rate_limits
from notion_service import clear_manifest_cache

@pytest.fixture
def client():
    app.config['TESTING'] = True
    clear_rate_limits()
    clear_manifest_cache()
    with app.test_client() as client:
        yield client

def test_health_check(client):
    rv = client.get('/health')
    assert rv.status_code == 200
    assert rv.get_json()["status"] == "healthy"

def test_unauthorized_redirects_to_login(client):
    rv = client.get('/')
    assert rv.status_code == 302
    assert "/login" in rv.location

def test_unauthorized_api_returns_401(client):
    rv = client.get('/api/notion/status')
    assert rv.status_code == 401
    assert "Authentication required" in rv.get_json()["error"]

def test_login_invalid_credentials(client):
    rv = client.post('/login', data={'username': 'admin', 'password': 'wrongpassword'}, follow_redirects=True)
    assert rv.status_code == 200
    assert b"Invalid username or password" in rv.data

def test_login_valid_credentials(client):
    rv = client.post('/login', data={'username': 'admin', 'password': 'manifest2026!'}, follow_redirects=True)
    assert rv.status_code == 200
    assert b"Manifest Batch Sync" in rv.data or b"Notion Manifest Batch" in rv.data

def test_login_rate_limiting(client):
    clear_rate_limits()
    # Attempt 5 failed logins
    for _ in range(5):
        client.post('/login', data={'username': 'admin', 'password': 'bad'}, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    
    # 6th attempt should be rate limited
    rv = client.post('/login', data={'username': 'admin', 'password': 'bad'}, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert rv.status_code == 429
    assert b"Too many login attempts" in rv.data

def test_logout(client):
    # Log in first
    client.post('/login', data={'username': 'admin', 'password': 'manifest2026!'})
    # Then logout
    rv = client.get('/logout', follow_redirects=True)
    assert rv.status_code == 200
    assert b"Sign In" in rv.data
    
    # Subsequent access to / should redirect back to login
    rv2 = client.get('/')
    assert rv2.status_code == 302
