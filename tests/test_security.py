"""Validation, rate-limiter and security-header unit tests."""

from msds.rate_limit import RateLimiter
from msds.services import validation


def test_rate_limiter_blocks_after_limit():
    rl = RateLimiter()
    assert all(rl.allow("k", 3, 60) for _ in range(3))
    assert rl.allow("k", 3, 60) is False


def test_rate_limiter_isolated_per_key():
    rl = RateLimiter()
    assert rl.allow("a", 1, 60)
    assert rl.allow("b", 1, 60)
    assert rl.allow("a", 1, 60) is False


def test_contact_parsing():
    assert validation.parse_contact("User@Example.COM") == ("email", "user@example.com")
    assert validation.parse_contact("+265 999 123 456") == ("phone", "+265999123456")
    assert validation.parse_contact("not-an-email") == (None, None)


def test_username_and_name():
    assert validation.valid_username("farmer_01")
    assert not validation.valid_username("bad username!")
    assert validation.valid_full_name("John Phiri Jr.")


def test_security_headers_present(client):
    resp = client.get("/login")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "unsafe-inline" not in resp.headers["Content-Security-Policy"]
    assert "jsdelivr" not in resp.headers["Content-Security-Policy"]


def test_health_reports_model_state(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["model_loaded"] is True


def test_open_redirect_blocked(client):
    resp = client.get("/set_language/en?next=https://evil.example.com")
    assert resp.status_code == 302
    assert "evil.example.com" not in resp.headers["Location"]
