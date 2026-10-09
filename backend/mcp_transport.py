"""Shared MCP transport + authentication for Swiggy's Instamart MCP server.

The Instamart server speaks MCP over streamable HTTP behind OAuth 2.1 + PKCE.
This module owns the JSON-RPC plumbing, token lookup, session-id handling, and
the signal-flow overrideable re-auth hook.

Verified against Swiggy's discovery document (mcp.swiggy.com/.well-known/oauth-authorization-server):
    issuer                        https://mcp.swiggy.com/auth
    authorization_endpoint        https://mcp.swiggy.com/auth/authorize
    token_endpoint                https://mcp.swiggy.com/auth/token
    registration_endpoint         https://mcp.swiggy.com/auth/register
    code_challenge_methods        S256
    scopes_supported              mcp:tools, mcp:resources, mcp:prompts
    token_endpoint_auth_methods   none (public client), client_secret_post/basic

Token lifecycle (v1.0):
    access token 5 days (432000 s); no refresh tokens wired in v1. On a 401 the
    client raises MCPNotProvisionedError so callers can fall back to local mode
    or re-run /auth/login. See mcp.swiggy.com/builders/docs.
"""

import base64
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

import httpx

_log = logging.getLogger("clip2cart.transport")

DEFAULT_SCOPE = "mcp:tools"
PROTOCOL_VERSION = "2025-06-18"

# Servers this transport can reach, keyed by the URL path segment Swiggy uses.
SERVER_INSTAMART = "im"


def _mcp_host() -> str:
    """The Swiggy MCP JSON-RPC host.

    Swiggy's docs say staging traffic lands on mcp-staging.swiggy.com/{server},
    so this is overridable via SWIGGY_MCP_HOST once staging credentials assign
    a value. Defaults to production.
    """
    return os.environ.get("SWIGGY_MCP_HOST") or "https://mcp.swiggy.com"


def _auth_base() -> str:
    """The Swiggy OAuth host (authorize/token/register/check-redirect-uri).

    Defaults to the known production auth host; override via SWIGGY_MCP_AUTH_HOST
    if a staging credential email says the auth server moves too.
    """
    return os.environ.get("SWIGGY_MCP_AUTH_HOST") or "https://mcp.swiggy.com/auth"


class MCPNotProvisionedError(RuntimeError):
    """No usable Swiggy MCP token, or the server rejected it with a 401.

    Callers should fall back to local simulation or re-run the OAuth flow.
    """


class MCPCallError(RuntimeError):
    """The MCP server rejected or failed a tool call."""


class ForbiddenToolError(RuntimeError):
    """Attempted to call a checkout/payment/order tool that was deliberately
    gated off. Never allowed from this build."""


# ---------------------------------------------------------------------------
# OAuth 2.1 + PKCE (shared by every server)
# ---------------------------------------------------------------------------

def make_pkce_pair() -> tuple[str, str]:
    """Returns (code_verifier, code_challenge) for S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def register_client(redirect_uri: str, client_name: str = "Clip2Cart", timeout: float = 20.0) -> dict:
    """RFC 7591 dynamic client registration against Swiggy's auth server."""
    response = httpx.post(
        f"{_auth_base()}/register",
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


def check_redirect_uri_whitelisted(redirect_uri: str, timeout: float = 10.0) -> Optional[bool]:
    """Ask Swiggy whether this redirect URI is on their client allowlist.

    Returns True or False as Swiggy reported it, or None when the check itself
    could not be completed (a network blip is "unknown", never "no").
    """
    try:
        response = httpx.get(
            f"{_auth_base()}/check-redirect-uri",
            params={"redirect_uri": redirect_uri},
            timeout=timeout,
        )
        response.raise_for_status()
        value = response.json().get("whitelisted")
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, bool) else None


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
    return f"{_auth_base()}/authorize?{query}"


