from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from .instamart_client import LocalInstamartSimulator
from .llm_extract import ExtractionError, extract_ingredients
from .models import (
    BasketItem,
    ExtractedProduct,
    ProcessRequest,
    ProcessResponse,
    Summary,
)
from .transcript import TranscriptFetchError, clean_transcript, fetch_youtube_transcript

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="Swiggy Instamart Recipe-to-Cart Agent")
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/auth/callback")
def auth_callback(code: Optional[str] = None, state: Optional[str] = None):
    """OAuth 2.1 + PKCE redirect target for Swiggy MCP production access.

    Not wired to a real token exchange yet — no production credentials.
    This is the registered redirect URI for the Builders Club application;
    once granted, this becomes the code-for-token exchange step.
    """
    return {"received_code": bool(code), "state": state}


@app.post("/process", response_model=ProcessResponse)
def process(request: ProcessRequest):
    if request.source_type == "youtube_url":
        try:
            raw_text = fetch_youtube_transcript(request.value)
        except TranscriptFetchError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    else:
        raw_text = request.value

    transcript_text = clean_transcript(raw_text)
    if not transcript_text:
        raise HTTPException(status_code=422, detail="Transcript is empty.")

    try:
        raw_items = extract_ingredients(transcript_text)
    except ExtractionError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    extracted_products = [ExtractedProduct(**item) for item in raw_items]

    client = LocalInstamartSimulator()
    basket: list[BasketItem] = []
    matched_count = 0
    estimated_total = 0.0

    for item in extracted_products:
        catalog_item, substitute = client.search_products(item.product_name)
        if catalog_item:
            client.update_cart(catalog_item, item.estimated_quantity)
            matched_count += 1
            estimated_total += catalog_item["price_inr"]
            basket.append(
                BasketItem(
                    product_name=item.product_name,
                    category=item.category,
                    matched_catalog_item=True,
                    catalog_name=catalog_item["product_name"],
                    price_inr=catalog_item["price_inr"],
                    estimated_quantity=item.estimated_quantity,
                    confidence=item.confidence,
                )
            )
        else:
            basket.append(
                BasketItem(
                    product_name=item.product_name,
                    category=item.category,
                    matched_catalog_item=False,
                    estimated_quantity=item.estimated_quantity,
                    confidence=item.confidence,
                    suggested_substitute=substitute,
                )
            )

    return ProcessResponse(
        transcript_snippet=transcript_text[:300],
        extracted_products=extracted_products,
        basket=basket,
        summary=Summary(
            total_items=len(extracted_products),
            matched_items=matched_count,
            estimated_total_inr=estimated_total,
        ),
    )
