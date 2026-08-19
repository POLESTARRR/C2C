#!/usr/bin/env bash
# Works out WHY the Groq call fails: bad key, or blocked network.
cd "$(dirname "$0")/.." || exit 1

echo ""
echo "=================================================="
echo " GROQ DIAGNOSTIC"
echo "=================================================="

KEY=$(grep -E '^GROQ_API_KEY=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs)

echo ""
echo "[A] The key in .env"
if [ -z "$KEY" ]; then
  echo "    EMPTY. Nothing set in .env."
else
  echo "    length ${#KEY}   starts ${KEY:0:8}   ends ${KEY: -4}"
  case "$KEY" in
    gsk_*) echo "    prefix looks right (gsk_)" ;;
    *)     echo "    WRONG PREFIX. Groq keys start with gsk_" ;;
  esac
fi

echo ""
echo "[B] Raw call WITH your key"
echo "----"
curl -s -i --max-time 25 https://api.groq.com/openai/v1/models \
     -H "Authorization: Bearer $KEY" | head -1
curl -s --max-time 25 https://api.groq.com/openai/v1/models \
     -H "Authorization: Bearer $KEY" | head -c 300
echo ""
echo "----"

echo ""
echo "[C] Raw call with NO key (this is the tell)"
echo "----"
curl -s -i --max-time 25 https://api.groq.com/openai/v1/models | head -1
curl -s --max-time 25 https://api.groq.com/openai/v1/models | head -c 300
echo ""
echo "----"

echo ""
echo "HOW TO READ THIS:"
echo "  [C] says 401 invalid_api_key   -> network is FINE, your key in [B] is the problem"
echo "  [C] says 403 Access denied     -> your NETWORK is blocked, the key is probably fine"
echo "  [B] 200 and [C] 401            -> everything works, rerun preflight"
echo "=================================================="
echo ""
