#!/usr/bin/env bash
# Clip2Cart preflight. Run this before recording.
# Usage:  bash demo/preflight.sh

cd "$(dirname "$0")/.." || exit 1
PASS=0; FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

echo ""
echo "=================================================="
echo " CLIP2CART PREFLIGHT"
echo "=================================================="

# 1. interpreter
echo ""
echo "[1/5] Python"
if [ ! -x venv/bin/python ]; then
  bad "venv not found. Run: python3 -m venv venv"
  echo ""; echo "Stopping here."; exit 1
fi
PY=./venv/bin/python
VER=$($PY --version 2>&1)
ok "$VER"

# A renamed project folder leaves the venv pointing at a path that no longer
# exists. `source venv/bin/activate` then silently does nothing and you end up
# on the system Python without the dependencies.
WANT="$(pwd)/venv"
HAVE=$(grep -m1 '^VIRTUAL_ENV=' venv/bin/activate | cut -d'"' -f2)
if [ "$HAVE" = "$WANT" ]; then
  ok "venv paths are consistent"
else
  bad "venv points at $HAVE but lives at $WANT"
  echo "        The project folder was renamed. Repair it with:"
  echo "        grep -rl '$HAVE' venv/bin/ | xargs sed -i '' 's|$HAVE|$WANT|g'"
fi

# 2. dependencies
echo ""
echo "[2/5] Dependencies"
if $PY -c "import fastapi, uvicorn, groq, rapidfuzz, pydantic, httpx, dotenv" 2>/dev/null; then
  ok "all imports resolve"
else
  bad "something is missing. Run: ./venv/bin/python -m pip install -r requirements.txt"
fi

# 3. tests
echo ""
echo "[3/5] Test suite"
TEST_OUT=$($PY -m pytest tests/ -q 2>&1 | tail -1)
if echo "$TEST_OUT" | grep -q "passed" && ! echo "$TEST_OUT" | grep -q "failed"; then
  ok "$TEST_OUT"
else
  bad "$TEST_OUT"
fi

# 4. the one that actually matters
echo ""
echo "[4/5] Groq live call  <-- the important one"
GROQ_OUT=$($PY - <<'PYEOF' 2>&1
import os, sys
from dotenv import load_dotenv
load_dotenv(".env")
key = os.environ.get("GROQ_API_KEY", "")
if not key:
    print("NOKEY"); sys.exit()
try:
    from groq import Groq
    from backend.llm_extract import MODEL   # whatever the app really uses
    r = Groq(api_key=key, timeout=30.0).chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "reply with the single word: ok"}])
    print("OK:" + MODEL + " -> " + r.choices[0].message.content.strip()[:15])
except Exception as e:
    print("ERR:" + type(e).__name__ + ":" + str(e)[:160])
PYEOF
)
case "$GROQ_OUT" in
  OK:*)    ok "Groq responded (${GROQ_OUT#OK:})" ;;
  NOKEY)   bad "GROQ_API_KEY is empty in .env" ;;
  *RateLimit*) bad "rate limited. Wait 60s and run this again." ;;
  *Authentication*|*PermissionDenied*|*403*) bad "call refused. Could be the key OR a blocked network. Run: bash demo/diagnose_groq.sh" ;;
  *)       bad "$GROQ_OUT" ;;
esac

# 5. port
echo ""
echo "[5/5] Port 8000"
HOLDER=$(lsof -nP -iTCP:8000 -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "$HOLDER" ]; then
  WHAT=$(ps -p "$HOLDER" -o command= 2>/dev/null | cut -c1-70)
  bad "port 8000 is taken by PID $HOLDER"
  echo "        $WHAT"
  echo "        Free it with:  kill $HOLDER"
  echo "        Or run the app on another port:  uvicorn backend.main:app --port 8080"
else
  ok "free"
fi

echo ""
echo "=================================================="
if [ "$FAIL" -eq 0 ]; then
  echo " ALL CLEAR. $PASS checks passed. Next: uvicorn backend.main:app --port 9000"
else
  echo " $FAIL FAILED, $PASS passed. Fix the FAIL lines above."
fi
echo "=================================================="
echo ""
