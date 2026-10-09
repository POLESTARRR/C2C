"""Live Swiggy Instamart MCP client.

Speaks MCP over streamable HTTP to POST {SWIGGY_MCP_HOST}/im using the shared
transport in mcp_transport. It implements the same three-method contract as the
local simulator, so the rest of the pipeline is unchanged.

Safety: this client exposes search and cart operations only. The Instamart
server also publishes checkout, payment and order tools. Those are refused
outright by _guard_tool, so no code path here can spend money or dispatch a
delivery.
"""

import os
from typing import Any, Optional

from .mcp_log import MODE_MCP
from .quantity import COUNT, MASS, UNITS, VOLUME
from .mcp_transport import (
    SwiggyMCPTransport,
    make_pkce_pair,
    register_client,
    check_redirect_uri_whitelisted,
    build_authorize_url,
    exchange_code,
    _auth_base,
    _as_data,
    MCPNotProvisionedError,
    MCPCallError,
    ForbiddenToolError,
)

ALLOWED_TOOLS = {"search_products", "update_cart", "get_cart", "clear_cart", "get_addresses"}
FORBIDDEN_TOOLS = {
    "checkout", "confirm_order", "get_payment_options", "check_payment_status",
}


class SwiggyMCPClient(SwiggyMCPTransport):
    """The Instamart client. Thin wrapper over the shared transport."""

    def __init__(self, token: Optional[str] = None, log=None, timeout: float = 30.0):
        super().__init__(
            server="im",
            allowed_tools=ALLOWED_TOOLS,
            forbidden_tools=FORBIDDEN_TOOLS,
            token=token,
            log=log,
            timeout=timeout,
        )
        self._address_id: Optional[str] = self._resolve_default_address()
        self._pending_items: list[dict] = []

    def clear_cart(self) -> None:
        """Empty the Instamart cart for the active address.

        Called at the start of every /process run so old items from previous
        runs do not accumulate. Sends update_cart with an empty items list to
        replace whatever was in the cart before.
        """
        if not self._address_id:
            return
        try:
            result = self.call_tool("update_cart", {
                "selectedAddressId": self._address_id,
                "items": [],
            })
            # Also clear any locally queued items from a previous partial run.
            self._pending_items.clear()
        except Exception:  # noqa: BLE001
            # If clearing fails, at least reset our local queue so we don't
            # double-send stale items from a prior partial run.
            self._pending_items.clear()

    # -- the InstamartClient contract -------------------------------------
    #
    # Reconcile against the live gateway (verified 8 Sep 2026):
    #   search_products  addressId (req), query (req), offset (opt)
    #   update_cart      selectedAddressId (req), items[] {spinId, skuId, quantity}
    #                     -- REPLACES the whole cart, so the client accumulates
    #                        every item and sends them together.
    #   get_cart         no arguments; items live under data.items
    #   get_addresses    data.addresses[].id  (the addressId to use below)

    def _resolve_default_address(self) -> Optional[str]:
        """Pick the user's default delivery address id for the session.

        Every Instamart search and cart call needs one. We take the most recent
        saved address (get_addresses sorts by last order date) and reuse it for
        the whole run rather than re-fetching per call.
        """
        try:
            result = self.call_tool("get_addresses", {})
        except Exception:  # noqa: BLE001
            return None
        data = _as_data(result)
        addresses = data.get("addresses") if isinstance(data, dict) else None
        if not addresses:
            return None
        first = addresses[0]
        self._address_line = str(first.get("addressLine") or "").strip()
        self._address_tag = str(first.get("addressTag") or first.get("addressCategory") or "").strip()
        return str(first.get("id") or first.get("addressId") or "").strip() or None

    def get_address_info(self) -> dict:
        """Return the address used for this session."""
        return {
            "id": self._address_id or "",
            "label": getattr(self, "_address_tag", ""),
            "address_line": getattr(self, "_address_line", ""),
        }

    def search_products(self, product_name: str) -> tuple[Optional[dict], Optional[str], float]:
        if not self._address_id:
            raise MCPCallError(
                "No delivery address on the Swiggy account. Instamart search "
                "needs an addressId from get_addresses before it will return "
                "products. Save a delivery address on Swiggy and reconnect."
            )
        result = self.call_tool("search_products", {
            "addressId": self._address_id,
            "query": product_name,
        })
        products = _coerce_products(result)
        if not products:
            return None, None, 0.0
        first = products[0]
        return _normalise_product(first), None, _extract_score(first)

    def update_cart(self, catalog_item: dict, quantity: str, units: int = 1) -> dict:
        if not self._address_id:
            raise MCPCallError(
                "No delivery address on the Swiggy account. Cannot update cart."
            )
        self._pending_items.append({
            "spinId": catalog_item.get("spinId") or catalog_item.get("product_id"),
            "skuId": catalog_item.get("skuId") or catalog_item.get("product_id"),
            "quantity": _as_int_quantity(quantity) if units == 1 else units,
        })
        # The live tool replaces the whole cart, so the accumulated list is the
        # truth for this run. get_cart() sends it in final form.
        return {"success": True, "queued": len(self._pending_items), "items": list(self._pending_items)}

    def get_cart(self) -> list[dict]:
        if self._pending_items and self._address_id:
            self.call_tool("update_cart", {
                "selectedAddressId": self._address_id,
                "items": self._pending_items,
            })
        result = self.call_tool("get_cart", {})
        data = _as_data(result)
        if isinstance(data, dict):
            for key in ("items", "cartItems", "products"):
                items = data.get(key)
                if isinstance(items, list):
                    return items
            cart = data.get("cart")
            if isinstance(cart, dict):
                for key in ("items", "cartItems"):
                    items = cart.get(key)
                    if isinstance(items, list):
                        return items
        return []


