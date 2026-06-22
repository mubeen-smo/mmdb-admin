import os
import re
from datetime import date

from .extractor import extract_restaurant, extract_dishes
from .notion_client import get_restaurant_pages, update_last_synced
from .supabase_client import upsert_place, upsert_item


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return re.sub(r"-+", "-", slug).strip("-")


def sync_places() -> dict:
    database_id = os.environ["NOTION_PLACES_DB_ID"]
    today = date.today().isoformat()

    pages = get_restaurant_pages(database_id)

    places_synced = 0
    dishes_synced = 0
    errors = []

    for page in pages:
        page_id = page["_page_id"]
        name = page["name"].strip()
        raw_notes = page["raw_notes"].strip()

        if not name:
            errors.append({"page_id": page_id, "error": "missing name, skipped"})
            continue

        try:
            restaurant_data = extract_restaurant(raw_notes)
            dishes_data = extract_dishes(raw_notes, name)

            place_row = {
                "place_name": name,
                "description": restaurant_data.get("description") or None,
                "area": restaurant_data.get("area") or None,
                "type": restaurant_data.get("type") or None,
                "cuisines": restaurant_data.get("cuisines") or [],
                "meal_periods": restaurant_data.get("meal_periods") or [],
                "good_for": restaurant_data.get("good_for") or [],
                "vibe": restaurant_data.get("vibe") or [],
                "veg_friendly": restaurant_data.get("veg_friendly"),
                "price_tier": restaurant_data.get("price_tier"),
                "ambience_rating": restaurant_data.get("ambience_rating"),
                "service_rating": restaurant_data.get("service_rating"),
            }

            place_id = upsert_place(place_row)
            places_synced += 1

            for dish in dishes_data:
                item_name = (dish.get("item") or "").strip()
                if not item_name:
                    continue
                item_row = {
                    "item": item_name,
                    "place_id": place_id,
                    "place_name": name,
                    "description": dish.get("description") or None,
                    "diet": (dish.get("diet") or "").lower() or None,
                    "item_rating": dish.get("item_rating"),
                    "course": dish.get("course") or [],
                    "meal_time": dish.get("meal_time") or [],
                }
                upsert_item(item_row)
                dishes_synced += 1

            update_last_synced(page_id, today)

        except Exception as exc:
            errors.append({"name": name, "error": str(exc)})

    return {
        "places_synced": places_synced,
        "dishes_synced": dishes_synced,
        "errors": errors,
    }
