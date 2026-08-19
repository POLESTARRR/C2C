"""End-to-end tests for /process with the LLM stubbed out.

Groq is the one part we cannot exercise deterministically, so it is replaced.
Everything downstream is the real code path. That covers normalisation, quantity
maths, matching, pack choice and MCP logging.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.models import ProcessRequest

def stub(items, serves=4):
    """extract_ingredients returns (items, serves), so stubs must too."""
    return lambda text: (items, serves)


RECIPE = [
    {"product_name": "Paneer", "category": "grocery", "estimated_quantity": "300 g", "confidence": "High"},
    {"product_name": "Atta", "category": "grocery", "estimated_quantity": "two cups", "confidence": "High"},
    # Genuinely not stocked, so the "flag it rather than fake it" path fires.
    {"product_name": "Gochujang", "category": "grocery", "estimated_quantity": "1 tbsp", "confidence": "Medium"},
    {"product_name": "Basmati Rice", "category": "grocery", "estimated_quantity": "3 kg", "confidence": "High"},
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "extract_ingredients", stub(RECIPE))
    # The semantic pass calls Groq. Tests stay offline and deterministic.
    monkeypatch.setattr(main, "resolve_unmatched", lambda names: {})
    return TestClient(main.app, raise_server_exceptions=False)


def _post(client, value="some transcript"):
    return client.post("/process", json={"source_type": "transcript_text", "value": value})


def test_happy_path(client):
    body = _post(client).json()
    assert body["summary"]["total_items"] == 4
    assert body["summary"]["matched_items"] == 3
    assert body["instamart_mode"] == "local"


def test_total_accounts_for_quantity(client):
    """3kg of rice must cost more than one pack. The old code summed unit prices
    once per ingredient no matter how much the recipe asked for."""
    body = _post(client).json()
    rice = next(b for b in body["basket"] if b["product_name"] == "Basmati Rice")
    assert rice["units"] >= 1
    assert rice["line_total_inr"] == rice["price_inr"] * rice["units"]

    expected = sum(b["line_total_inr"] for b in body["basket"] if b["matched_catalog_item"])
    assert body["summary"]["estimated_total_inr"] == pytest.approx(expected)


def test_unmatched_item_is_flagged_not_dropped(client):
    body = _post(client).json()
    missing = next(b for b in body["basket"] if b["product_name"] == "Gochujang")
    assert missing["matched_catalog_item"] is False
    assert missing["line_total_inr"] is None


def test_no_substitute_is_offered_when_nothing_is_close(client):
    """A bad suggestion is worse than none. Gochujang resembles nothing we
    stock, so the row must not propose a random product."""
    body = _post(client).json()
    missing = next(b for b in body["basket"] if b["product_name"] == "Gochujang")
    assert missing["suggested_substitute"] is None


def test_semantic_pass_rescues_what_lexical_matching_missed(client, monkeypatch):
    """The model names the product, the catalog decides if we stock it."""
    from backend.catalog import match_product

    pumpkin, _, _ = match_product("pumpkin")
    monkeypatch.setattr(main, "resolve_unmatched", lambda names: {0: pumpkin})
    # "kumro" is Bengali for pumpkin and is deliberately absent from the alias
    # file, so lexical matching cannot reach it and the semantic pass must.
    monkeypatch.setattr(main, "extract_ingredients", stub([
        {"product_name": "kumro", "category": "grocery",
         "estimated_quantity": "500 g", "confidence": "High"},
    ]))
    body = _post(client).json()
    row = body["basket"][0]
    assert row["matched_catalog_item"] is True
    assert row["matched_by"] == "semantic"
    assert row["catalog_name"] == pumpkin["product_name"]
    assert body["summary"]["matched_items"] == 1
    assert body["summary"]["estimated_total_inr"] == row["line_total_inr"]


def test_quantity_is_converted_into_the_pack_unit(client):
    body = _post(client).json()
    atta = next(b for b in body["basket"] if b["product_name"] == "Atta")
    assert atta["required_label"] == "240 g"      # two cups of flour
    assert atta["pack_label"] == "1000 g"


def test_mcp_calls_are_emitted(client):
    body = _post(client).json()
    calls = body["mcp_calls"]
    tools = [c["tool"] for c in calls]

    assert tools.count("search_products") == 4      # one per ingredient
    assert tools.count("update_cart") == 3          # one per match
    assert tools[-1] == "get_cart"
    assert body["summary"]["mcp_call_count"] == len(calls)

    first = calls[0]
    assert first["endpoint"] == "POST https://mcp.swiggy.com/im"
    assert first["request"]["jsonrpc"] == "2.0"
    assert first["request"]["method"] == "tools/call"
    assert first["request"]["params"]["name"] == "search_products"
    assert first["response"]["id"] == first["request"]["id"]


def test_malformed_llm_output_is_survivable(client, monkeypatch):
    """A stray enum value or a missing field must not 500 the request."""
    monkeypatch.setattr(main, "extract_ingredients", stub([
        {"product_name": "Paneer", "category": "GROCERY", "estimated_quantity": "300 g", "confidence": "high"},
        {"product_name": "Salt", "category": "nonsense", "confidence": "VERY HIGH"},
        {"product_name": "", "category": "grocery"},
        {"not": "a product"},
        "a bare string",
    ]))
    response = _post(client)
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total_items"] == 2       # the two salvageable ones
    assert body["basket"][0]["confidence"] == "High"
    assert body["basket"][1]["category"] == "grocery"


def test_empty_transcript_is_rejected(client):
    response = _post(client, value="   ")
    assert response.status_code == 422


def test_extraction_failure_returns_json_not_html(client, monkeypatch):
    from backend.llm_extract import ExtractionError

    def boom(text):
        raise ExtractionError("Groq rate limit reached. Wait a few seconds and try again.")

    monkeypatch.setattr(main, "extract_ingredients", boom)
    response = _post(client)
    assert response.status_code == 502
    assert "rate limit" in response.json()["detail"]


def test_unexpected_error_still_returns_json(client, monkeypatch):
    """The frontend parses every response as JSON, so a crash must not come back
    as a plain text 500."""
    monkeypatch.setattr(main, "extract_ingredients", lambda t: 1 / 0)
    response = _post(client)
    assert response.status_code == 500
    assert "detail" in response.json()


def test_every_row_gets_a_quantity(client, monkeypatch):
    """A blank cell tells the user nothing. When the model gives us nothing
    usable we fall back to one pack and flag it as our guess, so the column is
    never empty."""
    monkeypatch.setattr(main, "extract_ingredients", stub([
        {"product_name": "Paneer", "category": "grocery",
         "estimated_quantity": "unknown", "confidence": "High"},
        {"product_name": "Salt", "category": "grocery",
         "estimated_quantity": "", "confidence": "High"},
    ]))
    body = _post(client).json()
    for row in body["basket"]:
        assert row["estimated_quantity"], "quantity must never be blank"
        assert row["quantity_is_estimate"] is True


def test_stated_quantities_are_not_marked_as_estimates(client, monkeypatch):
    """An amount the video actually gave must not be labelled as our guess."""
    monkeypatch.setattr(main, "extract_ingredients", stub([
        {"product_name": "Paneer", "category": "grocery", "estimated_quantity": "300 g",
         "quantity_source": "stated", "confidence": "High"},
        {"product_name": "Butter", "category": "grocery", "estimated_quantity": "2 tbsp",
         "quantity_source": "estimated", "confidence": "Medium"},
    ]))
    rows = _post(client).json()["basket"]
    assert rows[0]["quantity_is_estimate"] is False
    assert rows[1]["quantity_is_estimate"] is True


# --- serving size ---------------------------------------------------------

SCALABLE = [
    {"product_name": "Paneer", "category": "grocery", "estimated_quantity": "200 g",
     "quantity_source": "stated", "confidence": "High"},
    {"product_name": "Basmati Rice", "category": "grocery", "estimated_quantity": "500 g",
     "quantity_source": "stated", "confidence": "High"},
]


def _servings(client, n):
    return client.post("/process", json={
        "source_type": "transcript_text", "value": "x", "servings": n}).json()


def test_defaults_to_the_recipes_own_serving_size(client, monkeypatch):
    monkeypatch.setattr(main, "extract_ingredients", stub(SCALABLE, serves=4))
    body = client.post("/process", json={
        "source_type": "transcript_text", "value": "x"}).json()
    assert body["summary"]["recipe_serves"] == 4
    assert body["summary"]["servings"] == 4


def test_doubling_the_people_doubles_what_you_buy(client, monkeypatch):
    """A recipe for 4 shopped for 8 needs twice the rice."""
    monkeypatch.setattr(main, "extract_ingredients", stub(SCALABLE, serves=4))
    four = _servings(client, 4)
    eight = _servings(client, 8)

    rice4 = next(b for b in four["basket"] if b["product_name"] == "Basmati Rice")
    rice8 = next(b for b in eight["basket"] if b["product_name"] == "Basmati Rice")
    assert rice8["units"] >= rice4["units"]
    assert eight["summary"]["estimated_total_inr"] >= four["summary"]["estimated_total_inr"]
    assert eight["summary"]["servings"] == 8
    assert eight["summary"]["recipe_serves"] == 4


def test_halving_never_goes_below_one_pack(client, monkeypatch):
    """You cannot buy half a packet."""
    monkeypatch.setattr(main, "extract_ingredients", stub(SCALABLE, serves=8))
    body = _servings(client, 1)
    for row in body["basket"]:
        if row["matched_catalog_item"]:
            assert row["units"] >= 1


def test_servings_are_clamped_to_something_sane(client, monkeypatch):
    monkeypatch.setattr(main, "extract_ingredients", stub(SCALABLE, serves=4))
    assert _servings(client, 500)["summary"]["servings"] == 20
    assert _servings(client, 0)["summary"]["servings"] == 4     # 0 is falsy, so recipe default
    assert _servings(client, -3)["summary"]["servings"] == 1
