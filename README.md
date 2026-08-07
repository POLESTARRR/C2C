# Clip2Cart, a Swiggy Instamart agent (Builders Club demo)

This is my submission for Swiggy Builders Club. The idea is simple. You paste a cooking video or its transcript, and an agent works out what ingredients were mentioned, how much of each you actually need, and builds you a cart. It is how grocery shopping should work after you watch a recipe video, instead of pausing every ten seconds to write things down.

## Why the cart is not talking to real Instamart yet

I do not have Swiggy MCP production access, and this project is my case for getting it. Swiggy's Instamart MCP server is reachable though. It publishes a full OAuth discovery document, dynamic client registration works, and I registered this app against it while building. So nothing here was technically blocked.

What I chose not to do is point a side project at real customer accounts and real carts before Swiggy was comfortable with that. So the demo runs against a local catalog that speaks the exact same interface a live integration would use.

Instead of faking that or skipping it, I built the integration boundary to match Swiggy's actual tool contract exactly.

```python
class InstamartClient(ABC):
    def search_products(self, product_name: str) -> (catalog_item, substitute): ...
    def update_cart(self, catalog_item: dict, quantity: str) -> dict: ...
    def get_cart(self) -> list[dict]: ...
```

Right now `LocalInstamartSimulator` implements that against a local catalog. `SwiggyMCPClient` implements the exact same three methods against the real MCP server at `mcp.swiggy.com/im`, and it is already written and tested. Switching between them is one environment variable, `INSTAMART_MODE`. Nothing upstream changes, not the transcript fetching, not the extraction, none of it.

## How it actually works

1. You paste a video link or transcript text. For a link, captions are tried first because they are instant and free. Most cooking videos have none, so when captions are missing the audio track is pulled and transcribed with Whisper. The video description is read too, since creators often write the real amounts there even when they never say them aloud.
2. The transcript goes to Groq, running `openai/gpt-oss-120b` on their free tier. It pulls out every product mentioned along with a quantity and a confidence level, as structured JSON.
3. Each item is matched against the catalog in three stages. The model first normalises the ingredient into a plain English grocery term, so a Hindi or regional word still resolves. An alias and fuzzy matching pass then handles the bulk of it. Anything still unmatched goes back to the model to be named, and that name is matched again rather than trusted outright, so the catalog always decides what is actually stocked.
4. Whatever matched gets added to a basket with a real quantity, converted into the unit the product is sold in and mapped onto a pack size. You can also say how many people you are cooking for, and every amount rescales.

## Running it yourself

You will need Python 3.9 to 3.13, not 3.14. On 3.14 both `youtube-transcript-api` and `rapidfuzz` fail to install because their build tooling does not support it yet.

```bash
python3 --version
```

If that says 3.14, use an older Python such as `python3.11 -m venv venv`, or grab one from python.org.

```bash
git clone https://github.com/POLESTARRR/C2C.git
cd C2C

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env              # add a free Groq key from console.groq.com/keys, no card needed

uvicorn backend.main:app --reload
```

Then open `http://localhost:8000`.

The quickest way to see it work is the Paste Transcript tab with this:

```
Today I'm making a simple aloo paratha. You'll need two cups of atta, three to four
boiled potatoes, chopped onions, green chillies, ginger, garam masala, red chilli
powder, salt to taste, and ghee for roasting. Keep some curd on the side to serve.
```

That exercises the whole pipeline without depending on YouTube's transcript API, which is unofficial and can be unpredictable depending on your network.

## What is not built, on purpose

- No Instagram or TikTok fetching. Both demand a logged in session before they will serve a video, and shipping someone else's cookies is not something I want in this. Paste the transcript instead.
- No checkout. The `checkout` and `confirm_order` tools and the payment tools are refused in code, not merely left unused. A recipe agent should build your cart. It should never be able to spend your money.
- No job queue and no saved history. It is a single paste and go flow, kept small on purpose.
