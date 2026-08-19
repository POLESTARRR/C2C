import pytest

from backend.quantity import Quantity, parse_quantity, to_pack_unit, units_needed


@pytest.mark.parametrize("text,value,unit", [
    ("2 cups", 480, "ml"),
    ("two cups of atta", 480, "ml"),
    ("250 g", 250, "g"),
    ("1 kg", 1000, "g"),
    ("1/2 kg", 500, "g"),
    ("half a cup", 120, "ml"),
    ("1 1/2 cups", 360, "ml"),
    ("2 tbsp", 30, "ml"),
    ("1 teaspoon", 5, "ml"),
    ("a pinch", 1, "g"),
    ("500ml", 500, "ml"),
    ("1 litre", 1000, "ml"),
    ("12 eggs", 12, "pc"),
    ("3", 3, "pc"),
])
def test_parses_common_forms(text, value, unit):
    qty = parse_quantity(text)
    assert qty is not None, text
    assert qty.value == pytest.approx(value)
    assert qty.unit == unit


@pytest.mark.parametrize("text", ["unknown", "to taste", "as needed", "", None])
def test_unusable_quantities_are_none(text):
    assert parse_quantity(text) is None


@pytest.mark.parametrize("text,expected", [
    ("three to four", 4),
    ("3-4", 4),
    ("2 to 3", 3),
    ("6 to 7 cloves", 7),
])
def test_ranges_take_the_upper_bound(text, expected):
    """A shopper buying for a recipe rounds up, not down."""
    qty = parse_quantity(text)
    assert qty.value == expected


def test_volume_to_mass_uses_ingredient_density():
    # 2 cups = 480ml of atta at ~0.5 g/ml -> ~240g
    grams = to_pack_unit(parse_quantity("2 cups"), "g", "atta")
    assert 230 <= grams <= 250


def test_piece_count_converts_only_when_weight_is_known():
    assert to_pack_unit(parse_quantity("4"), "g", "onion") == pytest.approx(440)
    # Nothing sensible to say about "10 strands" of saffron
    assert to_pack_unit(parse_quantity("10"), "g", "saffron strands") is None


@pytest.mark.parametrize("qty,pack,unit,ingredient,expected", [
    ("2 cups", 1000, "g", "atta", 1),
    ("3 kg", 1000, "g", "rice", 3),
    ("300 g", 200, "g", "paneer", 2),
    ("300 g", 500, "g", "paneer", 1),
    (None, 1000, "g", "atta", 1),
])
def test_units_needed(qty, pack, unit, ingredient, expected):
    assert units_needed(parse_quantity(qty) if qty else None, pack, unit, ingredient) == expected


def test_units_needed_is_capped():
    """A bad parse must not put 400kg of atta in the basket."""
    assert units_needed(Quantity(400_000, "g"), 1000, "g", "atta") == 10


def test_label_formatting():
    assert Quantity(1500, "g").label() == "1.5 kg"
    assert Quantity(240, "g").label() == "240 g"
    assert Quantity(4, "pc").label() == "4 pc"


@pytest.mark.parametrize("text,value,unit", [
    ("about fifteen", 15, "pc"),
    ("roughly two cups", 480, "ml"),
    ("around 250 g", 250, "g"),
    ("approximately 1 kg", 1000, "g"),
])
def test_hedged_amounts_still_parse(text, value, unit):
    """People say "about fifteen cashews". The hedge must not eat the number."""
    qty = parse_quantity(text)
    assert qty is not None, text
    assert qty.value == pytest.approx(value)
    assert qty.unit == unit
