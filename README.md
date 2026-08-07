# Clip2Cart, a Swiggy Instamart agent (Builders Club demo)

This is my submission for **Swiggy Builders Club**. The idea is pretty simple: paste a cooking video (or its transcript), and an agent figures out what ingredients were mentioned, works out roughly how much of each you'd need, and builds you a cart, the way you'd wish grocery shopping worked after watching a recipe video instead of pausing every ten seconds to write things down.

## Why the cart isn't "real" yet

I don't have Swiggy MCP production access. This project is basically my application for it. Swiggy's Instamart MCP server (`POST mcp.swiggy.com/im`, 16 tools, OAuth 2.1 + PKCE) doesn't have a public sandbox you can just hit without credentials, so there was no way to wire up a real cart today.

Instead of faking that or skipping it, I built the integration boundary to match Swiggy's actual tool contract exactly:

```python
class InstamartClient(ABC):
    def search_products(self, product_name: str) -> (catalog_item, substitute): ...
    def update_cart(self, catalog_item: dict, quantity: str) -> dict: ...
    def get_cart(self) -> list[dict]: ...
```

Right now `LocalInstamartSimulator` implements that against a small local catalog. If this gets approved, a `SwiggyMCPClient` with the exact same three methods, just calling the real `search_products` / `update_cart` / `get_cart` MCP tools instead, drops in without touching anything upstream (the transcript fetching, the LLM extraction, none of it changes). That's the whole point of building it this way.

## How it actually works

1. You paste a YouTube link or transcript text. For a YouTube URL, the transcript gets pulled automatically; for anything else (a Reel, a podcast clip, whatever), you paste the text yourself, since there's no free API for fetching those.
2. The transcript goes to Groq's free Llama 3.3 70B, which pulls out every product mentioned along with a rough quantity guess and a confidence level, as structured JSON.
3. Each item gets fuzzy-matched (via `rapidfuzz`) against a small local grocery catalog. If nothing matches well enough, it's flagged instead of silently dropped, with a nearest substitute suggested.
4. Whatever matched gets added to a basket, and you get a summary, item count, how many matched, and an estimated total in ₹.

## Running it yourself

You'll need **Python 3.9-3.13**, not 3.14. I found this out the hard way: on 3.14, both `youtube-transcript-api` and `rapidfuzz` fail to install because their build tooling doesn't support it yet. Worth checking before you start:

```bash
python3 --version
```

If that says 3.14, use whatever older Python you have installed instead (e.g. `python3.11 -m venv venv` below, or grab one from python.org).

```bash
git clone https://github.com/POLESTARRR/C2C.git
cd C2C

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env              # add a free Groq key, console.groq.com/keys, no card needed

uvicorn backend.main:app --reload
```

Then open `http://localhost:8000`.

**Quickest way to see it work:** skip YouTube and use the "Paste Transcript" tab with this:

```
Today I'm making a simple aloo paratha. You'll need two cups of atta, three to four
boiled potatoes, chopped onions, green chillies, ginger, garam masala, red chilli
powder, salt to taste, and ghee for roasting. Keep some curd on the side to serve.
```

That exercises the full pipeline (extraction, matching, basket) without depending on YouTube's transcript API, which can be a little unpredictable depending on your network.

I ran this exact sequence on a clean machine before writing it down here, so it should just work.

## What's not built (on purpose)

- No real Instamart search or checkout. Everything runs against a ~50-item local catalog until MCP access exists.
- No fetching for Instagram Reels or other platforms. Paste the transcript manually.
- No job queue, no saved history. It's a single paste-and-go flow, kept deliberately small for a one-day build.
