import pytest

from backend.catalog import MATCH_THRESHOLD, match_product, normalise, repick_pack, resolve_alias


@pytest.mark.parametrize("raw,expected", [
    ("jeera", "cumin seeds"),
    ("haldi", "turmeric powder"),
    ("dahi", "curd"),
    ("wheat flour", "atta"),
    ("pyaz", "onion"),
    ("aloo", "potato"),
    ("kaju", "cashew"),
])
def test_aliases_resolve_hindi_and_synonyms(raw, expected):
    assert resolve_alias(raw) == expected


def test_normalise_strips_recipe_filler():
    assert normalise("finely chopped fresh Onions!") == "onions"
    assert normalise("2 large boiled potatoes") == "2 potatoes"


@pytest.mark.parametrize("name", [
    "paneer", "atta", "jeera", "haldi", "garam masala", "onions",
    "boiled potatoes", "ghee", "fresh cream", "cashews", "green chillies",
])
def test_common_ingredients_match(name):
    item, _, score = match_product(name)
    assert item is not None, f"{name} should match"
    assert score >= MATCH_THRESHOLD


def test_unrelated_item_does_not_match():
    item, _, _ = match_product("gochujang")
    assert item is None


def test_no_substitute_when_nothing_is_remotely_close():
    """Blueberries once suggested toilet cleaner at 48%. A suggestion that bad
    is worse than admitting the item is not stocked."""
    for name in ["gochujang", "wasabi", "truffle oil"]:
        item, substitute, score = match_product(name)
        assert item is None, name
        assert substitute is None, f"{name} should not propose {substitute}"


def test_devanagari_matches():
    """A Hindi transcript arrives in Devanagari. Normalising used to delete it."""
    for hindi, expected in [("आटा", "atta"), ("प्याज़", "onion"),
                            ("पनीर", "paneer"), ("ब्रोकली", "broccoli")]:
        item, _, score = match_product(hindi)
        assert item is not None, hindi
        assert item["base_name"] == expected
        assert score >= MATCH_THRESHOLD


def test_items_instamart_actually_stocks():
    """These all reported "no match" before the catalog was widened."""
    for name in ["broccoli", "chicken breast", "ricotta cheese",
                 "blueberries", "zucchini", "tofu", "olive oil"]:
        item, _, _ = match_product(name)
        assert item is not None, f"{name} should be in the catalog"


def test_picks_smallest_pack_that_covers_the_need():
    small, _, _ = match_product("paneer", required=150)
    assert small["pack_size"] == 200

    large, _, _ = match_product("paneer", required=300)
    assert large["pack_size"] == 500


def test_defaults_to_smallest_pack_when_amount_unknown():
    item, _, _ = match_product("atta")
    assert item["pack_size"] == 1000


def test_repick_pack_upgrades_after_conversion():
    item, _, _ = match_product("paneer")
    assert item["pack_size"] == 200
    assert repick_pack(item, 400)["pack_size"] == 500


def test_catalog_entries_are_well_formed():
    from backend.catalog import _CATALOG

    for entry in _CATALOG:
        assert entry["pack_size"] > 0
        assert entry["pack_unit"] in {"g", "ml", "pc"}
        assert entry["price_inr"] > 0
        assert entry["product_id"].startswith("IM")
    assert len({e["product_id"] for e in _CATALOG}) == len(_CATALOG), "duplicate product_id"
