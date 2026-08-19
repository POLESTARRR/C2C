from typing import Any, Literal, Optional

from pydantic import BaseModel


class ProcessRequest(BaseModel):
    source_type: Literal["youtube_url", "transcript_text"]
    value: str
    # How many people to shop for. None means keep the recipe's own serving size.
    servings: Optional[int] = None


class ExtractedProduct(BaseModel):
    product_name: str
    canonical_name: str = ""      # plain English term the LLM normalised it to
    category: str
    estimated_quantity: str
    # False when the amount came from the video, True when we filled it in.
    quantity_is_estimate: bool = False
    confidence: Literal["Low", "Medium", "High"]


class BasketItem(BaseModel):
    product_name: str
    canonical_name: str = ""
    category: str
    matched_catalog_item: bool
    estimated_quantity: str
    confidence: Literal["Low", "Medium", "High"]

    # Populated when we matched a real product
    catalog_name: Optional[str] = None
    product_id: Optional[str] = None
    price_inr: Optional[float] = None
    pack_label: Optional[str] = None
    units: int = 1
    line_total_inr: Optional[float] = None
    match_score: Optional[float] = None

    # What the recipe actually asked for, normalised (e.g. "480 ml")
    required_label: Optional[str] = None
    suggested_substitute: Optional[str] = None
    matched_by: Optional[str] = None   # "lexical" or "semantic"
    quantity_is_estimate: bool = False


class MCPCall(BaseModel):
    seq: int
    endpoint: str
    tool: str
    mode: str
    latency_ms: float
    arguments_verified: bool
    request: dict[str, Any]
    response: dict[str, Any]


class Summary(BaseModel):
    recipe_serves: int = 4      # what the recipe itself is written for
    servings: int = 4           # what we shopped for
    total_items: int
    matched_items: int
    estimated_total_inr: float
    mcp_call_count: int = 0


class ProcessResponse(BaseModel):
    transcript_snippet: str
    transcript_source: str = "pasted"   # "captions", "audio", or "pasted"
    instamart_mode: str
    extracted_products: list[ExtractedProduct]
    basket: list[BasketItem]
    summary: Summary
    mcp_calls: list[MCPCall] = []
