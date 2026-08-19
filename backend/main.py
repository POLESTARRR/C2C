import logging
import os
import secrets
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

load_dotenv()

from .catalog import repick_pack
from .instamart_client import LocalInstamartSimulator
from .llm_extract import ExtractionError, extract_ingredients
from .mcp_log import MCPCallLog, MODE_LOCAL, MODE_MCP
from .models import (
    BasketItem,
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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "instamart_mode": _instamart_mode(),
        "groq_key_present": bool(os.environ.get("GROQ_API_KEY")),
        "swiggy_token_present": bool(os.environ.get("SWIGGY_MCP_TOKEN")),
    }


# ---------------------------------------------------------------------------
# Swiggy MCP OAuth 2.1 + PKCE
# ---------------------------------------------------------------------------

_PKCE_STATES: dict[str, str] = {}  # state -> code_verifier


def _instamart_mode() -> str:
    return (os.environ.get("INSTAMART_MODE") or "local").strip().lower()


def _redirect_uri() -> str:
    return os.environ.get("SWIGGY_MCP_REDIRECT_URI") or "http://localhost:8000/auth/callback"


@app.get("/auth/login")
def auth_login():
    """Start the Swiggy MCP authorization flow.

    Swiggy's auth server supports dynamic client registration and PKCE S256, as
    published at /.well-known/oauth-authorization-server. No pre shared secret
    is needed. We register, then send the user to Swiggy to authorize.
    """
    from .swiggy_mcp_client import build_authorize_url, make_pkce_pair, register_client

    redirect_uri = _redirect_uri()
    client_id = os.environ.get("SWIGGY_MCP_CLIENT_ID")
    if not client_id:
        try:
            client_id = register_client(redirect_uri).get("client_id", "swiggy-mcp")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Client registration with Swiggy failed: {exc}")

    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(16)
    _PKCE_STATES[state] = verifier

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

    verifier = _PKCE_STATES.pop(state, None)
    if not verifier:
        raise HTTPException(400, "Unknown or expired state. Start again at /auth/login.")

    client_id = os.environ.get("SWIGGY_MCP_CLIENT_ID") or "swiggy-mcp"
    token_response = exchange_code(code, verifier, client_id, _redirect_uri())

    access_token = token_response.get("access_token")
    if access_token:
        # Held in this process only. Nothing is written to disk.
        os.environ["SWIGGY_MCP_TOKEN"] = access_token

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


@app.post("/process", response_model=ProcessResponse)
def process(request: ProcessRequest):
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
    call_log = MCPCallLog(mode=MODE_MCP if mode == "mcp" else MODE_LOCAL)
    client = LocalInstamartSimulator(log=call_log)

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
                    pack_label=f"{catalog_item['pack_size']:g} {catalog_item['pack_unit']}",
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
    leftovers = [i for i, line in enumerate(basket) if not line.matched_catalog_item]
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
            line.pack_label = f"{catalog_item['pack_size']:g} {catalog_item['pack_unit']}"
            line.units = units
            line.line_total_inr = round(catalog_item["price_inr"] * units, 2)
            line.suggested_substitute = None
            line.matched_by = "semantic"

            matched_count += 1
            estimated_total += line.line_total_inr

    client.get_cart()  # final MCP call, the same way a real checkout prep would

    return ProcessResponse(
        transcript_snippet=transcript_text[:300],
        transcript_source=transcript_source,
        instamart_mode=mode,
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
    )
