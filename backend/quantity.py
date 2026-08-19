"""Parse the free-text quantities the LLM returns into something we can shop with.

A transcript says "two cups of atta" or "three to four potatoes". Instamart sells
1kg packs. This module turns the former into grams, or ml, or pieces, so we can
work out how many packs a recipe actually needs instead of pretending every
ingredient costs exactly one unit price.

Conversions are deliberately approximate, since a cup of flour is not a precise
measure. The UI labels the result as an estimate.
"""

import math
import re
from dataclasses import dataclass
from typing import Optional

# Canonical units we normalise everything into.
MASS = "g"
VOLUME = "ml"
COUNT = "pc"

WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
    "dozen": 12, "half": 0.5, "quarter": 0.25, "couple": 2,
    "few": 3, "several": 3,
    # Hindi. A recipe narrated in Hindi says "आधा चम्मच", not "half a teaspoon".
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6,
    "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12,
    "पंद्रह": 15, "बीस": 20, "पचास": 50, "सौ": 100,
    "आधा": 0.5, "आधी": 0.5, "अाधा": 0.5, "डेढ़": 1.5, "ढाई": 2.5, "पौना": 0.75,
    "सवा": 1.25, "चौथाई": 0.25,
}

# unit token maps to (canonical unit, multiplier). Kitchen measures that are really
# volumes get treated as ml, and only become grams once we know the ingredient.
UNITS = {
    "g": (MASS, 1), "gram": (MASS, 1), "grams": (MASS, 1), "gm": (MASS, 1), "gms": (MASS, 1),
    "kg": (MASS, 1000), "kilo": (MASS, 1000), "kilos": (MASS, 1000), "kilogram": (MASS, 1000),
    "kilograms": (MASS, 1000),
    "ml": (VOLUME, 1), "millilitre": (VOLUME, 1), "milliliter": (VOLUME, 1),
    "l": (VOLUME, 1000), "litre": (VOLUME, 1000), "liter": (VOLUME, 1000),
    "litres": (VOLUME, 1000), "liters": (VOLUME, 1000),
    "cup": (VOLUME, 240), "cups": (VOLUME, 240),
    "glass": (VOLUME, 250), "glasses": (VOLUME, 250),
    "katori": (VOLUME, 150), "katoris": (VOLUME, 150),
    "bowl": (VOLUME, 250), "bowls": (VOLUME, 250),
    "tbsp": (VOLUME, 15), "tablespoon": (VOLUME, 15), "tablespoons": (VOLUME, 15),
    "tsp": (VOLUME, 5), "teaspoon": (VOLUME, 5), "teaspoons": (VOLUME, 5),
    "pinch": (MASS, 1), "pinches": (MASS, 1),
    "handful": (MASS, 30), "handfuls": (MASS, 30),
    "pc": (COUNT, 1), "pcs": (COUNT, 1), "piece": (COUNT, 1), "pieces": (COUNT, 1),
    "unit": (COUNT, 1), "units": (COUNT, 1), "no": (COUNT, 1), "nos": (COUNT, 1),
    "packet": (COUNT, 1), "packets": (COUNT, 1), "pack": (COUNT, 1), "packs": (COUNT, 1),
    "bunch": (COUNT, 1), "bunches": (COUNT, 1),
    "clove": (COUNT, 1), "cloves": (COUNT, 1),
    "inch": (COUNT, 1), "inches": (COUNT, 1),
    # Hindi measures.
    "चम्मच": (VOLUME, 5), "चमच": (VOLUME, 5), "चाय": (VOLUME, 5),
    "टेबलस्पून": (VOLUME, 15), "बड़ा": (VOLUME, 15),
    "कप": (VOLUME, 240), "प्याला": (VOLUME, 240),
    "कटोरी": (VOLUME, 150), "गिलास": (VOLUME, 250),
    "चुटकी": (MASS, 1), "मुट्ठी": (MASS, 30),
    "ग्राम": (MASS, 1), "किलो": (MASS, 1000), "किलोग्राम": (MASS, 1000),
    "लीटर": (VOLUME, 1000), "मिली": (VOLUME, 1), "मिलीलीटर": (VOLUME, 1),
    "पीस": (COUNT, 1), "नग": (COUNT, 1), "कली": (COUNT, 1), "कलियाँ": (COUNT, 1),
}

