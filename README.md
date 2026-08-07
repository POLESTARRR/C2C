# Recipe-to-Cart  a Swiggy Instamart Agent(FOR SWIGGY BUILDERS CLUB)

Paste a cooking video link (or its transcript). The agent extracts every ingredient
mentioned, infers a quantity, matches each one against a product catalog, and builds
a ready-to-checkout basket.

Built as a demo for **Swiggy Builders Club**.

## Why the cart is simulated today

Swiggy's Instamart MCP server (`POST mcp.swiggy.com/im`, 16 tools, OAuth 2.1 + PKCE)
has no public unauthenticated sandbox — getting a real bearer token requires the
same production-access application this project is meant to support. So the
"Instamart" catalog and cart are simulated locally (`backend/data/catalog.json`,
`backend/instamart_client.py`), but the interface deliberately mirrors Swiggy's
real tool names:

```python
class InstamartClient(ABC):
    def search_products(self, product_name: str) -> (catalog_item, substitute): ...
    def update_cart(self, catalog_item: dict, quantity: str) -> dict: ...
    def get_cart(self) -> list[dict]: ...
```

`LocalInstamartSimulator` implements this today. Once MCP access is granted, a
`SwiggyMCPClient` with the same method signatures — backed by real calls to
Swiggy's `search_products` / `update_cart` / `get_cart` MCP tools — drops in with
no changes anywhere else in the pipeline.

## How it works

1. **Input** — a YouTube URL (transcript fetched automatically) or pasted transcript
   text (for Reels or any other source with no free transcript API).
2. **Extraction** — the transcript is sent to Groq's free Llama 3.3 70B API, which
   returns a JSON list of `{product_name, category, estimated_quantity, confidence}`.
3. **Matching** — each extracted product is fuzzy-matched (`rapidfuzz`) against the
   local catalog. Unmatched items get a suggested substitute instead.
4. **Basket** — matched items are "added to cart" via `LocalInstamartSimulator`;
   the response includes the basket, an item count, and an estimated ₹ total.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # add your free Groq API key — console.groq.com, no card needed
uvicorn backend.main:app --reload
```

Open `http://localhost:8000`.

## Limitations

- No real Instamart search or checkout — simulated against a ~50-item local catalog.
- Instagram Reels have no free transcript API — paste the caption/transcript manually.
- Single-shot only: no job queue, no history/persistence (kept out of scope for the
  one-day demo).
