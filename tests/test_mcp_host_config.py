"""SWIGGY_MCP_HOST / SWIGGY_MCP_AUTH_HOST overrides.

Staging credentials point at a different host than production
(mcp-staging.swiggy.com vs mcp.swiggy.com per Swiggy's docs). These tests pin
the default (unset env -> production) and the override, for both the OAuth
URL builder and the call-log's displayed endpoint, so the two never drift
apart again the way they did before mcp_log.py had its own copy of the host.

No test here touches the network.
"""

from backend import swiggy_mcp_client as C
from backend.mcp_log import MCPCallLog, MODE_MCP


def test_authorize_url_defaults_to_production(monkeypatch):
    monkeypatch.delenv("SWIGGY_MCP_AUTH_HOST", raising=False)
    url = C.build_authorize_url("cid", "http://localhost:8000/auth/callback", "chal", "state")
    assert url.startswith("https://mcp.swiggy.com/auth/authorize?")


def test_authorize_url_honors_override(monkeypatch):
    monkeypatch.setenv("SWIGGY_MCP_AUTH_HOST", "https://mcp-staging.swiggy.com/auth")
    url = C.build_authorize_url("cid", "http://localhost:8000/auth/callback", "chal", "state")
    assert url.startswith("https://mcp-staging.swiggy.com/auth/authorize?")


def test_call_log_endpoint_defaults_to_production(monkeypatch):
    monkeypatch.delenv("SWIGGY_MCP_HOST", raising=False)
    entry = MCPCallLog(mode=MODE_MCP).record("search_products", {"query": "milk"}, {"products": []})
    assert entry["endpoint"] == "POST https://mcp.swiggy.com/im"


def test_call_log_endpoint_honors_override(monkeypatch):
    monkeypatch.setenv("SWIGGY_MCP_HOST", "https://mcp-staging.swiggy.com")
    entry = MCPCallLog(mode=MODE_MCP).record("search_products", {"query": "milk"}, {"products": []})
    assert entry["endpoint"] == "POST https://mcp-staging.swiggy.com/im"
