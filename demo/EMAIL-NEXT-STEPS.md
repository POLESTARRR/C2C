# Follow-up email, send now to builders@swiggy.in

This is separate from EMAIL-DRAFT.md / EMAIL-SHORT.md. Those got you approved.
This one is post-approval, asks two specific things, and should go out now
that the agreement is signed rather than waiting for the staging email.

Subject: Clip2Cart, two quick questions now that we're approved

---

Hi team,

Signed the agreement, thanks for the quick turnaround on it. While I wait on
staging access, two things I'd rather ask now than guess at.

The first is the redirect URI for my production deploy,
https://clip2cart.onrender.com/auth/callback. It's not on the allowlist yet,
I checked with your own check-redirect-uri endpoint and got a clean false.
Someone on your manifest repo hit the exact same wall (issue #89), so I don't
think it's anything specific to my setup, just the domain not being added on
your end yet. Since onboarding is done on my side now, could this get added.
Worth knowing the live link can take 20 to 30 seconds to load on a cold hit,
it's on Render's free tier, so if you try it and nothing happens for a bit
that's why, not a crash.

The second is smaller but I'd rather not build around a guess. Your docs say
staging runs at mcp-staging.swiggy.com, but don't say whether the OAuth side
of it, authorize, token, register, moves there too or stays on
mcp.swiggy.com/auth like production. I've already got the client set up to
point at either one once I know, just don't want to wire it up wrong and have
it quietly work against the wrong host.

Repo's at https://github.com/POLESTARRR/C2C if it's useful to have open while
reading this.

Thanks again for the club, looking forward to getting this properly wired up.

Dhruv

---

## Why these two and not more

* Both are things only Swiggy can answer, nothing here is a workaround.
* Redirect URI: asking during onboarding is the documented process, not a
  favour. Worth sending today rather than after staging arrives.
* Staging auth host: the code already defaults to production and takes an
  override (SWIGGY_MCP_AUTH_HOST) for exactly this, so the answer plugs in
  directly, no rewrite either way.
* Kept to two questions. Anything longer competes with the demo email for
  attention.
