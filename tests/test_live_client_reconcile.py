"""Tests for the live-gateway schema reconciliation.

The real MCP gateway requires an address for every Instamart call and keys cart
items by spinId/skuId. These tests stub the transport (_rpc) at the class level
so even the address resolution inside __init__ is hermetic, pinning the
argument building, field mapping and cart accumulation to the documented
schemas without a live token.

Verified shapes (Swiggy builder docs, 8 Sep 2026):
    get_addresses   -> data.addresses[].id
    search_products -> addressId (req), query (req)
    update_cart     -> selectedAddressId (req), items[] {spinId, skuId, quantity}
    get_cart        -> data.items[]
"""

import pytest

from backend.swiggy_mcp_client import SwiggyMCPClient

ADDRESS_ID = "addr_123"

SEARCH_RESULT = {
    "success": True,
    "data": {"products": [{
        "spinId": "spin_41",
        "skuId": "sku_77",
        "productName": "Amul Paneer 200g",
        "mrp": 95.0,
        "packSize": 200,
        "packUnit": "g",
    }]},
}


def _client(monkeypatch, responses, addresses=None):
    """Construct SwiggyMCPClient with a stubbed transport.

    The fake is installed on the class before __init__ runs, so the
    get_addresses call inside the constructor is served too. Returns the
    client and the list of (tool, arguments) calls it made.
    """
    calls = []

    def fake_rpc(self, method=None, params=None):
        tool = params["name"]
        calls.append((tool, params["arguments"]))
        return {"structuredContent": responses.get(tool, {})}

    monkeypatch.setattr(SwiggyMCPClient, "_rpc", fake_rpc)
    if addresses is not None:
        responses["get_addresses"] = {"success": True, "data": {"addresses": addresses}}
    responses.setdefault("get_addresses", {
        "success": True,
        "data": {"addresses": [{"id": ADDRESS_ID, "addressLine": "Home"}]},
    })
    client = SwiggyMCPClient(token="dummy")
    return client, calls


def test_client_resolves_default_address_on_init(monkeypatch):
    """Construction calls get_addresses and keeps the first address id."""
    client, calls = _client(monkeypatch, {})
    assert client._address_id == ADDRESS_ID
    assert calls[0][0] == "get_addresses"


def test_search_products_sends_address_id(monkeypatch):
    client, calls = _client(monkeypatch, {"search_products": SEARCH_RESULT})
    item, _sub, score = client.search_products("paneer")
    assert calls[1] == ("search_products", {"addressId": ADDRESS_ID, "query": "paneer"})
    assert item["product_id"] == "spin_41"
    assert item["spinId"] == "spin_41"
    assert item["skuId"] == "sku_77"
    assert item["price_inr"] == 95.0
    assert score == 100.0  # nominal: the server already ranked


def test_update_cart_accumulates_and_get_cart_flushes(monkeypatch):
    client, calls = _client(monkeypatch, {
        "search_products": SEARCH_RESULT,
        "update_cart": {"success": True},
        "get_cart": {"success": True, "data": {"items": []}},
    })
    item, _, _ = client.search_products("paneer")
    client.update_cart(item, "200 g", units=1)
    client.update_cart(item, "200 g", units=2)
    client.get_cart()

    updates = [c for c in calls if c[0] == "update_cart"]
    assert len(updates) == 1  # batched, not one per ingredient
    assert updates[0][1] == {
        "selectedAddressId": ADDRESS_ID,
        "items": [
            {"spinId": "spin_41", "skuId": "sku_77", "quantity": 1},
            {"spinId": "spin_41", "skuId": "sku_77", "quantity": 2},
        ],
    }


def test_get_cart_reads_items_under_data(monkeypatch):
    client, _calls = _client(monkeypatch, {
        "get_cart": {"success": True, "data": {"items": [{"spinId": "s1"}]}}})
    items = client.get_cart()
    assert items == [{"spinId": "s1"}]


def test_normalise_product_unwraps_envelope(monkeypatch):
    client, _calls = _client(monkeypatch, {"search_products": SEARCH_RESULT})
    item, _, _ = client.search_products("paneer")
    # product_id comes from spinId, name from productName, price from mrp.
    assert item["product_name"] == "Amul Paneer 200g"
    assert item["base_name"] == "Amul Paneer 200g"
    assert item["pack_size"] == 200.0
    assert item["pack_unit"] == "g"