def exchange_code(
    code: str, verifier: str, client_id: str, redirect_uri: str, timeout: float = 20.0
) -> dict:
    """Swap an authorization code for an access token. Public client, no secret."""
    response = httpx.post(
        f"{_auth_base()}/token",
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


def _token_path() -> Path:
    return Path(__file__).parent.parent / ".swiggy_token"


def load_token(token: Optional[str] = None) -> str:
    """Resolve the bearer token from the argument, env, or the on-disk store.

    The OAuth callback persists the token to .swiggy_token (chmod 600) so a
    server restart doesn't force a re-login. Precedence: explicit arg, then
    SWIGGY_MCP_TOKEN env, then the file.
    """
    if token:
        return token
    token = os.environ.get("SWIGGY_MCP_TOKEN") or ""
    if token:
        return token
    path = _token_path()
    if path.exists():
        return path.read_text().strip()
    return ""


# ---------------------------------------------------------------------------
# The transport
# ---------------------------------------------------------------------------

class SwiggyMCPTransport:
    """Talk to one Swiggy MCP server (im) over streamable HTTP.

    Owns the httpx connection, session-id header, JSON-RPC framing, and the
    401 → MCPNotProvisionedError path. Subclasses add a ``_guard_tool`` and the
    typed wrapper methods for the tools they expose.
    """

    def __init__(self, server: str, allowed_tools: set[str], forbidden_tools: set[str],
                 token: Optional[str] = None, log=None, timeout: float = 30.0):
        self.server = server
        self.allowed_tools = set(allowed_tools)
        self.forbidden_tools = set(forbidden_tools)

        self.token = load_token(token)
        if not self.token:
            raise MCPNotProvisionedError(
                "No Swiggy MCP access token. Run the OAuth flow at /auth/login, "
                "or set SWIGGY_MCP_TOKEN in .env."
            )

        self.log = log
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._next_id = 1
        self._client = httpx.Client(timeout=timeout)

    # -- plumbing -----------------------------------------------------------

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

    def _rpc(self, method: str, params: Optional[dict] = None,
             _retries: int = 2, _backoff: float = 0.4) -> dict:
        """Send a JSON-RPC request with automatic retry on transient failures.

        Retries on: connection errors, 5xx, and timeouts. 401 is never retried
        (the token is dead; raising MCPNotProvisionedError is immediate). 4xx
        (other than 401) is also never retried — it's a client-level mistake.
        """
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        self._next_id += 1
        if params is not None:
            payload["params"] = params

        last_error: Optional[Exception] = None
        for attempt in range(_retries + 1):
            try:
                response = self._client.post(
                    f"{_mcp_host()}/{self.server}", json=payload, headers=self._headers()
                )
                if response.status_code == 401:
                    raise MCPNotProvisionedError(
                        f"Swiggy MCP ({self.server}) rejected the token with a 401. "
                        "Run the OAuth flow again at /auth/login."
                    )
                if response.status_code >= 500 and attempt < _retries:
                    _log.warning(
                        "%s %s returned %d, retrying (%d/%d)",
                        self.server, method, response.status_code, attempt + 1, _retries,
                    )
                    time.sleep(_backoff * (2 ** attempt))
                    continue
                if response.status_code >= 400:
                    raise MCPCallError(f"MCP HTTP {response.status_code}: {response.text[:300]}")

                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self._session_id = session

                body = _decode_body(response)
                if "error" in body:
                    raise MCPCallError(f"MCP error: {body['error']}")
                return body.get("result", {})

            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                last_error = exc
                if attempt < _retries:
                    _log.warning(
                        "%s %s connection error (%s), retrying (%d/%d)",
                        self.server, method, type(exc).__name__, attempt + 1, _retries,
                    )
                    time.sleep(_backoff * (2 ** attempt))
                    continue
                raise MCPCallError(
                    f"MCP connection failed after {_retries + 1} attempts: {exc}"
                ) from exc

            except MCPNotProvisionedError:
                raise  # never retry auth failures

        # If we exhausted retries, raise the last connection error.
        if last_error:
            raise MCPCallError(f"MCP connection failed after {_retries + 1} attempts: {last_error}")

    def _guard_tool(self, tool: str) -> None:
        # "*" in the allowlist is an explicit escape hatch meaning every tool
        # except the forbidden set — used by reconcile probes and for optional
        # tools a client has not enumerated.
        if tool in self.forbidden_tools:
            raise ForbiddenToolError(
                f"Tool '{tool}' is not callable from Clip2Cart ({self.server}). "
                "Checkout and payment tools are intentionally blocked."
            )
        if "*" not in self.allowed_tools and tool not in self.allowed_tools:
            raise ForbiddenToolError(
                f"Tool '{tool}' is not in this client's allowlist ({self.server})."
            )

    def call_tool(self, tool: str, arguments: dict):
        """Call one tool, record it on the wire log if one is attached.

        The unwrapped payload: structuredContent when Swiggy populated it
        (Instamart and Food generally do; a few tools answer in prose and leave
        it empty). Tools that answer in prose keep their text under the
        ``content_text`` key so a client can parse it without losing the raw
        message.
        """
        self._guard_tool(tool)
        started = time.perf_counter()
        try:
            result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        except Exception as exc:
            if self.log:
                self.log.record(tool, arguments, str(exc), started, is_error=True,
                                arguments_verified=True, server=self.server)
            raise
        unwrapped = _extract_result(result)
        if self.log:
            self.log.record(tool, arguments, unwrapped, started,
                            arguments_verified=True, server=self.server)
        return unwrapped

    # -- lifecycle ----------------------------------------------------------

    def initialize(self) -> dict:
        return self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Clip2Cart", "version": "1.0.0"},
        })

    def list_tools(self) -> list[dict]:
        """Read the server's real tool schemas (reconciles inferred shapes)."""
        return self._rpc("tools/list").get("tools", [])

    def close(self) -> None:
        self._client.close()


