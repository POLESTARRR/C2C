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

## How it works

1. You paste a video link or transcript text. For a link, captions are tried
   first because they are instant and free. Most cooking videos have none, so
   when captions are missing the audio track gets pulled and transcribed with
   Whisper on Groq. That fallback is the difference between the URL box working
   on a handful of captioned videos and working on almost all of them.
2. The transcript goes to Groq, running `openai/gpt-oss-120b` on their free tier. It extracts every product mentioned with a quantity and a confidence level, as structured JSON.
3. Quantities get parsed and converted into the unit the product is actually sold in. "Two cups of atta" becomes 240 g. "Three to four potatoes" becomes 600 g. Then each amount is matched to a real pack size. A recipe needing 3 kg of rice buys three 1 kg packs instead of one.
4. Each item is matched against the 262 item catalog in three stages, described below.
5. You get a basket with per line quantities and totals, plus a **wire log** of every MCP call the run made.

You can also say how many people you are cooking for. The app works out what the
recipe itself serves, and scaling to a different number adjusts every amount and
re-picks pack sizes, so a recipe for four shopped for ten buys two packs of
paneer rather than one.

Where a video never states an amount, which is common, a sensible quantity for
the serving size is filled in and tagged as an estimate in the results. You
always get a complete cart, and you can always tell which numbers came from the
video and which are ours.

Transcripts also include the video title and description, because creators
usually list exact amounts there even when they never say them out loud.

## How matching works

Three stages, cheapest first.

**Stage 1, the model normalises.** The extractor returns both what the speaker
actually said and a plain English grocery term for it. "प्याज़" comes back with
`canonical_name: "onion"`. This is what makes a Hindi video work at all, and it
generalises to any language and any phrasing rather than depending on a list of
words somebody remembered to add.

**Stage 2, lexical matching.** An alias pass covering about 300 terms, including
Devanagari, then fuzzy matching with `rapidfuzz` against a clean base name with
the pack size stripped out. Fast and free, and it resolves the large majority.

**Stage 3, semantic rescue.** Anything still unmatched goes to the model in one
batched call, which is asked to NAME the product in plain English. That answer
goes back through stage 2. So "kumro" becomes pumpkin and gets matched, while
"gochujang" comes back as "Korean chili paste" and correctly finds nothing.

The important property is that the model never picks the product. It only
supplies the word. The catalog remains the only thing that decides what is
actually stocked, so no ingredient can be matched to a product that does not
exist. The results table marks any row rescued by stage 3.

### Substitutes

If nothing clears the match threshold, the nearest catalog item is offered as a
substitute, but only when it scores above a floor. Before that floor existed,
blueberries suggested toilet cleaner and broccoli suggested chickpeas, purely
because those strings happened to be the least dissimilar. A bad suggestion
costs more trust than an honest "not stocked".

## The Instamart integration

Every basket operation maps to one of Swiggy's real Instamart MCP tools. Those are `search_products`, `update_cart` and `get_cart`. The app shows you the exact JSON-RPC envelope it produces:

```json
{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": { "name": "search_products", "arguments": { "query": "paneer" } }
}
```

Two implementations satisfy the same three method contract:

| | |
|---|---|
| `LocalInstamartSimulator` | runs against the bundled 262 item catalog and records the MCP payload each operation maps to |
| `SwiggyMCPClient` | makes those same calls for real against `POST mcp.swiggy.com/im` |

Nothing upstream knows which one it is talking to. Not the transcript fetching, not the LLM extraction, not the quantity maths. The `INSTAMART_MODE` variable picks.

### What is real and what is not

The OAuth details in `swiggy_mcp_client.py` were read from Swiggy's own discovery document at `/.well-known/oauth-authorization-server`. That covers the issuer, the authorize and token endpoints, PKCE S256 and the `mcp:tools` scope. None of it is guessed.