def _coerce_products(result: Any) -> list[dict]:
    data = _as_data(result)
    if isinstance(data, dict):
        for key in ("products", "items", "results", "productList"):
            if isinstance(data.get(key), list):
                return data[key]
    if isinstance(data, list):
        return data
    return []


def _normalise_product(p: dict) -> Optional[dict]:
    """Map one live search_products result into the catalog-shaped dict that
    the rest of the pipeline reads (pack_unit, base_name, price_inr, ...).

    Handles two response shapes:
      1. Flat:  {spinId, skuId, price: 56, ...}           (reference / mock)
      2. Nested: {displayName, variations: [{spinId, skuId,
         price: {mrp:81, offerPrice:56}, quantityDescription:"1 kg"}]}
         — the live Instamart response

    For nested shape, we pick the first variation and flatten its fields.
    """

    def first(d: dict, *keys: str) -> Any:
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
        return None

    # --- Flatten: if variations[] exists, extract spinId/skuId/price from it
    variations = p.get("variations") or []
    if variations:
        v0 = variations[0] if isinstance(variations[0], dict) else {}
        # Overwrite top-level identifiers with variation-level values
        for key in ("spinId", "spin_id", "skuId", "sku_id"):
            if v0.get(key):
                p[key] = v0[key]
        # Flatten nested price object → top-level price / mrp
        var_price = v0.get("price") or {}
        if isinstance(var_price, dict):
            if var_price.get("offerPrice") is not None:
                p["price"] = var_price["offerPrice"]
            if var_price.get("mrp") is not None:
                p["mrp"] = var_price["mrp"]
        elif var_price is not None:
            p["price"] = var_price
        # Pack label from quantityDescription (e.g. "1 kg", "250 g", "6 pcs")
        qdesc = str(v0.get("quantityDescription") or "").strip()
        if qdesc:
            p["quantityDescription"] = qdesc
            parsed_size, parsed_unit = _parse_pack_label(qdesc)
            if parsed_size is not None:
                p["pack_size"] = parsed_size
                p["pack_unit"] = parsed_unit or ""

    # --- Read identifiers (flat OR flattened from variations)
    spin_id = str(first(p, "spinId", "spin_id", "productId", "product_id") or "").strip()
    sku_id = str(first(p, "skuId", "sku_id", "sku", "product_code") or "").strip()
    if not spin_id:
        return None  # no way to add this to a cart

    name = str(first(p, "displayName", "display_name", "product_name", "productName",
                      "name", "title") or "").strip()
    base_name = str(first(p, "base_name", "baseName", "category_name") or "").strip() or name

    # Price: try several paths (flat number, nested object, mrp vs offerPrice).
    price_raw = first(p, "price_inr", "price", "mrp", "selling_price",
                      "discountedFinalPrice", "finalPrice")
    try:
        price_inr = float(price_raw)
    except (TypeError, ValueError):
        price_inr = 0.0

    # Pack size / unit: prefer explicit numeric fields; fall back to parsing
    # quantityDescription if nothing numeric was set during flattening.
    pack_size = first(p, "pack_size", "packSize", "quantity", "size")
    try:
        pack_size = float(pack_size)
    except (TypeError, ValueError):
        pack_size = None
    pack_unit = str(first(p, "pack_unit", "packUnit", "unit") or "").strip()

    # If pack_size still None but we have a text label, try parsing it now.
    if pack_size is None:
        qdesc = str(first(p, "quantityDescription") or "").strip()
        if qdesc:
            pack_size, pack_unit = _parse_pack_label(qdesc)

    return {
        "product_id": spin_id,
        "spinId": spin_id,
        "skuId": sku_id or spin_id,
        "product_name": name,
        "base_name": base_name,
        "price_inr": price_inr,
        "pack_size": pack_size,
        "pack_unit": pack_unit,
        "category": str(first(p, "category", "department") or "grocery"),
    }


def _parse_pack_label(label: str) -> tuple[Optional[float], str]:
    """Parse a Swiggy quantityDescription into (numeric_size, canonical_unit).

    The unit is normalised to the canonical form the quantity maths expects
    (g / ml / pc) so live pack_unit values line up with the local catalog:
      "1 kg"    → (1000.0, "g")
      "250 g"   → (250.0, "g")
      "6 pcs"   → (6.0, "pc")
      "500 ml"  → (500.0, "ml")
      "1.5 L"   → (1500.0, "ml")
      "1"       → (1.0, "pc")   — a bare count
      ""        → (None, "")
    """
    import re
    label = label.strip()
    if not label:
        return None, ""
    m = re.match(r"([\d.]+)\s*([a-zA-Z]+)?", label)
    if not m:
        return None, ""
    try:
        size = float(m.group(1))
    except ValueError:
        return None, ""
    unit = (m.group(2) or "").strip().lower()
    if unit:
        canonical, mult = UNITS.get(unit, (unit, 1.0))
        if mult != 1.0:
            size = size * mult
        return size, canonical
    # A bare number on a pack label is a count (e.g. "6" eggs, "2" pavs).
    return size, COUNT


def _extract_score(p: dict) -> float:
    """A nominal match score for the top hit. The server already ranked the
    results, so a present top hit is treated as a strong match unless the
    response carries an explicit relevance/confidence value."""
    for k in ("score", "relevance", "confidence", "match_score"):
        v = p.get(k)
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 100.0


def _as_int_quantity(quantity: Any) -> int:
    try:
        return max(1, int(float(quantity)))
    except (TypeError, ValueError):
        return 1


# Canonical entrypoint used by main._live_client to build this server's client.
SWIGGY_CLIENT = SwiggyMCPClient