"""The redirect URI allowlist preflight.

Swiggy allowlists redirect URIs by exact domain and refuses everything else at
the authorize step. These tests pin the two things that matter: an explicit
refusal is reported before we send anyone anywhere, and a check that simply
failed is never mistaken for a refusal.

No test here touches the network.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main, swiggy_mcp_client as C
from backend.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def prod_uri(monkeypatch):
    monkeypatch.setenv("SWIGGY_MCP_REDIRECT_URI", "https://clip2cart.onrender.com/auth/callback")
    monkeypatch.delenv("SWIGGY_MCP_CLIENT_ID", raising=False)


def _answer(monkeypatch, value):
    """Pin what Swiggy's allowlist check returns.

    Both routes import the helper from the client module at call time, so that
    is where it has to be replaced.
    """
    monkeypatch.setattr(C, "check_redirect_uri_whitelisted", lambda uri, **k: value)


# --- the check itself ---------------------------------------------------

def test_reports_refusal(monkeypatch):
    _answer(monkeypatch, False)
    body = client.get("/auth/preflight").json()
    assert body["whitelisted"] is False
    assert body["redirect_uri"] == "https://clip2cart.onrender.com/auth/callback"
    assert "allowlist" in body["detail"]


def test_reports_acceptance(monkeypatch):
    _answer(monkeypatch, True)
    body = client.get("/auth/preflight").json()
    assert body["whitelisted"] is True
    assert "will work" in body["detail"]


def test_unreachable_check_is_unknown_not_refusal(monkeypatch):
    _answer(monkeypatch, None)
    body = client.get("/auth/preflight").json()
    assert body["whitelisted"] is None
    assert "says nothing about the URI" in body["detail"]


# --- what the guard does to /auth/login ---------------------------------

def test_login_stops_before_registering_when_refused(monkeypatch):
    """The point of the guard: fail here, not after a client is registered."""
    registered = []
    _answer(monkeypatch, False)
    monkeypatch.setattr(C, "register_client", lambda *a, **k: registered.append(a) or {})

    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 409
    assert registered == []


def test_login_proceeds_when_allowlisted(monkeypatch):
    _answer(monkeypatch, True)
    monkeypatch.setattr(C, "register_client", lambda *a, **k: {"client_id": "cid-123"})

    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://mcp.swiggy.com/auth/authorize")


def test_login_proceeds_when_check_is_unavailable(monkeypatch):
    """An unreachable allowlist check must not block a flow that might work."""
    _answer(monkeypatch, None)
    monkeypatch.setattr(C, "register_client", lambda *a, **k: {"client_id": "cid-123"})

    assert client.get("/auth/login", follow_redirects=False).status_code == 307


def test_force_overrides_a_refusal(monkeypatch):
    _answer(monkeypatch, False)
    monkeypatch.setattr(C, "register_client", lambda *a, **k: {"client_id": "cid-123"})

    assert client.get("/auth/login?force=1", follow_redirects=False).status_code == 307


# --- the client_id fix this flow depends on -----------------------------

def test_registered_client_id_survives_for_the_token_exchange(monkeypatch):
    """A dynamically registered client_id must reach /auth/callback.

    The token exchange has to present the same client_id the code was issued
    to. Registering one and forgetting it fails that match.
    """
    _answer(monkeypatch, True)
    monkeypatch.setattr(C, "register_client", lambda *a, **k: {"client_id": "dynamic-abc"})

    client.get("/auth/login", follow_redirects=False)

    import os
    assert os.environ.get("SWIGGY_MCP_CLIENT_ID") == "dynamic-abc"
