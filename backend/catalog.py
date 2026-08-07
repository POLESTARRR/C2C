import json
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

CATALOG_PATH = Path(__file__).parent / "data" / "catalog.json"
MATCH_THRESHOLD = 70


def load_catalog() -> list[dict]:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


_CATALOG = load_catalog()
_NAMES = [item["product_name"] for item in _CATALOG]


def match_product(name: str) -> tuple[Optional[dict], Optional[str]]:
    """Returns (matched_catalog_item, nearest_name_as_substitute).

    If the best match clears MATCH_THRESHOLD, it's returned as a match.
    Otherwise the best candidate (whatever its score) is returned as a
    suggested substitute, and matched_catalog_item is None.
    """
    if not _NAMES:
        return None, None

    best = process.extractOne(name, _NAMES, scorer=fuzz.partial_ratio)
    if best is None:
        return None, None

    best_name, score, index = best
    if score >= MATCH_THRESHOLD:
        return _CATALOG[index], None
    return None, best_name
