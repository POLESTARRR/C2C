# Clip2Cart, read this aloud

Everything below the line is speech. Read it straight through.

Glance at this once before you press record, then forget it and just read.

If you want it shorter, skip the paragraph marked OPTIONAL near the end. That
saves about forty seconds and loses the least.

> YouTube URL tab, paste, Generate Basket, wait, scroll the table, go up and set
> people to 8, Generate Basket again, scroll down, open the wire log, cut to the
> editor, done.

Paste this link: `https://youtu.be/7k_CXe0AejQ`

---

Hi, I am Dhruv. This is Clip2Cart.

I watch a cooking video, I want to make it, and now I need seventeen things from
Instamart. That is seventeen separate searches, so usually I just do not bother.
Clip2Cart turns the video into the cart.

This is a paneer butter masala recipe. It is in Hindi, and it has no captions,
which is true of almost every Reel. I am not typing anything. Just the link.

While that runs, here is what is happening. It looked for captions first, because
those are instant. There were none, so it is downloading the audio and
transcribing it with Whisper, and reading the video description at the same time,
because that is where creators usually put the real amounts even when they never
say them out loud. Without that fallback, paste a link only works on the few
videos that happen to be captioned.

And there it is. Seventeen ingredients, all seventeen matched, with a total. From
a link, in Hindi, with no captions anywhere.

Under each ingredient is what the chef said, and below it the English product it
matched to. Maida to refined flour. Kaju to cashews. That is not a translation
table I typed by hand, the model normalises each ingredient into a plain English
grocery term.

But it never picks the product, it only supplies the word. The catalog decides
what is actually in stock, so nothing can be added to your cart that does not
exist.

Now this column, because real cooking videos are messy. Where the amount is in
the video or the description, it uses it. Where it is never given, it fills in a
sensible amount and tags it as an estimate. You get a complete cart either way,
but you can always tell which numbers came from the chef and which are ours.

And this column turns each amount into what you actually buy. Two tablespoons of
butter is twenty seven grams, so it buys one hundred gram pack. It reads the
quantity, converts it into the unit Instamart sells in, and picks the smallest
pack that covers you. Ask for three kilos of rice and it puts three packs in.

Which brings me to the question you always ask after a recipe. How many people am
I feeding. This one is written for four. Let me say eight.

Everything recalculates. Every amount that changed is highlighted, and where a
bigger pack is needed it moves you onto the bigger pack.

And when it cannot find something at all, it says so, rather than quietly
dropping it or swapping in something random. I would rather show a gap than hide
one.

Now the part that matters most for Swiggy.

Everything that just happened to that basket is a real MCP call, and this panel
shows the exact payload. Tools slash call. Search products, update cart, get
cart. Your tool names, posted to mcp dot swiggy dot com slash i m.

This is not a drawing of what an integration might look like. The OAuth
configuration came from your own discovery document, not guesswork, and the
client that talks to your server is already written, sitting behind one
environment variable.

And checkout and the payment tools are blocked in code. Not unused, refused. A
recipe agent should build your cart. It should never spend your money.

OPTIONAL. One honest note. These products come from a local catalog, not Instamart's real
range. That is a choice, not a wall I hit. Your MCP endpoint is reachable and the
OAuth flow works. I did not want to aim a side project at real customer carts
without Swiggy being comfortable with that first. The day you are, it is a
configuration change, not a rewrite.

So where does this actually live? Not on a website of mine. I think it belongs
inside Instamart.

Someone watching a recipe Reel taps share, and Swiggy Instamart shows up in the
share sheet, right next to WhatsApp. No new app, no link to copy.

Instamart does everything you just watched, then shows a review screen. Seventeen
items, the total, and every line on a checkbox so you untick the salt and oil you
already have. One tap adds the rest to your normal cart, and from there it is
just Instamart.

That review step is the part I would insist on. You never silently drop seventeen
items into somebody's cart. You show them and let them choose.

And most of this already exists on your side. Search, cart and checkout are all
there. The new parts are the transcript, the extraction and the quantity maths,
which are exactly the three things I have built. A new way into a funnel you
already have, not a new funnel.

Food content is where cooking intent starts, and right now that intent
evaporates, because rebuilding the ingredient list by hand is too much effort.
When it converts, it is a seventeen item basket from somebody who has already
decided what they are cooking. Very different from somebody typing milk.

I would love to talk to whoever owns this part of Instamart about building it
properly. Code is on GitHub, link below. Thanks for the Builders Club.
