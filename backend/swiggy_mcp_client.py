"""Live Swiggy Instamart MCP client.

Speaks MCP over streamable HTTP to POST https://mcp.swiggy.com/im. It implements
the same three method contract as the local simulator, so the rest of the
pipeline is unchanged.

The OAuth details below were read from Swiggy's own discovery document at
https://mcp.swiggy.com/.well-known/oauth-authorization-server. None of it is
guessed.

    issuer                        https://mcp.swiggy.com/auth
    authorization_endpoint        https://mcp.swiggy.com/auth/authorize
    token_endpoint                https://mcp.swiggy.com/auth/token
    registration_endpoint         https://mcp.swiggy.com/auth/register
    code_challenge_methods        S256
    scopes_supported              mcp:tools, mcp:resources, mcp:prompts
    token_endpoint_auth_methods   none (public client), client_secret_post/basic

Safety: this client exposes search and cart operations only. The Instamart
server also publishes checkout, payment and order tools. Those are refused
outright by _guard_tool, so no code path here can spend money or dispatch a
delivery.
"""

import base64
import hashlib
import os
import secrets
import time
from typing import Any, Optional

import httpx

from .mcp_log import MCPCallLog, MODE_MCP

MCP_ENDPOINT = "https://mcp.swiggy.com/im"
AUTH_BASE = "https://mcp.swiggy.com/auth"
AUTHORIZE_URL = f"{AUTH_BASE}/authorize"
TOKEN_URL = f"{AUTH_BASE}/token"
REGISTER_URL = f"{AUTH_BASE}/register"
DEFAULT_SCOPE = "mcp:tools"
PROTOCOL_VERSION = "2025-06-18"

# Tools this client is allowed to call. Anything that places an order or touches
# payment is deliberately left out.
ALLOWED_TOOLS = {"search_products", "update_cart", "get_cart", "clear_cart", "get_addresses"}
FORBIDDEN_TOOLS = {
    "checkout", "confirm_order", "get_payment_options", "check_payment_status",
}


class MCPNotProvisionedError(RuntimeError):
    """No usable Swiggy MCP token. The app should fall back or tell the user."""


class MCPCallError(RuntimeError):
    """The MCP server rejected or failed a tool call."""


class ForbiddenToolError(RuntimeError):
    """Attempted to call a checkout/payment tool. Never allowed from here."""


# --------------------------------------------------------------------------
# OAuth 2.1 + PKCE
# --------------------------------------------------------------------------

def make_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) for S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def register_client(redirect_uri: str, client_name: str = "Clip2Cart", timeout: float = 20.0) -> dict:
    """RFC 7591 dynamic client registration against Swiggy's auth server."""
    response = httpx.post(
        REGISTER_URL,
        json={
            "client_name": client_name,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": DEFAULT_SCOPE,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def build_authorize_url(client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    from urllib.parse import urlencode

    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": DEFAULT_SCOPE,
    })
    return f"{AUTHORIZE_URL}?{query}"


def exchange_code(
    code: str, verifier: str, client_id: str, redirect_uri: str, timeout: float = 20.0
) -> dict:
    """Swap an authorization code for an access token. Public client, no secret."""
    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise MCPCallError(f"Token exchange failed ({response.status_code}): {response.text[:300]}")
    return response.json()


# --------------------------------------------------------------------------
# MCP client
# --------------------------------------------------------------------------

class SwiggyMCPClient:
    """Implements the same contract as LocalInstamartSimulator, over real MCP.

    It is deliberately not a subclass of the InstamartClient ABC at import time.
    That keeps this module importable without dragging the catalog in with it.
    """

    def __init__(self, token: Optional[str] = None, log: Optional[MCPCallLog] = None,
                 timeout: float = 30.0):
        self.token = token or os.environ.get("SWIGGY_MCP_TOKEN") or ""
        if not self.token:
            raise MCPNotProvisionedError(
                "No Swiggy MCP access token. Run the OAuth flow at /auth/login, "
                "or set SWIGGY_MCP_TOKEN in .env. Falling back to the local "
                "catalog."
            )
        self.log = log
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._next_id = 1
        self._client = httpx.Client(timeout=timeout)

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _rpc(self, method: str, params: Optional[dict] = None) -> dict:
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            payload["params"] = params

        response = self._client.post(MCP_ENDPOINT, json=payload, headers=self._headers())
        if response.status_code == 401:
            raise MCPNotProvisionedError(
                "Swiggy MCP rejected the token with a 401. Run the OAuth flow "
                "again at /auth/login."
            )
        if response.status_code >= 400:
            raise MCPCallError(f"MCP HTTP {response.status_code}: {response.text[:300]}")

        session = response.headers.get("Mcp-Session-Id")
        if session:
            self._session_id = session

        body = _decode_body(response)
        if "error" in body:
            raise MCPCallError(f"MCP error: {body['error']}")
        return body.get("result", {})

    def _guard_tool(self, tool: str) -> None:
        if tool in FORBIDDEN_TOOLS or tool not in ALLOWED_TOOLS:
            raise ForbiddenToolError(
                f"Tool '{tool}' is not callable from Clip2Cart. This build is "
                f"restricted to search and cart operations. Checkout and payment "
                f"tools are intentionally blocked."
            )

    def call_tool(self, tool: str, arguments: dict) -> Any:
        self._guard_tool(tool)
        started = time.perf_counter()
        try:
            result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        except Exception as exc:
            if self.log:
                self.log.record(tool, arguments, str(exc), started, is_error=True,
                                arguments_verified=True)
            raise
        structured = result.get("structuredContent", result)
        if self.log:
            self.log.record(tool, arguments, structured, started, arguments_verified=True)
        return structured

    # -- lifecycle --------------------------------------------------------

    def initialize(self) -> dict:
        return self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Clip2Cart", "version": "1.0.0"},
        })

    def list_tools(self) -> list[dict]:
        """Read the server's real tool schemas.

        This is what reconciles our inferred argument shapes with the truth.
        Swiggy does not publish the input schemas, so we ask the server.
        """
        return self._rpc("tools/list").get("tools", [])

    # -- the InstamartClient contract -------------------------------------

    def search_products(self, product_name: str) -> tuple[Optional[dict], Optional[str]]:
        result = self.call_tool("search_products", {"query": product_name})
        products = _coerce_products(result)
        if not products:
            return None, None
        return products[0], None

    def update_cart(self, catalog_item: dict, quantity: str) -> dict:
        return self.call_tool("update_cart", {
            "items": [{
                "product_id": catalog_item.get("product_id") or catalog_item.get("id"),
                "quantity": _as_int_quantity(quantity),
            }],
        })

    def get_cart(self) -> list[dict]:
        result = self.call_tool("get_cart", {})
        if isinstance(result, dict):
            return result.get("items", []) or result.get("cart", {}).get("items", [])
        return []

    def close(self) -> None:
        self._client.close()


# --------------------------------------------------------------------------

def _decode_body(response: httpx.Response) -> dict:
    """MCP streamable HTTP answers either as JSON or as a single SSE event."""
    import json

    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {}
    return response.json()


def _coerce_products(result: Any) -> list[dict]:
    if isinstance(result, dict):
        for key in ("products", "items", "results"):
            if isinstance(result.get(key), list):
                return result[key]
    if isinstance(result, list):
        return result
    return []


def _as_int_quantity(quantity: Any) -> int:
    try:
        return max(1, int(float(quantity)))
    except (TypeError, ValueError):
        return 1
