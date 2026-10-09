"""Records every MCP operation as the JSON-RPC envelope it maps to.

The point of this module is auditability. Clip2Cart can run against a local
catalog, or against Swiggy's live Instamart MCP server.
Every simulated operation emits the exact ``tools/call`` payload that gets
POSTed to Swiggy's MCP endpoint in live mode, and the UI renders them verbatim.

Verified against Swiggy's live server on 19 Aug 2026:
  endpoint   POST https://mcp.swiggy.com/im, which answers 401 with
             WWW-Authenticate: Bearer
  auth       OAuth 2.1, PKCE S256, scopes mcp:tools, mcp:resources, mcp:prompts
  tools      search_products, update_cart, get_cart, out of 16 Instamart tools

Argument names are not published in Swiggy's public docs, so the shapes below
are our best reading of the tool descriptions. `arguments_verified` is False to
say so out loud. The MCP clients call `tools/list` on connect and reconcile
against the server's real schema rather than trusting these.
"""

import json
import os
import time
from typing import Any, Optional

JSONRPC_VERSION = "2.0"

# Which Swiggy server a call went to. Logged so the wire log always shows the
# exact endpoint that was hit.
SERVER_INSTAMART = "im"
SERVER_LOCAL = "local_simulator"


def _mcp_endpoint(server: str = SERVER_INSTAMART) -> str:
    """Mirrors the MCP clients' host so the logged endpoint always matches
    whichever host a real MCP run actually hit."""
    host = os.environ.get("SWIGGY_MCP_HOST") or "https://mcp.swiggy.com"
    if server == SERVER_LOCAL:
        return f"local://catalog"
    return f"{host}/{server}"

MODE_LOCAL = "local_simulator"
MODE_MCP = "swiggy_mcp"


class MCPCallLog:
    """Collects the JSON-RPC exchanges for one request."""

    def __init__(self, mode: str = MODE_LOCAL, server: str = SERVER_INSTAMART):
        self.mode = mode
        self.server = server
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
        server: Optional[str] = None,
    ) -> dict:
        """Append one tools/call exchange and return it."""
        call_id = self._allocate_id()
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2) if started_at else 0.0
        server = server or self.server

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
                    "content": [{"type": "text", "text": _as_text(result, tool, server)}],
                    "structuredContent": result,
                },
            }

        entry = {
            "seq": call_id,
            "endpoint": f"POST {_mcp_endpoint(server)}",
            "tool": tool,
            "server": server,
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


def _as_text(result: Any, tool: str = "", server: str = SERVER_INSTAMART) -> str:
    """MCP results carry a readable text block alongside the structured data."""
    if result is None:
        return "null"
    if isinstance(result, dict):
        if "products" in result or "results" in result:
            products = result.get("products") or result.get("results") or []
            if not products:
                return "No matching products found."
            head = products[0]
            name = head.get("name") or head.get("product_name") or "product"
            price = head.get("price") or head.get("price_inr")
            return f"{len(products)} result(s). Top: {name} at Rs.{price}"
        if "cart" in result:
            cart = result["cart"]
            return f"Cart has {len(cart.get('items', []))} item(s), subtotal Rs.{cart.get('subtotal', 0)}"
        if "item" in result:
            item = result["item"]
            return f"Added {item.get('quantity')} x {item.get('name')} to cart."
    return str(result)