# Rough density for ingredients commonly measured by volume but sold by weight.
# grams per ml. Only consulted when a volume measure meets a mass-sold product.
DENSITY_G_PER_ML = {
    "atta": 0.50, "wheat flour": 0.50, "maida": 0.53, "besan": 0.42, "rava": 0.68,
    "suji": 0.68, "rice": 0.77, "basmati rice": 0.77, "poha": 0.35,
    "sugar": 0.85, "salt": 1.20, "jaggery": 0.85,
    "toor dal": 0.80, "moong dal": 0.80, "chana dal": 0.80, "urad dal": 0.80,
    "masoor dal": 0.80, "rajma": 0.75, "chole": 0.75,
    "paneer": 0.60, "curd": 1.03, "yogurt": 1.03, "milk": 1.03,
    "ghee": 0.91, "butter": 0.91, "oil": 0.92, "cooking oil": 0.92,
    "cashew": 0.55, "almond": 0.60, "peanut": 0.60, "coconut": 0.35,
}
DEFAULT_DENSITY = 0.60

# Typical edible weight of one piece. Recipes count produce, Instamart sells it by
# weight, so "three to four potatoes" becomes roughly 600g and then one 1kg pack.
PIECE_WEIGHT_G = {
    "onion": 110, "tomato": 100, "potato": 150, "lemon": 55, "carrot": 80,
    "capsicum": 120, "cucumber": 200, "banana": 120, "apple": 180,
    "brinjal": 100, "green chilli": 5, "garlic": 5, "ginger": 30,
    "coconut": 400, "cauliflower": 600, "cabbage": 800, "lauki": 700,
    "beetroot": 130, "mushroom": 18, "bhindi": 10,
    "cashew": 1.5, "almond": 1.2, "raisins": 0.5, "peanut": 0.8,
    "pav": 45, "bread": 30, "eggs": 55,
}


@dataclass(frozen=True)
class Quantity:
    """A parsed amount, normalised to g / ml / pc."""
    value: float
    unit: str

    def label(self) -> str:
        if self.unit == COUNT:
            n = int(round(self.value))
            return f"{n} pc"
        if self.value >= 1000:
            big = "kg" if self.unit == MASS else "L"
            trimmed = f"{self.value / 1000:.2f}".rstrip("0").rstrip(".")
            return f"{trimmed} {big}"
        trimmed = f"{self.value:.0f}" if self.value >= 10 else f"{self.value:.1f}".rstrip("0").rstrip(".")
        return f"{trimmed} {self.unit}"


def _as_number(token: str) -> Optional[float]:
    token = token.strip().lower()
    if not token:
        return None
    if token in WORD_NUMBERS:
        return float(WORD_NUMBERS[token])
    # unicode fractions
    for glyph, val in (("½", 0.5), ("¼", 0.25), ("¾", 0.75), ("⅓", 1 / 3), ("⅔", 2 / 3)):
        if token == glyph:
            return val
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", token)
    if m:
        denom = float(m.group(2))
        return float(m.group(1)) / denom if denom else None
    try:
        return float(token)
    except ValueError:
        return None


