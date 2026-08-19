"""Semantic fallback for ingredients that string matching cannot resolve.

Fuzzy matching compares spellings, so it cannot know that "kaddu" and "pumpkin"
are one vegetable, or that "jhinga" is prawns. Worse, a shortlist built from
fuzzy scores does not even contain the right answer in those cases, so asking a
model to pick from that shortlist cannot help.

So instead of choosing from candidates, the model is asked to NAME the product:
give the plain English grocery term for this ingredient. That answer then goes
back through the ordinary lexical matcher. The model supplies world knowledge,
the catalog stays the single source of truth about what is actually stocked,
and nothing can be matched to a product that does not exist.

One batched call handles every leftover ingredient at once.
"""

import json
import os
from typing import Optional

SYSTEM_PROMPT = """You translate ingredient names into plain English grocery terms.

For each numbered ingredient, give the ordinary English name it would carry on a
supermarket shelf in India. The input may be Hindi, Devanagari, a regional word,
a brand name, or a casual phrase.

Examples:
  "kaddu" -> "pumpkin"
  "कद्दू" -> "pumpkin"
  "jhinga" -> "prawns"
  "shimla mirch" -> "capsicum"
  "hung curd" -> "curd"
  "sabut lal mirch" -> "red chilli"

If the item is not something a grocery shop sells, return "" for it.
Do not invent a different product. Stay as close to the original as possible.

Respond ONLY with a JSON list, no commentary:
[{"i": 0, "name": "pumpkin"}, {"i": 1, "name": ""}]
"""


def _client():
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key, timeout=30.0, max_retries=0)


def canonicalise(names: list[str]) -> dict[int, str]:
    """Ask the model for the English grocery term for each name.

    Returns {index: english_name} for the ones it could name. Never raises;
    an unavailable or malformed response simply yields nothing, leaving the
    caller with the unmatched items it already had.
    """
    if not names:
        return {}

    client = _client()
    if client is None:
        return {}

    from .llm_extract import MODEL

    listing = "\n".join(f"{i}. {name}" for i, name in enumerate(names))
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": listing},
            ],
        )
        raw = completion.choices[0].message.content or ""
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end == -1:
            return {}
        answers = json.loads(raw[start : end + 1])
    except Exception:  # noqa: BLE001 - a best effort extra pass, never fatal
        return {}

    resolved: dict[int, str] = {}
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        index, name = answer.get("i"), answer.get("name")
        if isinstance(index, int) and isinstance(name, str) and name.strip():
            resolved[index] = name.strip()
    return resolved


def resolve_unmatched(names: list[str]) -> dict[int, dict]:
    """Match ingredients the lexical pass gave up on.

    The model names the product, then the normal matcher decides whether we
    actually stock it. Returns {index: catalog_item} for real matches only.
    """
    from .catalog import match_product

    resolved: dict[int, dict] = {}
    for index, english in canonicalise(names).items():
        item, _substitute, _score = match_product(english)
        if item:
            resolved[index] = item
    return resolved
