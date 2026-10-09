# Reply to Swiggy Builders Club

Send today. Their deadline is Friday, so arriving early reads well.

Subject: Re: Share a demo of Clip2Cart

## The email

Hi team,

Thanks for the nudge. Good timing, I had just finished a round of work on it.

Here is Clip2Cart: **[PASTE YOUR VIDEO LINK HERE]** (about 5 minutes)

The idea in one line. You paste a cooking video link and it builds you an
Instamart cart.

Three things in there I would point you at.

**It works from just a link.** Most cooking videos have no captions, and almost
no Reels or Shorts do. When captions are missing it pulls the audio and
transcribes it with Whisper, and it reads the video description too, because
that is where creators usually put the actual amounts. In the video that is a
Hindi paneer butter masala recipe going from a bare URL to a costed cart of
seventeen items, with nothing typed.

**It works in Hindi.** Instamart is an Indian product and a lot of food content
is in Hindi, so a Devanagari transcript has to work as well as an English one.
The results show what the speaker said and the English term it matched on
underneath, so you can see how each row was resolved.

**The quantities are real.** It does not just pull out ingredient names. Two
tablespoons of butter becomes 27 grams becomes one 100 gram pack. Ask for three
kilos of rice and it puts three packs in.

And where a video never states an amount, which is common, it fills in a
sensible quantity for three or four people and tags it as an estimate. You get a
complete cart either way, but you can always tell which numbers came from the
chef and which are ours. Anything it cannot match is flagged rather than
silently dropped.

One thing that is not in the video. After recording it I added a serving size
control, because the obvious question after any recipe is how many people you
are feeding. It works out what the recipe itself serves, and scaling it adjusts
every amount and re-picks pack sizes. That paneer recipe serves four at around
fourteen hundred rupees, and shopping the same recipe for ten takes it to two
packs of paneer and about seventeen hundred. It is in the repo if you want to
try it.

On the integration. Every basket operation maps to a real Instamart MCP call and
the app shows you the exact `tools/call` envelope it produces, so you can check
it rather than take my word for it. That is `search_products`, `update_cart` and
`get_cart` against `mcp.swiggy.com/im`. The OAuth setup came from your own
discovery document rather than guesswork, and the client that talks to you is
written and sitting behind one environment variable.

What it is running against today is a local catalog of 262 products, not your
real range. I want to be precise about why, because it is not a technical
blocker. Your MCP endpoint is reachable and the OAuth flow works. I could have
pointed this at live accounts. I did not want to aim a side project at real
customer carts without Swiggy being comfortable with it first, so the demo runs
on a stand in that speaks the identical interface. The day you are happy for it
to talk to production, it is a configuration change on my side, not a rewrite,
and nothing above that layer changes.

Where I would love your help. The Instamart tool argument schemas are not in the
public docs, so I inferred them from the tool descriptions and reconcile against
`tools/list` on connect. If someone on the Instamart side can tell me where I
have got that wrong, I will fix it the same day.

And the bigger question I would like to put to you. I do not think this belongs
on a website of mine. I think it belongs inside Instamart, and it would look
something like this.

Someone watching a recipe Reel taps share, and Swiggy Instamart appears in the
share sheet next to WhatsApp. No new app, no link to copy. Instamart runs what
you saw in the video, then shows a review screen: here are the seventeen things
this recipe needs, here is the total, and every line has a checkbox so you untick
the salt and oil you already have. One tap adds the rest to the normal cart, and
from there it is your existing checkout and delivery.

The review step is the part I would argue hardest for. You never silently drop
seventeen items into someone's cart. Showing them and letting them choose is what
separates a gimmick people try once from something they use every week.

From an engineering point of view most of it already exists on your side. Search,
cart and checkout are all there. The genuinely new pieces are transcript
retrieval, ingredient extraction and the quantity maths, which are precisely the
three things I have built and tested. It is a new entry point into a funnel you
already own, not a new funnel.

I would really like to talk to whoever owns that surface about what it would
take.

Code is at https://github.com/POLESTARRR/C2C

Happy to walk anyone through it live if that is easier.

Thanks,
Dhruv

## Notes on why it is worded this way

* **"I did not want to point a side project at real customer accounts"** is the
  important sentence. Your MCP OAuth is publicly reachable, so nothing is
  actually stopping you technically. Claiming you are blocked would be wrong and
  they would know it. Choosing restraint reads far better than waiting for
  permission, and it is what you actually did.
* The ask at the end is a conversation, not a favour. Much easier to say yes to.
* Do not apologise for the local catalog. It is framed as a deliberate choice,
  because it is one.
* Keep it this length. The video carries the rest.
