"""Records every Instamart operation as the MCP JSON-RPC envelope it maps to.

The point of this module is auditability. Clip2Cart currently runs against a
local catalog, so it would be easy to wave vaguely at what the real integration
might look like. Instead, every simulated operation emits the exact
`tools/call` payload that gets POSTed to Swiggy's MCP endpoint when
INSTAMART_MODE=mcp, and the UI renders them verbatim.

Verified against Swiggy's live server on 19 Aug 2026:
  endpoint   POST https://mcp.swiggy.com/im, which answers 401 with
             WWW-Authenticate: Bearer
  auth       OAuth 2.1, PKCE S256, scopes mcp:tools, mcp:resources, mcp:prompts
  tools      search_products, update_cart, get_cart, out of 16 Instamart tools

Argument names are not published in Swiggy's public docs, so the shapes below
are our best reading of the tool descriptions. `arguments_verified` is False to
say so out loud. SwiggyMCPClient calls `tools/list` on connect and reconciles
against the server's real schema rather than trusting these.
"""

import time
from typing import Any, Optional

MCP_ENDPOINT = "https://mcp.swiggy.com/im"
JSONRPC_VERSION = "2.0"

MODE_LOCAL = "local_simulator"
MODE_MCP = "swiggy_mcp"


class MCPCallLog:
    """Collects the JSON-RPC exchanges for one /process request."""

    def __init__(self, mode: str = MODE_LOCAL):
        self.mode = mode
        self.entries: list[dict] = []
        self._next_id = 1

    def _allocate_id(self) -> int:
        current = self._next_id
        self._next_id += 1
        return current

    def record(
        self,
        tool: str,
        arguments: dict,
        result: Any,
        started_at: Optional[float] = None,
        is_error: bool = False,
        arguments_verified: bool = False,
    ) -> dict:
        """Append one tools/call exchange and return it."""
        call_id = self._allocate_id()
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2) if started_at else 0.0

        request = {
            "jsonrpc": JSONRPC_VERSION,
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }

        if is_error:
            response = {
                "jsonrpc": JSONRPC_VERSION,
                "id": call_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": str(result)}],
                },
            }
        else:
            response = {
                "jsonrpc": JSONRPC_VERSION,
                "id": call_id,
                "result": {
                    "content": [{"type": "text", "text": _as_text(result)}],
                    "structuredContent": result,
                },
            }

        entry = {
            "seq": call_id,
            "endpoint": f"POST {MCP_ENDPOINT}",
            "tool": tool,
            "mode": self.mode,
            "latency_ms": elapsed_ms,
            "arguments_verified": arguments_verified,
            "request": request,
            "response": response,
        }
        self.entries.append(entry)
        return entry

    def as_list(self) -> list[dict]:
        return self.entries


def _as_text(result: Any) -> str:
    """MCP results carry a readable text block alongside the structured data."""
    if result is None:
        return "null"
    if isinstance(result, dict):
        if "products" in result:
            products = result["products"]
            if not products:
                return "No matching products found."
            head = products[0]
            return (
                f"{len(products)} product(s). Top result: "
                f"{head.get('name')} at Rs.{head.get('price')}"
            )
        if "cart" in result:
            cart = result["cart"]
            return f"Cart has {len(cart.get('items', []))} item(s), subtotal Rs.{cart.get('subtotal', 0)}"
        if "item" in result:
            item = result["item"]
            return f"Added {item.get('quantity')} x {item.get('name')} to cart."
    return str(result)
