"""Instamart integration surface.

Method names deliberately mirror Swiggy's real Instamart MCP tools
(`search_products`, `update_cart`, `get_cart` — see
https://mcp.swiggy.com/builders/docs/reference/instamart/, 16 tools at
POST mcp.swiggy.com/im, OAuth 2.1 + PKCE). No public sandbox exists without
production access, so `LocalInstamartSimulator` implements this contract
against the local catalog today. Once Builders Club access is granted, a
`SwiggyMCPClient` with the same method signatures (making real MCP tool
calls over the authenticated endpoint) replaces it here — nothing else in
the pipeline changes.
"""

from abc import ABC, abstractmethod
from typing import Optional

from .catalog import match_product


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
    def __init__(self):
        self._cart: list[dict] = []

    def search_products(self, product_name: str) -> tuple[Optional[dict], Optional[str]]:
        return match_product(product_name)

    def update_cart(self, catalog_item: dict, quantity: str) -> dict:
        entry = {
            "product_name": catalog_item["product_name"],
            "category": catalog_item["category"],
            "price_inr": catalog_item["price_inr"],
            "quantity": quantity,
        }
        self._cart.append(entry)
        return entry

    def get_cart(self) -> list[dict]:
        return self._cart