def test_search_returns_none_when_no_products(monkeypatch):
    client, _calls = _client(monkeypatch, {
        "search_products": {"success": True, "data": {"products": []}}})
    item, sub, score = client.search_products("kumro")
    assert item is None and sub is None and score == 0.0


def test_no_address_gives_a_clear_error(monkeypatch):
    client, _calls = _client(monkeypatch, {}, addresses=[])
    assert client._address_id is None
    from backend.swiggy_mcp_client import MCPCallError

    with pytest.raises(MCPCallError, match="address"):
        client.search_products("paneer")


# --- live Instamart nesting: spinId/skuId/price live under variations[] ----

LIVE_SEARCH_RESULT = {
    "success": True,
    "data": {"products": [{
        "displayName": "Onion (Pyaaz)",
        "category": "Fruits & Vegetables",
        "variations": [{
            "spinId": "FG8WDTXSUN",
            "skuId": "XRLPFGZDL0",
            "quantityDescription": "1 kg",
            "price": {"mrp": 81, "offerPrice": 56},
        }],
    }]},
}


def test_normalise_product_reads_nested_variations(monkeypatch):
    """Live responses nest spinId/skuId/price under products[].variations[].

    This was the blocking bug: identifiers read at the product top level
    returned None for every live product, so nothing matched. The normaliser
    must flatten the first variation's fields.
    """
    client, _calls = _client(monkeypatch, {"search_products": LIVE_SEARCH_RESULT})
    item, _sub, score = client.search_products("onion")
    assert item is not None
    assert item["spinId"] == "FG8WDTXSUN"
    assert item["skuId"] == "XRLPFGZDL0"
    assert item["product_name"] == "Onion (Pyaaz)"
    # offerPrice wins over mrp; pack label canonicalised to the units maths use.
    assert item["price_inr"] == 56.0
    assert item["pack_size"] == 1000.0
    assert item["pack_unit"] == "g"
    assert item["product_id"] == "FG8WDTXSUN"
    assert score == 100.0


def test_normalise_product_multiple_variations_takes_first(monkeypatch):
    client, _calls = _client(monkeypatch, {"search_products": {
        "success": True, "data": {"products": [{
            "displayName": "Tomato",
            "variations": [
                {"spinId": "v1", "skuId": "s1", "quantityDescription": "500 g",
                 "price": {"mrp": 60, "offerPrice": 46}},
                {"spinId": "v2", "skuId": "s2", "quantityDescription": "1 kg",
                 "price": {"mrp": 110, "offerPrice": 90}},
            ],
        }]},
    }})
    item, _, _ = client.search_products("tomato")
    assert item["spinId"] == "v1"
    assert item["pack_size"] == 500.0
    assert item["pack_unit"] == "g"


def test_parse_pack_label_normalises_units():
    """quantityDescription units must land in canonical g/ml/pc, not raw
    'kg'/'L'/'pcs', or units_needed would compare 250 g against '250 g' and
    never agree, buying one pack of everything."""
    from backend.swiggy_mcp_client import _parse_pack_label
    assert _parse_pack_label("1 kg") == (1000.0, "g")
    assert _parse_pack_label("250 g") == (250.0, "g")
    assert _parse_pack_label("500 ml") == (500.0, "ml")
    assert _parse_pack_label("1.5 L") == (1500.0, "ml")
    assert _parse_pack_label("6 pcs") == (6.0, "pc")
    assert _parse_pack_label("2") == (2.0, "pc")
    assert _parse_pack_label("") == (None, "")


def test_pack_math_with_live_pack_label():
    """A live 1 kg pack and a 3 kg recipe requirement must buy 3 units."""
    from backend.quantity import Quantity, units_needed
    from backend.swiggy_mcp_client import _parse_pack_label

    pack_size, pack_unit = _parse_pack_label("1 kg")
    n = units_needed(Quantity(3000, "g"), pack_size, pack_unit, "atta")
    assert n == 3


def test_forbidden_tools_are_still_refused(monkeypatch):
    from backend.swiggy_mcp_client import ForbiddenToolError

    client, _calls = _client(monkeypatch, {})
    with pytest.raises(ForbiddenToolError):
        client.call_tool("checkout", {})
    with pytest.raises(ForbiddenToolError):
        client.call_tool("confirm_order", {})