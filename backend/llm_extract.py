import json
import os

from groq import Groq

MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are a shopping assistant. You will receive a transcript from a video \
(recipe, haul, review, etc.). Your job is to extract product mentions and guess quantities.

Respond ONLY with valid JSON: a list of objects, no markdown fences, no commentary.
[
  {
    "product_name": "Atta",
    "category": "grocery",
    "estimated_quantity": "1 unit",
    "confidence": "High"
  }
]

Rules:
- category must be one of: grocery, personal_care, household
- confidence must be one of: Low, Medium, High
- If quantity is unclear, set estimated_quantity to "unknown"
- Only include real products, not generic terms like "things" or "stuff"
- If nothing is found, return an empty list []
"""


class ExtractionError(Exception):
    pass


def _client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ExtractionError("GROQ_API_KEY is not set. Add it to your .env file.")
    return Groq(api_key=api_key)


def _call_groq(client: Groq, transcript_text: str, retry_hint: bool = False) -> str:
    user_content = transcript_text
    if retry_hint:
        user_content += "\n\nReturn valid JSON only. No markdown, no explanation."

    completion = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
    )
    return completion.choices[0].message.content


def _parse_json_list(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("No JSON list found in response")
    return json.loads(raw[start : end + 1])


def extract_ingredients(transcript_text: str) -> list[dict]:
    client = _client()

    raw = _call_groq(client, transcript_text)
    try:
        return _parse_json_list(raw)
    except (ValueError, json.JSONDecodeError):
        pass

    raw = _call_groq(client, transcript_text, retry_hint=True)
    try:
        return _parse_json_list(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"LLM did not return valid JSON: {exc}")
