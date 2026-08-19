#!/usr/bin/env bash
# Test a video URL before you record with it.
# Usage:  bash demo/try_video.sh "https://www.youtube.com/watch?v=..."
cd "$(dirname "$0")/.." || exit 1

URL="$1"
PORT="${PORT:-9000}"
if [ -z "$URL" ]; then
  echo "Usage: bash demo/try_video.sh \"<video url>\""; exit 1
fi

if ! curl -s --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "The app is not running on port $PORT."
  echo "Start it first:  uvicorn backend.main:app --port $PORT"
  exit 1
fi

echo ""
echo "Testing: $URL"
echo "This can take up to a minute if the video has no captions."
echo ""

# Build the payload in a file so shell quoting cannot mangle it.
URL="$URL" ./venv/bin/python -c 'import json,os;open("/tmp/c2c_payload.json","w").write(json.dumps({"source_type":"youtube_url","value":os.environ["URL"]}))'

START=$(date +%s)
curl -s --max-time 300 -X POST "http://127.0.0.1:$PORT/process" \
  -H "Content-Type: application/json" \
  --data-binary @/tmp/c2c_payload.json > /tmp/c2c_try.json
END=$(date +%s)

./venv/bin/python - "$((END-START))" <<'PYEOF'
import json, sys
elapsed = sys.argv[1]
try:
    d = json.load(open("/tmp/c2c_try.json"))
except Exception:
    print("  The server did not return readable JSON."); raise SystemExit(1)

if "detail" in d:
    print(f"  FAILED after {elapsed}s")
    print(f"  {d['detail']}")
    print("\n  Pick a different video, or use the Paste Transcript tab.")
    raise SystemExit(1)

s = d["summary"]
src = d.get("transcript_source", "?")
print(f"  WORKED in {elapsed}s")
print(f"  transcript from : {src}")
print(f"  ingredients     : {s['total_items']} extracted, {s['matched_items']} matched")
print(f"  cart total      : Rs.{s['estimated_total_inr']}")
print(f"  mcp calls       : {s['mcp_call_count']}")
missing = [b["product_name"] for b in d["basket"] if not b["matched_catalog_item"]]
print(f"  not stocked     : {', '.join(missing) if missing else 'none'}")
print()

verdict = []
if int(elapsed) > 45: verdict.append("SLOW. Over 45s is a long silence on camera.")
if s["total_items"] < 5: verdict.append("FEW INGREDIENTS. Find a recipe video with a proper ingredient list.")
if s["matched_items"] < s["total_items"] * 0.6: verdict.append("LOW MATCH RATE. Not your best demo.")
if src == "audio": verdict.append("GOOD: no captions, so the Whisper path shows on camera.")

if verdict:
    for v in verdict: print(f"  - {v}")
else:
    print("  Looks like a good demo video.")
PYEOF
echo ""
