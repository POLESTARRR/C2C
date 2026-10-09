import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

load_dotenv()

from .catalog import repick_pack
from .instamart_client import InstamartClient, LocalInstamartSimulator
from .llm_extract import ExtractionError, extract_ingredients
from .mcp_log import (
    MCPCallLog,
    MODE_LOCAL,
    MODE_MCP,
)
from .models import (
    BasketItem,
    DeliveryAddress,
    ExtractedProduct,
    ProcessRequest,
    ProcessResponse,
    Summary,
)
from .quantity import Quantity, parse_quantity, scale, to_pack_unit, units_needed
from .semantic import resolve_unmatched
from .transcript import TranscriptFetchError, clean_transcript, fetch_transcript

log = logging.getLogger("clip2cart")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Clip2Cart, a Swiggy Instamart recipe to cart agent")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.middleware("http")
async def _no_cache_html(request, call_next):
    """Keep the page itself fresh. The demo frontend ships from the same disk
    that we edit here, and a phone browser caching index.html/app.js means the
    user keeps seeing a stale version of the flow. HTML may be revalidated on
    every load (no-cache, not no-store) so edits show up immediately."""
    response = await call_next(request)
    if request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
    return response

VALID_CATEGORIES = {"grocery", "personal_care", "household"}
VALID_CONFIDENCE = {"low": "Low", "medium": "Medium", "high": "High"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Always answer with JSON.

    The frontend parses every response as JSON. FastAPI's default plain text 500
    turns a backend hiccup into an unreadable "Unexpected token" error in the UI.
    This keeps failures legible.
    """
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Server error: {exc}"})


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/qr")
def qr_code():
    """Redirect to a public QR code image pointing at this server's LAN URL.

    Scan with your phone camera to open Clip2Cart — no tunnel, no typing IPs.
    Works when laptop and phone share the same WiFi.
    """
    import socket
    from urllib.parse import quote

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        host_ip = s.getsockname()[0]
        s.close()
    except Exception:
        host_ip = "localhost"

    url = f"http://{host_ip}:8000"
    qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={quote(url)}"
    from fastapi.responses import RedirectResponse
    return RedirectResponse(qr_img_url)


@app.get("/health")
def health():
    """Unified health check for the backend.

    Quick-peeks the Swiggy Instamart MCP server to report reachability.
    """
    from .mcp_transport import load_token

    token = load_token()
    token_present = bool(token)

    def _peek(server: str) -> bool:
        if not token_present:
            return False
        try:
            import httpx
            resp = httpx.post(
                f"https://mcp.swiggy.com/{server}",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "healthcheck", "version": "0"}}},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False

    return {
        "status": "ok",
        "instamart_mode": _instamart_mode(),
        "groq_key_present": bool(os.environ.get("GROQ_API_KEY")),
        "swiggy_token_present": token_present,
        "servers": {
            "instamart": {"endpoint": "mcp.swiggy.com/im", "reachable": _peek("im")},
        },
    }


# ---------------------------------------------------------------------------
# Swiggy MCP OAuth 2.1 + PKCE
# ---------------------------------------------------------------------------

# PKCE state is held on disk, not in process memory. The OTP flow is a
# multi-minute round trip through Swiggy; if this process restarts (a --reload
# edit, a Render redeploy, a free-tier cold start) the code_verifier must
# survive to meet the callback. Otherwise every redeploy between /auth/login
# and /auth/callback strands the user on "Unknown or expired state", which is
# exactly what happened twice in testing.
_PKCE_STORE_PATH = Path(__file__).parent.parent / ".auth_state.json"
_STATE_TTL_SECONDS = 600  # generous; an OTP flow is seconds, not minutes


def _load_pkce_states() -> dict:
    try:
        with open(_PKCE_STORE_PATH) as fh:
            states = json.load(fh)
        return states if isinstance(states, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_pkce_states(states: dict) -> None:
    # These are code_verifiers: read them only from this app's account.
    with open(_PKCE_STORE_PATH, "w") as fh:
        json.dump(states, fh)
    try:
        os.chmod(_PKCE_STORE_PATH, 0o600)
    except OSError:
        pass  # non-POSIX platform; all we could do is not crash


def _store_state(state: str, verifier: str) -> None:
    states = _load_pkce_states()
    for stale in [s for s, e in states.items() if time.time() > e.get("expires", 0)]:
        states.pop(stale, None)
    states[state] = {"verifier": verifier, "expires": time.time() + _STATE_TTL_SECONDS}
    _save_pkce_states(states)


def _consume_state(state: str) -> Optional[str]:
    """Pop a used state and return its verifier, or None if unknown/expired.

    The callback pops once: a browser refreshing a used callback then gets a
    clean 400, which is correct behaviour rather than thrash.
    """
    states = _load_pkce_states()
    entry = states.pop(state, None)
    if entry is None:
        return None
    _save_pkce_states(states)
    if time.time() > entry.get("expires", 0):
        return None
    return entry.get("verifier")


def _instamart_mode() -> str:
    return (os.environ.get("INSTAMART_MODE") or "local").strip().lower()


def _build_instamart_client(mode: str) -> tuple[InstamartClient, MCPCallLog, Optional[str]]:
    """Returns (client, call_log, fallback_note) for the configured mode.

    In mcp mode without a token we fall back to the local catalog and say so
    plainly rather than fail the request. The wire log then records the mode it
    actually ran in, so a fallback is never mistaken for a live cart.
    """
    if mode == "mcp":
        call_log = MCPCallLog(mode=MODE_MCP)
        try:
            from .swiggy_mcp_client import MCPNotProvisionedError, SwiggyMCPClient

            client = SwiggyMCPClient(log=call_log)
        except MCPNotProvisionedError as exc:
            log.warning("INSTAMART_MODE=mcp but no Swiggy token; using the local catalog")
            call_log = MCPCallLog(mode=MODE_LOCAL)
            return LocalInstamartSimulator(log=call_log), call_log, str(exc)
        else:
            return client, call_log, None

    call_log = MCPCallLog(mode=MODE_LOCAL)
    return LocalInstamartSimulator(log=call_log), call_log, None


def _redirect_uri() -> str:
    return os.environ.get("SWIGGY_MCP_REDIRECT_URI") or "http://localhost:8000/auth/callback"


@app.get("/auth/preflight")
def auth_preflight():
    """Report whether Swiggy will accept our redirect URI, without starting a flow.

    Swiggy allowlists redirect URIs by exact domain and the list is maintained
    by hand. Ours is not on it, so sign-in cannot complete no matter what this
    app does. That is worth stating plainly rather than letting someone
    discover it by being bounced to an error page mid flow.
    """
    from .swiggy_mcp_client import _auth_base, check_redirect_uri_whitelisted

    redirect_uri = _redirect_uri()
    whitelisted = check_redirect_uri_whitelisted(redirect_uri)

    if whitelisted is None:
        detail = ("Could not reach Swiggy's allowlist check. This says nothing "
                  "about the URI itself, only that the check did not complete.")
    elif whitelisted:
        detail = "Swiggy accepts this redirect URI. /auth/login will work."
    else:
        detail = (
            "Swiggy does not have this redirect URI on its allowlist, so sign in "
            "cannot complete. The allowlist is maintained by Swiggy per exact "
            "domain, and no change on our side satisfies it. It needs this URI "
            "added at their end."
        )

    return {
        "redirect_uri": redirect_uri,
        "whitelisted": whitelisted,
        "detail": detail,
        "checked_with": f"GET {_auth_base()}/check-redirect-uri",
    }


@app.get("/auth/login")
def auth_login(force: bool = False):
    """Start the Swiggy MCP authorization flow.

    Swiggy's auth server supports dynamic client registration and PKCE S256, as
    published at /.well-known/oauth-authorization-server. No pre shared secret
    is needed. We register, then send the user to Swiggy to authorize.
    """
    from .swiggy_mcp_client import (
        build_authorize_url,
        check_redirect_uri_whitelisted,
        make_pkce_pair,
        register_client,
    )

    redirect_uri = _redirect_uri()

    # Swiggy refuses unallowlisted redirect URIs at the authorize step, after we
    # have already registered a client and sent the user away. Checking first
    # means we can say why here, in our own words. Only an explicit false stops
    # us: an unreachable check is not a rejection, and ?force=1 proceeds anyway
    # so this can never become the reason a working flow is blocked.
    if not force and check_redirect_uri_whitelisted(redirect_uri) is False:
        raise HTTPException(
            409,
            f"Swiggy has not allowlisted {redirect_uri}, so sign in cannot "
            "complete. This is a per domain allowlist maintained on Swiggy's "
            "side and no configuration here satisfies it. See /auth/preflight "
            "for the check, or retry with ?force=1 to attempt it anyway.",
        )
    client_id = os.environ.get("SWIGGY_MCP_CLIENT_ID")
    if not client_id:
        try:
            client_id = register_client(redirect_uri).get("client_id", "swiggy-mcp")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Client registration with Swiggy failed: {exc}")
        # The token exchange has to present the same client_id the code was
        # issued to, so a dynamically registered id has to outlive this request.
        # Held in this process only, same as the token below.
        os.environ["SWIGGY_MCP_CLIENT_ID"] = client_id

    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(16)
    _store_state(state, verifier)

    return RedirectResponse(build_authorize_url(client_id, redirect_uri, challenge, state))


@app.get("/auth/callback")
def auth_callback(code: Optional[str] = None, state: Optional[str] = None,
                  error: Optional[str] = None):
    """OAuth redirect target. Exchanges the code for an access token."""
    from .swiggy_mcp_client import exchange_code

    if error:
        raise HTTPException(400, f"Swiggy returned an authorization error: {error}")
    if not code or not state:
        raise HTTPException(400, "Missing code or state in the callback.")

    verifier = _consume_state(state)
    if not verifier:
        raise HTTPException(400, "Unknown or expired state. Start again at /auth/login.")

    client_id = os.environ.get("SWIGGY_MCP_CLIENT_ID") or "swiggy-mcp"
    token_response = exchange_code(code, verifier, client_id, _redirect_uri())

    access_token = token_response.get("access_token")
    if access_token:
        os.environ["SWIGGY_MCP_TOKEN"] = access_token
        # Persist to disk so a restart (Render cold start, uvicorn reload) doesn't
        # force the user through OTP again. chmod 0600 keeps it account-private.
        token_path = Path(__file__).parent.parent / ".swiggy_token"
        token_path.write_text(access_token)
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass

    return {
        "authorized": bool(access_token),
        "token_type": token_response.get("token_type"),
        "expires_in": token_response.get("expires_in"),
        "scope": token_response.get("scope"),
        "next": "Set INSTAMART_MODE=mcp and run a recipe again to build a live cart.",
    }


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def _pack_label(catalog_item: dict) -> str:
    """Format a pack label even when the live gateway gave us no pack size.

    Local catalog rows always know their pack; live search results may not,
    and a None pack_size must not crash the row renderer (:g on None raises).
    """
    size = catalog_item.get("pack_size")
    unit = str(catalog_item.get("pack_unit") or "").strip()
    if size is None:
        return "1 pack" if unit else "1 pack"
    return f"{size:g} {unit}".strip()


def _normalise_item(item: dict) -> Optional[ExtractedProduct]:
    """Coerce one LLM object into an ExtractedProduct, or drop it.

    The model is told to use a fixed enum but it occasionally returns "high" or
    leaves a field out. Dropping one stray item beats failing the whole request.
    """
    if not isinstance(item, dict):
        return None

    name = str(item.get("product_name") or "").strip()
    if not name:
        return None

    category = str(item.get("category") or "grocery").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "grocery"

    confidence = VALID_CONFIDENCE.get(
        str(item.get("confidence") or "").strip().lower(), "Medium"
    )

    quantity = str(item.get("estimated_quantity") or "").strip()
    source = str(item.get("quantity_source") or "").strip().lower()
    is_estimate = source == "estimated"

    # A blank cell tells the user nothing. If the model gave us nothing usable,
    # fall back to one pack and be open about it being our guess.
    if not quantity or quantity.lower() in {"unknown", "some", "to taste", "n/a"}:
        quantity = "1 pack"
        is_estimate = True

    canonical = str(item.get("canonical_name") or "").strip()

    try:
        return ExtractedProduct(
            product_name=name,
            canonical_name=canonical,
            category=category,
            estimated_quantity=quantity,
            quantity_is_estimate=is_estimate,
            confidence=confidence,
        )
    except ValidationError:
        return None


@app.api_route("/process", methods=["GET", "POST"], response_model=ProcessResponse)
def process(request: ProcessRequest = None, source_type: str = None, value: str = None, servings: int = None):
    # GET fallback: cloudflared drops POST requests from some mobile IPv6
    # clients, so the frontend sends a GET with query params as a workaround.
    if request is None and source_type and value:
        request = ProcessRequest(source_type=source_type, value=value, servings=servings)
    transcript_source = "pasted"
    if request.source_type == "youtube_url":
        try:
            raw_text, transcript_source = fetch_transcript(request.value)
        except TranscriptFetchError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    else:
        raw_text = request.value

    transcript_text = clean_transcript(raw_text)
    if not transcript_text:
        raise HTTPException(status_code=422, detail="Transcript is empty.")

    try:
        raw_items, recipe_serves = extract_ingredients(transcript_text)
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Shop for however many people were asked for, defaulting to whatever the
    # recipe itself was written for.
    servings = request.servings or recipe_serves
    servings = max(1, min(servings, 20))
    factor = servings / recipe_serves if recipe_serves else 1.0

    extracted_products = [p for p in (_normalise_item(i) for i in raw_items) if p]

    mode = _instamart_mode()
    client, call_log, fallback_note = _build_instamart_client(mode)

    # Clear any leftover items from previous runs so the cart only contains
    # ingredients from this recipe.
    if hasattr(client, "clear_cart"):
        client.clear_cart()

    basket: list[BasketItem] = []
    matched_count = 0
    estimated_total = 0.0

    # The LLM already normalised each mention to an English grocery term.
    # Search on that first, since "onion" matches the catalog and "प्याज़"
    # only matches if someone happened to add it to the alias file.
    lookups = [p.canonical_name or p.product_name for p in extracted_products]

    for item, lookup in zip(extracted_products, lookups):
        qty = scale(parse_quantity(item.estimated_quantity), factor)

        # Search on both the canonical term and the speaker's own word, then
        # keep whichever scores higher. Canonicalising "मैदा" to the generic
        # "flour" once matched Ragi Flour, and because that counted as a hit we
        # never tried the original word, which has an exact alias to maida.
        probe, substitute, score = client.search_products(lookup)
        if lookup != item.product_name:
            alt_probe, alt_sub, alt_score = client.search_products(item.product_name)
            if alt_score > score:
                probe, substitute, score = alt_probe, alt_sub, alt_score

        required_amount = None
        required_label = qty.label() if qty else None
        if qty and probe:
            required_amount = to_pack_unit(qty, probe["pack_unit"], probe["base_name"])
            if required_amount:
                # Show what the recipe needs in the unit the product is sold in,
                # so that "two cups of atta" reads as "240 g" beside a 1kg pack.
                required_label = Quantity(required_amount, probe["pack_unit"]).label()

        catalog_item = probe
        if required_amount and probe:
            # Re-pick the pack now that we know how much is needed.
            catalog_item = repick_pack(probe, required_amount)

        if catalog_item:
            units = units_needed(
                qty,
                catalog_item["pack_size"],
                catalog_item["pack_unit"],
                catalog_item["base_name"],
            )
            client.update_cart(catalog_item, item.estimated_quantity, units=units)
            line_total = round(catalog_item["price_inr"] * units, 2)
            matched_count += 1
            estimated_total += line_total

            basket.append(
                BasketItem(
                    product_name=item.product_name,
                    canonical_name=item.canonical_name,
                    category=item.category,
                    matched_catalog_item=True,
                    estimated_quantity=item.estimated_quantity,
                    quantity_is_estimate=item.quantity_is_estimate,
                    confidence=item.confidence,
                    catalog_name=catalog_item["product_name"],
                    product_id=catalog_item["product_id"],
                    price_inr=catalog_item["price_inr"],
                    pack_label=_pack_label(catalog_item),
                    units=units,
                    line_total_inr=line_total,
                    match_score=round(score, 1),
                    required_label=required_label,
                    matched_by="lexical",
                )
            )
        else:
            basket.append(
                BasketItem(
                    product_name=item.product_name,
                    canonical_name=item.canonical_name,
                    category=item.category,
                    matched_catalog_item=False,
                    estimated_quantity=item.estimated_quantity,
                    quantity_is_estimate=item.quantity_is_estimate,
                    confidence=item.confidence,
                    suggested_substitute=substitute,
                    match_score=round(score, 1),
                    required_label=required_label,
                )
            )

    # Anything string matching could not place goes to the semantic pass. The
    # model names the product in plain English and the catalog decides whether
    # we stock it, so nothing can be matched to a product that does not exist.
    #
    # This rescue is catalog-bound, so it only runs in local mode. In live mode
    # the search already happened against the real server, and rescuing a miss
    # with a local catalog product would push a product_id the live cart has
    # never seen. An honest unmatched line beats a fake cart entry.
    leftovers = [i for i, line in enumerate(basket) if not line.matched_catalog_item]
    if leftovers and not isinstance(client, LocalInstamartSimulator):
        log.info("Skipping semantic rescue in live mode for %d unmatched items", len(leftovers))
        leftovers = []
    if leftovers:
        rescued = resolve_unmatched([
            basket[i].canonical_name or basket[i].product_name for i in leftovers
        ])
        for offset, catalog_item in rescued.items():
            position = leftovers[offset]
            line = basket[position]
            qty = scale(parse_quantity(line.estimated_quantity), factor)
            required = to_pack_unit(qty, catalog_item["pack_unit"], catalog_item["base_name"]) if qty else None
            if required:
                catalog_item = repick_pack(catalog_item, required)
                line.required_label = Quantity(required, catalog_item["pack_unit"]).label()
            units = units_needed(qty, catalog_item["pack_size"], catalog_item["pack_unit"],
                                 catalog_item["base_name"])
            client.update_cart(catalog_item, line.estimated_quantity, units=units)

            line.matched_catalog_item = True
            line.catalog_name = catalog_item["product_name"]
            line.product_id = catalog_item["product_id"]
            line.price_inr = catalog_item["price_inr"]
            line.pack_label = _pack_label(catalog_item)
            line.units = units
            line.line_total_inr = round(catalog_item["price_inr"] * units, 2)
            line.suggested_substitute = None
            line.matched_by = "semantic"

            matched_count += 1
            estimated_total += line.line_total_inr

    client.get_cart()  # final MCP call, the same way a real checkout prep would

    # Surface the delivery address so the frontend can tell the user which
    # Swiggy address their cart was written to — prevents the "empty cart"
    # confusion when the app has a different address selected.
    addr = None
    if hasattr(client, "get_address_info"):
        info = client.get_address_info()
        if info.get("id"):
            addr = DeliveryAddress(**info)

    resp = ProcessResponse(
        transcript_snippet=transcript_text[:300],
        transcript_source=transcript_source,
        instamart_mode=mode,
        fallback_note=fallback_note,
        extracted_products=extracted_products,
        basket=basket,
        summary=Summary(
            recipe_serves=recipe_serves,
            servings=servings,
            total_items=len(extracted_products),
            matched_items=matched_count,
            estimated_total_inr=round(estimated_total, 2),
            mcp_call_count=len(call_log.as_list()),
        ),
        mcp_calls=call_log.as_list(),
        delivery_address=addr,
    )
    # Stream the JSON response to keep the connection alive through cloudflared
    # tunnels, which can drop buffered responses from mobile IPv6 clients.
    payload = resp.model_dump_json()
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Cache-Control": "no-cache"},
    )
