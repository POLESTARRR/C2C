"""Instamart integration surface.

Method names deliberately mirror Swiggy's real Instamart MCP tools. Those are
`search_products`, `update_cart` and `get_cart`, three of the 16 tools served at
POST mcp.swiggy.com/im behind OAuth 2.1 with PKCE. Two implementations satisfy
this contract:

  LocalInstamartSimulator  runs against the bundled catalog and records the
                           MCP payload each operation maps to.
  SwiggyMCPClient          makes those same calls for real (swiggy_mcp_client).

Nothing upstream knows which one it is talking to. Not the transcript fetching,
not the LLM extraction, not the quantity maths. Switching is one environment
variable.
"""

import time
from abc import ABC, abstractmethod
from typing import Optional

from .catalog import match_product, packs_for
from .mcp_log import MCPCallLog


class InstamartClient(ABC):
    @abstractmethod
    def search_products(self, product_name: str) -> tuple[Optional[dict], Optional[str]]:
        """Returns (matched_catalog_item, suggested_substitute_name)."""

    @abstractmethod
    def update_cart(self, catalog_item: dict, quantity: str) -> dict:
        """Adds an item to the cart and returns the cart line entry."""

    @abstractmethod
    def get_cart(self) -> list[dict]:
        """Returns the current cart contents."""


class LocalInstamartSimulator(InstamartClient):
    """The stand in that runs on the bundled catalog, with MCP shaped logging.

    Every method records the `tools/call` envelope it corresponds to, so the UI
    can show exactly what would go over the wire against the real server.
    """

    def __init__(self, log: Optional[MCPCallLog] = None):
        self._cart: list[dict] = []
        self.log = log

    def search_products(
        self, product_name: str, required: Optional[float] = None
    ) -> tuple[Optional[dict], Optional[str], float]:
        started = time.perf_counter()
        item, substitute, score = match_product(product_name, required=required)

        if self.log:
            # A real product search returns every pack variant, not just the one
            # we end up adding. Mirror that so the log matches the cart.
            products = []
            if item:
                for variant in packs_for(item["base_name"]):
                    products.append({
                        "product_id": variant["product_id"],
                        "name": variant["product_name"],
                        "brand": variant.get("brand"),
                        "price": variant["price_inr"],
                        "pack": f"{_trim(variant['pack_size'])}{variant['pack_unit']}",
                        "in_stock": True,
                    })
            self.log.record(
                "search_products",
                {"query": product_name},
                {"products": products, "match_score": round(score, 1)},
                started,
            )
        return item, substitute, score

    def update_cart(self, catalog_item: dict, quantity: str, units: int = 1) -> dict:
        started = time.perf_counter()
        entry = {
            "product_id": catalog_item["product_id"],
            "product_name": catalog_item["product_name"],
            "category": catalog_item["category"],
            "price_inr": catalog_item["price_inr"],
            "units": units,
            "line_total_inr": round(catalog_item["price_inr"] * units, 2),
            "recipe_quantity": quantity,
        }
        self._cart.append(entry)

        if self.log:
            self.log.record(
                "update_cart",
                {"items": [{"product_id": catalog_item["product_id"], "quantity": units}]},
                {
                    "item": {
                        "product_id": catalog_item["product_id"],
                        "name": catalog_item["product_name"],
                        "quantity": units,
                    },
                    "cart_size": len(self._cart),
                },
                started,
            )
        return entry

    def get_cart(self) -> list[dict]:
        started = time.perf_counter()
        if self.log:
            subtotal = round(sum(line["line_total_inr"] for line in self._cart), 2)
            self.log.record(
                "get_cart",
                {},
                {
                    "cart": {
                        "items": [
                            {
                                "product_id": line["product_id"],
                                "name": line["product_name"],
                                "quantity": line["units"],
                                "line_total": line["line_total_inr"],
                            }
                            for line in self._cart
                        ],
                        "subtotal": subtotal,
                        "currency": "INR",
                    }
                },
                started,
            )
        return self._cart


def _trim(value) -> str:
    """1000.0 -> '1000', 0.5 -> '0.5'"""
    return f"{value:g}"