def parse_quantity(text: Optional[str]) -> Optional[Quantity]:
    """Best effort parse of an LLM quantity string.

    Returns None when there is nothing usable, such as "unknown", "to taste" or
    an empty string. Callers should read that as "just get one pack".
    """
    if not text:
        return None
    raw = str(text).strip().lower()
    if not raw or raw in {
        "unknown", "to taste", "as needed", "as required", "some", "n/a",
        # Hindi vagueness. These are honest "not stated" answers, not amounts.
        "थोड़ा", "थोड़ा सा", "थोड़े से", "थोड़ी", "थोड़ी सी", "बहुत सारे",
        "स्वादानुसार", "जरूरत अनुसार", "कुछ",
    }:
        return None

    # Speakers hedge constantly. "about fifteen cashews", "roughly two cups".
    # Drop the hedge so the number behind it still parses.
    raw = re.sub(r"^(?:about|approx\.?|approximately|roughly|around|nearly|some|maybe|like)\s+",
                 "", raw)

    # "3-4" and "three to four" both take the upper bound, which is the safe
    # choice when you are shopping for a recipe.
    raw = re.sub(r"\s*(?:-|–|—|to)\s*", " to ", raw)

    tokens = re.findall(
        r"\d+\s*/\s*\d+|\d+(?:\.\d+)?|[a-z]+|[\u0900-\u097F]+|[½¼¾⅓⅔]", raw)
    if not tokens:
        return None

    # Collect the leading run of numbers, honouring ranges and mixed fractions.
    # "1 1/2 cups" gives 1.5 and "three to four" gives 4.
    numbers: list[float] = []
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "to":
            idx += 1
            continue
        n = _as_number(tok)
        if n is None:
            break
        numbers.append(n)
        idx += 1

    if not numbers:
        amount = 1.0
    elif "to" in tokens[: idx + 1]:
        amount = max(numbers)          # a range takes its upper bound
    elif len(numbers) == 2 and numbers[1] < 1:
        amount = numbers[0] + numbers[1]  # mixed fraction "1 1/2"
    else:
        amount = numbers[0]

    for tok in tokens[idx:]:
        if tok in UNITS:
            canonical, mult = UNITS[tok]
            return Quantity(value=amount * mult, unit=canonical)

    # A bare number with no unit ("3 onions", "2") means pieces.
    if numbers:
        return Quantity(value=amount, unit=COUNT)
    return None


def to_pack_unit(qty: Quantity, pack_unit: str, ingredient: str = "") -> Optional[float]:
    """Convert a parsed quantity into the unit the product is sold in.

    Returns None when the two units genuinely cannot be compared, such as
    "2 cups" of something sold by the piece. In that case one pack is the best
    we can do.
    """
    if qty.unit == pack_unit:
        return qty.value

    density = DEFAULT_DENSITY
    key = (ingredient or "").strip().lower()
    for name, value in DENSITY_G_PER_ML.items():
        if name in key:
            density = value
            break

    if qty.unit == VOLUME and pack_unit == MASS:
        return qty.value * density
    if qty.unit == MASS and pack_unit == VOLUME:
        return qty.value / density

    if qty.unit == COUNT and pack_unit in (MASS, VOLUME):
        # Only convert a piece count when we actually know what a piece weighs.
        # Guessing a default here once turned "15 cashews" into 1.5 kg.
        weight = None
        for name, grams in PIECE_WEIGHT_G.items():
            if name in key:
                weight = grams
                break
        if weight is None:
            return None
        total_g = qty.value * weight
        return total_g if pack_unit == MASS else total_g / density

    # Buying a mass of something sold per piece isn't reliably convertible.
    return None


def units_needed(
    qty: Optional[Quantity],
    pack_size: Optional[float],
    pack_unit: Optional[str],
    ingredient: str = "",
    max_units: int = 10,
) -> int:
    """How many packs to buy. Always at least 1, and capped so that a bad parse
    cannot put 400 kg of atta in the basket."""
    if qty is None or not pack_size or not pack_unit:
        return 1
    required = to_pack_unit(qty, pack_unit, ingredient)
    if required is None or required <= 0:
        return 1
    return max(1, min(max_units, math.ceil(required / pack_size - 1e-9)))


def scale(qty: Optional[Quantity], factor: float) -> Optional[Quantity]:
    """Scale an amount for a different number of people.

    Cooking does not scale perfectly linearly, spices least of all, but for a
    shopping list it is close enough and it is what a person does in their head
    anyway.
    """
    if qty is None or factor == 1:
        return qty
    return Quantity(value=qty.value * factor, unit=qty.unit)