The tool names come from Swiggy's published reference. The argument shapes are not public, so they are my best reading of the tool descriptions. `SwiggyMCPClient.list_tools()` calls `tools/list` on connect to reconcile against the server's real schema rather than trusting them. The wire log labels these as inferred instead of pretending otherwise.

The default build runs on the local catalog so the demo does not depend on live service state or a logged in account. Switching to live is one environment variable.

### Safety

`SwiggyMCPClient` is restricted to search and cart operations. The `checkout` and `confirm_order` tools and the payment tools are refused by `_guard_tool`. No code path here can spend money or dispatch a delivery.

## Running it yourself

You will need **Python 3.9 to 3.13**, not 3.14. On 3.14 both `youtube-transcript-api` and `rapidfuzz` fail to install because their build tooling does not support it yet:

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

The quickest way to see it work is the "Paste Transcript" tab with `demo/paneer-butter-masala.txt`. Or use this shorter one:

```
Today I'm making a simple aloo paratha. You'll need two cups of atta, three to four
boiled potatoes, chopped onions, green chillies, ginger, garam masala, red chilli
powder, salt to taste, and ghee for roasting. Keep some curd on the side to serve.
```

That exercises the whole pipeline without depending on YouTube's transcript API, which is unofficial and gets IP blocked unpredictably.

### Where transcripts come from

| Source | When | Speed |
|---|---|---|
| Captions | The video has them | Instant |
| Whisper audio | No captions | About 10 seconds for a short video |
| Pasted | You typed it in | Instant |

The results page shows which of the three was used, so you always know whether
you are looking at the uploader's own captions or a machine transcription.

### Long videos

Groq caps a single request at 25 MB. That is a limit on one request, not on how
much audio the app can handle.

Before uploading, the video is stripped to 16 kHz mono speech audio. Whisper
resamples to that internally anyway, so nothing it would have used is lost, and
a real six minute recipe went from 27.3 MB to 1.5 MB. If the result is still
over the cap, which means a genuinely long recording, it is split into 20 minute
pieces, transcribed separately and joined back together.

In practice that means roughly 100 minutes of speech fits in a single request
and anything longer is chunked automatically, up to a 4 hour ceiling that exists
only to stop a runaway download. A 51 minute recording splits into three pieces
of about 4.7 MB each.

ffmpeg comes from the `imageio-ffmpeg` package in `requirements.txt`, so there
is nothing to install system wide. If it is somehow unavailable the app still
works for short videos and says so plainly for long ones.

### If the model stops working

Groq retires models fairly often. If you see an error saying the model does not
exist, list what your key can actually reach:

```bash
curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"
```

Then set `GROQ_MODEL` in `.env` to one of those ids. No code change needed.

Also worth knowing: Groq sits behind Cloudflare, which refuses datacenter and VPN
addresses. If you get "Access denied, please check your network settings", turn
your VPN off. That is a network block, not a bad key.

### Going live against Swiggy MCP

```bash
# 1. Start the app, then visit http://localhost:8000/auth/login
#    This registers the client, runs OAuth 2.1 with PKCE, and stores the token in process.
# 2. Flip the mode and restart.
INSTAMART_MODE=mcp uvicorn backend.main:app
```

Without a token the app says so plainly and falls back. It never fakes a successful cart.

### Tests

```bash
pytest -q      # 90 tests covering quantity parsing, catalog matching and /process end to end
```

The LLM is stubbed in tests. Everything downstream is the real code path.

## What is not built, on purpose

* No Instagram or TikTok fetching. Both demand a logged in session before they
  will serve a video, and shipping someone else's cookies is not something I
  want in this. The app says so plainly and points you at the paste box.
* The catalog is 262 representative items, not Instamart's real 40,000 product
  range. Against the live MCP server, `search_products` replaces it and this
  limitation disappears.
* No checkout. That is deliberate. See the safety note above.
* No job queue and no saved history. It is a single paste and go flow, kept small on purpose.
