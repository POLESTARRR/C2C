# The email to send

Reply to their original thread. Subject stays as it is.

Attach: Clip2Cart.pdf
Video: unlisted YouTube link, tested in a private window

---

Hi team,

Thanks for the nudge, and sorry for the slightly long video. Once I started
showing things I did not want to skip the parts I am proud of.

Here it is: [YOUR VIDEO LINK]

Clip2Cart is simple to describe. You paste a cooking video link and it builds you
an Instamart cart. In the video I paste a Hindi paneer butter masala recipe that
has no captions at all, and about thirty seconds later there is a seventeen item
cart with a total.

A few things I would love you to notice.

It reads the audio when there are no captions, and it reads the description too,
because that is where creators actually write the amounts. It works properly in
Hindi, which felt non negotiable given who Instamart is for. And it does the
boring maths that makes a cart usable, so two tablespoons of butter becomes
twenty seven grams becomes one hundred gram pack. You can also tell it how many
people you are cooking for and everything rescales.

The part most relevant to you is that every basket action is a real MCP call, and
the app shows you the exact payload it would post to mcp.swiggy.com/im. It is
running on a local catalog right now, and I want to be straight about why. Your
endpoint is reachable and the OAuth works, so that was not a blocker. I just did
not want to point a side project at real customer carts before Swiggy was
comfortable with it. Switching it over is a config change, not a rewrite.

I have attached a short PDF with the detail, in case that is easier to pass
around internally. Code is at https://github.com/POLESTARRR/C2C

The thing I would really like is a conversation. I think this belongs inside
Instamart rather than on a site of mine, as a share sheet target and a review
screen before anything hits your cart. If there is someone who owns that surface,
I would love twenty minutes with them.

Thanks for running the Builders Club.

Dhruv
