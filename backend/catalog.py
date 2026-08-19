"""Match a free-text ingredient to a real catalog product.

Two things matter here that the first version got wrong.

1. Matching against full product names like "Amul Malai Paneer 200g" meant the
   pack size was part of the fuzzy comparison. We now match against a clean
   `base_name` and treat pack selection as a separate decision.
2. Recipe transcripts are bilingual. Words like "jeera", "haldi" and "dahi"
   simply miss without an alias pass, so the alias map runs before fuzzy
   matching.
"""

import json
import re
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

DATA_DIR = Path(__file__).parent / "data"
CATALOG_PATH = DATA_DIR / "catalog.json"
ALIASES_PATH = DATA_DIR / "aliases.json"

# Tuned against the demo transcripts. High enough that near-misses do not
# silently become the wrong product, low enough that real variants still land.
MATCH_THRESHOLD = 82

# Below this, a "nearest catalog item" is noise rather than a suggestion.
# Without it, blueberries suggested toilet cleaner at 48 and broccoli suggested
# chickpeas at 46, which is worse than admitting we do not stock the thing.
SUBSTITUTE_FLOOR = 70

_STOPWORDS = {
    "fresh", "chopped", "boiled", "finely", "roughly", "grated", "sliced", "diced",
    "ground", "powdered", "raw", "ripe", "large", "small", "medium", "whole",
    "of", "the", "a", "an", "some", "few", "and", "or", "to", "taste", "for",
    "garnish", "optional", "roasted", "crushed", "peeled", "washed", "soaked",
}


def load_catalog() -> list[dict]:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_aliases() -> dict:
    with open(ALIASES_PATH, encoding="utf-8") as f:
        return json.load(f)


_CATALOG = load_catalog()
_ALIASES = load_aliases()

# base_name -> every pack of that product, cheapest pack first
_BY_BASE: dict[str, list[dict]] = {}
for _item in _CATALOG:
    _BY_BASE.setdefault(_item["base_name"], []).append(_item)
for _packs in _BY_BASE.values():
    _packs.sort(key=lambda i: (i.get("pack_size") or 0))

_BASE_NAMES = list(_BY_BASE.keys())


# Devanagari, so a Hindi transcript survives normalisation instead of being
# deleted character by character.
_KEEP = re.compile(r"[^a-z0-9\s\u0900-\u097F]")


def normalise(name: str) -> str:
    """Lowercase the name, then strip punctuation and recipe filler words.

    Non-Latin scripts are preserved. The previous version stripped anything
    outside a-z0-9, which silently reduced every Hindi ingredient to an empty
    string and guaranteed a zero score.
    """
    text = _KEEP.sub(" ", (name or "").lower())
    words = [w for w in text.split() if w and w not in _STOPWORDS]
    return " ".join(words).strip()


def resolve_alias(name: str) -> str:
    """Map Hindi, regional and plural forms onto the catalog's base names."""
    key = normalise(name)
    if not key:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    # Try dropping a trailing plural s before giving up.
    if key.endswith("s") and key[:-1] in _ALIASES:
        return _ALIASES[key[:-1]]
    return key


def _score(query: str, choice: str, *, score_cutoff: float = 0, **_kwargs) -> float:
    """Blend token_set_ratio with a plain ratio.

    token_set_ratio on its own scores 100 whenever one name's tokens are a
    subset of the other's, which makes "kasuri methi" look like a perfect match
    for "methi seeds". Averaging in token_sort_ratio penalises the words that
    are missing or extra.

    It accepts and ignores rapidfuzz's score_cutoff so that it works as a custom
    scorer with process.extractOne.
    """
    blended = (fuzz.token_set_ratio(query, choice) + fuzz.token_sort_ratio(query, choice)) / 2
    return blended if blended >= score_cutoff else 0.0


def pick_pack(packs: list[dict], required: Optional[float], pack_unit_hint: str = "") -> dict:
    """Choose the smallest pack that covers what the recipe needs.

    When the recipe does not say how much, it falls back to the smallest pack,
    which is what a shopper reaches for anyway.
    """
    if not required:
        return packs[0]
    for pack in packs:  # already sorted smallest first
        if (pack.get("pack_size") or 0) >= required:
            return pack
    return packs[-1]  # more than the biggest pack, so the caller buys several


def match_product(
    name: str,
    required: Optional[float] = None,
) -> tuple[Optional[dict], Optional[str], float]:
    """Returns (matched_catalog_item, suggested_substitute_name, score).

    Above MATCH_THRESHOLD we return a product. Below it we return the nearest
    catalog item as a suggestion, so the user can see why nothing matched
    instead of watching the ingredient vanish from the basket.
    """
    if not _BASE_NAMES:
        return None, None, 0.0

    query = resolve_alias(name)
    if not query:
        return None, None, 0.0

    best = process.extractOne(query, _BASE_NAMES, scorer=_score)
    if best is None:
        return None, None, 0.0

    best_base, score, _ = best
    packs = _BY_BASE[best_base]

    if score >= MATCH_THRESHOLD:
        return pick_pack(packs, required), None, float(score)

    # Only offer a substitute when it is actually related. A bad suggestion
    # damages trust more than an honest "we do not stock this".
    if score >= SUBSTITUTE_FLOOR:
        return None, packs[0]["product_name"], float(score)
    return None, None, float(score)


def repick_pack(item: dict, required: Optional[float]) -> dict:
    """Given a product we already matched, choose the pack size again now that
    we know how much the recipe needs."""
    packs = _BY_BASE.get(item["base_name"])
    if not packs:
        return item
    return pick_pack(packs, required)


def packs_for(base_name: str) -> list[dict]:
    """Every pack variant of a product, smallest first."""
    return _BY_BASE.get(base_name, [])


def top_candidates(name: str, limit: int = 6) -> list[tuple[dict, float]]:
    """The most plausible catalog rows for a name, best first.

    Used to build the shortlist the semantic pass chooses from, so the model
    picks between real products rather than inventing one.
    """
    query = resolve_alias(name)
    if not query or not _BASE_NAMES:
        return []
    scored = process.extract(query, _BASE_NAMES, scorer=_score, limit=limit)
    return [(_BY_BASE[base][0], float(score)) for base, score, _ in scored]
