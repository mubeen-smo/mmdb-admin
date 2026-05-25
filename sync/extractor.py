import json
import os

from groq import Groq

_RESTAURANT_PROMPT = """\
Extract structured information about a restaurant from the notes below.
Return ONLY a valid JSON object with these exact fields:
{
  "name": "string",
  "description": "string",
  "area": "string — neighbourhood or area in Hyderabad",
  "type": "string — one of: restaurant, dhaba, cafe, food court, bakery, bar",
  "cuisines": ["list of cuisine strings"],
  "meal_periods": ["list — e.g. breakfast, lunch, dinner, brunch, all-day"],
  "good_for": ["list — e.g. families, couples, solo, groups, dates"],
  "vibe": ["list — e.g. casual, outdoor, heritage, rooftop, cosy, lively"],
  "veg_friendly": true or false,
  "price_tier": integer 1–4,
  "ambience_rating": float 0–10 or null,
  "service_rating": float 0–10 or null
}
For unknown text fields use an empty string. For unknown arrays use []. For unknown ratings use null."""

_DISHES_PROMPT = """\
Extract all dishes mentioned for restaurant "{place_name}" from the notes below.
Return ONLY a valid JSON object with one key "dishes" whose value is an array.
Each element must have:
{{
  "item": "string — dish name",
  "description": "string",
  "diet": "one of: veg, non_veg, vegan, egg",
  "item_rating": float 0–10 or null,
  "course": ["list — e.g. main, starter, dessert, snack, drink, side"],
  "meal_time": ["list — e.g. breakfast, lunch, dinner, snack"]
}}
For unknown text fields use an empty string. For unknown arrays use []. For unknown ratings use null."""


def _client() -> Groq:
    return Groq(api_key=os.environ["GROQ_API_KEY"])


def extract_restaurant(raw_text: str) -> dict:
    response = _client().chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": _RESTAURANT_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    return json.loads(response.choices[0].message.content or "{}")


def extract_dishes(raw_text: str, place_name: str) -> list[dict]:
    prompt = _DISHES_PROMPT.format(place_name=place_name)
    response = _client().chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": raw_text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    content = response.choices[0].message.content or "{}"
    parsed = json.loads(content)
    if isinstance(parsed, list):
        return parsed
    for key in ("dishes", "items", "menu"):
        if key in parsed and isinstance(parsed[key], list):
            return parsed[key]
    return []
