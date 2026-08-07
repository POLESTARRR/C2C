from typing import Literal, Optional

from pydantic import BaseModel


class ProcessRequest(BaseModel):
    source_type: Literal["youtube_url", "transcript_text"]
    value: str


class ExtractedProduct(BaseModel):
    product_name: str
    category: str
    estimated_quantity: str
    confidence: Literal["Low", "Medium", "High"]


class BasketItem(BaseModel):
    product_name: str
    category: str
    matched_catalog_item: bool
    catalog_name: Optional[str] = None
    price_inr: Optional[float] = None
    estimated_quantity: str
    confidence: Literal["Low", "Medium", "High"]
    suggested_substitute: Optional[str] = None


class Summary(BaseModel):
    total_items: int
    matched_items: int
    estimated_total_inr: float


class ProcessResponse(BaseModel):
    transcript_snippet: str
    extracted_products: list[ExtractedProduct]
    basket: list[BasketItem]
    summary: Summary
