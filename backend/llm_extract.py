import json
import os

from groq import Groq

# Groq retires models fairly often. Check what your key can actually reach with
# `curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"`.
# gpt-oss-20b is roughly twice as fast if you prefer a snappier demo.
# `or` rather than a get() default, so an empty GROQ_MODEL= in .env
# still falls back instead of resolving to an empty model name.
MODEL = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"

# A 40 minute video's transcript will blow Groq's free tier token budget and
# fail the whole request. Recipes state their ingredients early, so truncating
# costs very little and makes long videos work instead of failing.
MAX_TRANSCRIPT_CHARS = 12_000

REQUEST_TIMEOUT_SECONDS = 30.0

SYSTEM_PROMPT = """You are a shopping assistant. You will receive a transcript from a video \
(recipe, haul, review, etc.). Your job is to extract product mentions and guess quantities.

The transcript may be in ANY language, including Hindi in Devanagari script, or a
mix of Hindi and English. Handle it.

Respond ONLY with valid JSON, no markdown fences, no commentary. An object with
how many people the recipe serves, and the list of products:
{
 "serves": 4,
 "items": [
  {
    "product_name": "प्याज़",
    "canonical_name": "onion",
    "category": "grocery",
    "estimated_quantity": "2",
    "quantity_source": "stated",
    "confidence": "High"
  }
 ]
}

Rules:
- serves is how many people the recipe as described feeds. Use the number if the
  video or description says so. Otherwise judge it from the amounts, and if you
  genuinely cannot tell, use 4.
- product_name is what the speaker actually said, in their own words and script
- canonical_name is the SAME product as a plain lowercase English grocery term,
  the way it would be labelled on a shop shelf. "प्याज़" -> "onion",
  "kasuri methi" -> "kasuri methi", "shimla mirch" -> "capsicum",
  "hari mirch" -> "green chilli", "curd" -> "curd". Always fill this in.
- category must be one of: grocery, personal_care, household
- confidence must be one of: Low, Medium, High
- estimated_quantity must ALWAYS contain a usable amount. Never "unknown",
  never "some", never "to taste", never empty.
- quantity_source must be "stated" or "estimated".
  Use "stated" when the amount appears anywhere in the text, including the
  video description or a recap near the end. Keep the original wording, such as
  "2 cups", "आधा चम्मच", "three to four", "250 g".
  Use "estimated" when no amount is given anywhere. Then supply a sensible
  amount for this dish for 3 to 4 people, written in ordinary units, for
  example "1 tsp", "200 g", "2 tbsp". Base it on what the dish actually needs.
  A pinch of asafoetida, not 100 grams of it.
- Read the whole text before deciding. Amounts are often listed together in a
  description block or repeated in a summary at the end, even when the speaker
  never says them while cooking.
- Only include real products, not generic terms like "things" or "stuff"
- Do not include equipment such as a pan, kadai or spoon. Only include things
  you can buy as groceries.
- If nothing is found, return an empty list []
"""


class ExtractionError(Exception):
    pass


def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ExtractionError("GROQ_API_KEY is not set. Add it to your .env file.")
    return Groq(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=1)


def _friendly_groq_error(exc: Exception) -> str:
    """Turn an SDK exception into something the viewer can act on."""
    name = type(exc).__name__
    if "RateLimit" in name:
        return "Groq rate limit reached. Wait a few seconds and try again."
    if "Authentication" in name or "PermissionDenied" in name:
        return "Groq rejected the API key. Check GROQ_API_KEY in your .env file."
    if "APIConnection" in name or "Timeout" in name:
        return "Could not reach Groq. Check the network, then try again."
    if "NotFound" in name:
        return (
            f"Groq has no model called '{MODEL}'. It was probably retired. "
            f"List what your key can reach with: curl "
            f"https://api.groq.com/openai/v1/models -H \"Authorization: Bearer $GROQ_API_KEY\" "
            f"then set GROQ_MODEL in .env, or edit MODEL in backend/llm_extract.py."
        )
    if "BadRequest" in name:
        return "Groq rejected the request. The transcript may be too long."
    return f"Groq call failed ({name}): {exc}"


def _call_groq(client: Groq, transcript_text: str, retry_hint: bool = False) -> str:
    user_content = transcript_text
    if retry_hint:
        user_content += "\n\nReturn valid JSON only. No markdown, no explanation."

    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001, surfaced as a clean 502 upstream
        raise ExtractionError(_friendly_groq_error(exc)) from exc

    return completion.choices[0].message.content


def _parse_json_list(raw: str) -> tuple[list[dict], int]:
    """Returns (items, serves).

    Accepts either the object form the prompt asks for, or a bare list, so an
    older style response still works.
    """
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw

    obj_start, obj_end = raw.find("{"), raw.rfind("}")
    list_start, list_end = raw.find("["), raw.rfind("]")

    if obj_start != -1 and obj_end != -1 and obj_start < list_start:
        parsed = json.loads(raw[obj_start : obj_end + 1])
        if isinstance(parsed, dict) and isinstance(parsed.get("items"), list):
            serves = parsed.get("serves")
            serves = int(serves) if isinstance(serves, (int, float)) and serves else DEFAULT_SERVES
            items = [i for i in parsed["items"] if isinstance(i, dict)]
            return items, max(1, min(serves, 20))

    if list_start == -1 or list_end == -1:
        raise ValueError("No JSON found in response")
    parsed = json.loads(raw[list_start : list_end + 1])
    if not isinstance(parsed, list):
        raise ValueError("Expected a JSON list")
    return [i for i in parsed if isinstance(i, dict)], DEFAULT_SERVES


DEFAULT_SERVES = 4


def extract_ingredients(transcript_text: str) -> tuple[list[dict], int]:
    client = _client()
    transcript_text = transcript_text[:MAX_TRANSCRIPT_CHARS]

    raw = _call_groq(client, transcript_text)
    try:
        return _parse_json_list(raw)
    except (ValueError, json.JSONDecodeError):
        pass

    raw = _call_groq(client, transcript_text, retry_hint=True)
    try:
        return _parse_json_list(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"The model did not return valid JSON: {exc}")
