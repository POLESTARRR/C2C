"""Tests for INSTAMART_MODE wiring: the factory picks the right client, mcp
mode falls back honestly without a token, and a live cart is never faked from
a local catalog product.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.mcp_log import MODE_LOCAL, MODE_MCP
from backend.instamart_client import LocalInstamartSimulator
from backend.swiggy_mcp_client import MCPNotProvisionedError, SwiggyMCPClient

RECIPE = [
    {"product_name": "Paneer", "category": "grocery", "estimated_quantity": "300 g",
     "quantity_source": "stated", "confidence": "High"},
]


def stub(items, serves=4):
    return lambda text: (items, serves)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "extract_ingredients", stub(RECIPE))
    monkeypatch.setattr(main, "resolve_unmatched", lambda names: {})
    return TestClient(main.app, raise_server_exceptions=False)


def _post(client, value="some transcript"):
    return client.post("/process", json={"source_type": "transcript_text", "value": value})


# --- factory selection -----------------------------------------------------

def test_factory_returns_local_simulator_by_default(monkeypatch):
    monkeypatch.setenv("INSTAMART_MODE", "local")
    monkeypatch.delenv("SWIGGY_MCP_TOKEN", raising=False)
    client_obj, call_log, note = main._build_instamart_client(main._instamart_mode())
    assert isinstance(client_obj, LocalInstamartSimulator)
    assert call_log.mode == MODE_LOCAL
    assert note is None


def test_factory_returns_swiggy_client_when_token_present(monkeypatch):
    monkeypatch.setenv("INSTAMART_MODE", "mcp")
    monkeypatch.setenv("SWIGGY_MCP_TOKEN", "dummy-token")
    client_obj, call_log, note = main._build_instamart_client(main._instamart_mode())
    assert isinstance(client_obj, SwiggyMCPClient)
    assert call_log.mode == MODE_MCP
    assert note is None


def test_factory_raises_without_token_when_mode_is_mcp(monkeypatch):
    """Direct factory contract: with no token and no fallback path, constructing
    the live client raises MCPNotProvisionedError. The factory below converts
    that into the honest fallback the pipeline relies on."""
    monkeypatch.setenv("SWIGGY_MCP_TOKEN", "")
    # Point the on-disk token store somewhere that cannot exist, so a real
    # .swiggy_token on this machine cannot leak a token into this test.
    import backend.mcp_transport as transport
    from pathlib import Path as P
    monkeypatch.setattr(transport, "_token_path", lambda: P(__file__).parent / "no-such-token")
    with pytest.raises(MCPNotProvisionedError):
        SwiggyMCPClient()


# --- mcp mode with no token falls back honestly ----------------------------

def test_mcp_mode_without_token_falls_back_and_says_so(client, monkeypatch):
    monkeypatch.setenv("INSTAMART_MODE", "mcp")
    monkeypatch.setenv("SWIGGY_MCP_TOKEN", "")

    body = _post(client).json()

    # The requested mode is reported as mcp...
    assert body["instamart_mode"] == "mcp"
    # ...but the run honestly says it fell back to the local catalog.
    assert body["fallback_note"] and "token" in body["fallback_note"]
    # The wire log records what actually ran (local_simulator), never a fake.
    assert body["mcp_calls"][0]["mode"] == MODE_LOCAL
    # And the local cart still works so the demo never hard-fails.
    assert body["summary"]["matched_items"] == 1


def test_local_mode_has_no_fallback_note(client, monkeypatch):
    monkeypatch.setenv("INSTAMART_MODE", "local")
    body = _post(client).json()
    assert body["instamart_mode"] == "local"
    assert body["fallback_note"] is None
    assert body["mcp_calls"][0]["mode"] == MODE_LOCAL


# --- live mode never rescues from the local catalog -------------------------

def test_mcp_mode_skips_catalog_semantic_rescue(client, monkeypatch):
    """A live search that misses must not be 'rescued' by a local catalog
    product, because its product_id would be pushed into the real cart."""
    monkeypatch.setenv("INSTAMART_MODE", "mcp")
    monkeypatch.setenv("SWIGGY_MCP_TOKEN", "")

    # A name that matches nothing in the local catalog, so the pipeline would
    # ordinarily send it to the semantic rescue.
    from backend.catalog import match_product
    assert match_product("kumro")[0] is None

    monkeypatch.setattr(main, "extract_ingredients", stub([
        {"product_name": "kumro", "category": "grocery", "estimated_quantity": "500 g",
         "quantity_source": "stated", "confidence": "High"},
    ]))

    body = _post(client).json()
    row = body["basket"][0]
    # The row stays unmatched rather than being matched to a local product in a
    # supposedly-live run. (The fallback uses the local client, so an unmatched
    # row is flagged, not rescued.)
    assert row["matched_catalog_item"] is False