def safe_tool_call(client, tool: str, arguments: dict, fallback=None):
    """Call a tool, returning fallback on transient / auth errors.

    Designed for the FastAPI endpoint handlers: on MCPNotProvisionedError it
    returns the fallback dict (caller can render a "token expired" message);
    on MCPCallError it returns fallback with an ``error`` field so the frontend
    can show a specific message without a 500.
    """
    try:
        return client.call_tool(tool, arguments)
    except MCPNotProvisionedError as exc:
        if fallback is None:
            raise
        return {**(fallback or {}), "error": str(exc), "error_type": "auth"}
    except (MCPCallError, ForbiddenToolError) as exc:
        return {**(fallback or {}), "error": str(exc), "error_type": "mcp"}


def _content_text(result: Any) -> str:
    """Pull the prose payload Swiggy returns as MCP content blocks.

    Some tools answer with ``content`` blocks of
    type text and leave ``structuredContent`` empty. Joins multiple blocks so a
    multi-part answer is preserved.
    """
    if not isinstance(result, dict):
        return ""
    content = result.get("content") or []
    if not isinstance(content, list):
        return ""
    parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(parts).strip()


def _extract_result(result: Any) -> dict:
    """Settle on the payload shape a service client should read.

    Prefers non-empty structuredContent (sidelining the prose under
    ``content_text`` for messages that also explain themselves), falls back to
    the content text alone when structuredContent is empty, and finally the raw
    result so the client at least sees the envelope.
    """
    if not isinstance(result, dict):
        return {"content_text": str(result)}
    text = _content_text(result)
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        if text:
            structured = dict(structured)
            structured.setdefault("content_text", text)
        return structured
    if text:
        return {"content_text": text}
    return result


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


def _as_data(result) -> Any:
    """Every live response wraps its payload under a standard envelope.

    The wire log shows Swiggy answering with structuredContent like
    {'success': true, 'data': {...}}. A few tools respond at the top level
    instead, so unwrap either way.
    """
    if isinstance(result, dict):
        if isinstance(result.get("data"), dict) and result.get("success") is not False:
            return result["data"]
        return result
    return result