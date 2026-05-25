import os
import re

from .notion_client import get_client as get_notion_client
from .supabase_client import get_client as get_supabase_client


# ── helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return re.sub(r"-+", "-", slug).strip("-")


_CUISINE_MAP: dict[str, str] = {
    "andhra": "Andhra",
    "hyderabadi": "Hyderabadi",
    "north_indian": "North Indian",
    "chinese": "Chinese",
    "south_indian": "South Indian",
    "mughlai": "Mughlai",
    "fast_food": "Fast Food",
}

_TYPE_MAP: dict[str, str] = {
    "restaurant": "restaurant",
    "dhaba": "dhaba",
    "cafe": "cafe",
    "food_court": "food court",
    "bakery": "bakery",
    "street_food": "street food",
}

_PRICE_TIER_MAP: dict[int, str] = {
    1: "1 - Budget",
    2: "2 - Moderate",
    3: "3 - Pricey",
    4: "4 - Premium",
}


# ── existing-slug fetch ───────────────────────────────────────────────────────

def _fetch_existing_slugs(database_id: str) -> set[str]:
    """Return slugs already present in the Notion Places database (all statuses)."""
    notion = get_notion_client()
    slugs: set[str] = set()
    cursor = None

    while True:
        kwargs: dict = {"database_id": database_id}
        if cursor:
            kwargs["start_cursor"] = cursor

        response = notion.databases.query(**kwargs)

        for page in response["results"]:
            props = page["properties"]
            rich = (props.get("Slug") or {}).get("rich_text") or []
            slug = "".join(t["plain_text"] for t in rich)
            if slug:
                slugs.add(slug)

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    return slugs


# ── block builders ────────────────────────────────────────────────────────────

def _heading2(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _heading3(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text or ""}}]},
    }


def _bold_field(label: str, value: str) -> dict:
    """Single paragraph: **Label:** value"""
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {"content": f"{label}: "},
                    "annotations": {"bold": True},
                },
                {"type": "text", "text": {"content": value}},
            ]
        },
    }


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


# ── page body builder ─────────────────────────────────────────────────────────

def _build_blocks(place: dict, items: list[dict]) -> list[dict]:
    blocks: list[dict] = []

    # Place Info section
    blocks.append(_heading2("Place Info"))

    lat = place.get("latitude")
    lng = place.get("longitude")
    location_str = f"{lat}° N, {lng}° E" if (lat is not None and lng is not None) else "Not recorded"
    blocks.append(_bold_field("Location", location_str))

    blocks.append(_bold_field("Description", ""))
    desc = (place.get("description") or "").strip()
    blocks.append(_paragraph(desc if desc else "—"))

    ambience = place.get("ambience_rating")
    blocks.append(_bold_field("Ambience Rating", f"{ambience} / 10" if ambience is not None else "—"))

    service = place.get("service_rating")
    blocks.append(_bold_field("Service Rating", f"{service} / 10" if service is not None else "—"))

    blocks.append(_divider())

    # Items section
    blocks.append(_heading2("Items"))

    if not items:
        blocks.append(_paragraph("No items recorded."))
    else:
        for item in items:
            blocks.append(_heading3((item.get("item") or "Unnamed").strip()))

            blocks.append(_bold_field("Diet", item.get("diet") or "—"))

            rating = item.get("item_rating")
            blocks.append(_bold_field("Rating", f"{rating} / 10" if rating is not None else "—"))

            course = item.get("course") or []
            blocks.append(_bold_field("Course", ", ".join(course) if course else "—"))

            meal_time = item.get("meal_time") or []
            blocks.append(_bold_field("Meal Time", ", ".join(meal_time) if meal_time else "—"))

            item_desc = (item.get("description") or "").strip()
            if item_desc:
                blocks.append(_paragraph(item_desc))

    return blocks


# ── property builder ──────────────────────────────────────────────────────────

def _build_properties(place: dict, slug: str) -> dict:
    props: dict = {}

    # Name (title)
    props["Name"] = {
        "title": [{"type": "text", "text": {"content": place.get("place_name") or ""}}]
    }

    # Slug
    props["Slug"] = {
        "rich_text": [{"type": "text", "text": {"content": slug}}]
    }

    # Area
    area = (place.get("area") or "").strip()
    if area:
        props["Area"] = {"select": {"name": area}}

    # Type
    raw_type = (place.get("type") or "").lower().replace(" ", "_")
    mapped_type = _TYPE_MAP.get(raw_type)
    if mapped_type:
        props["Type"] = {"select": {"name": mapped_type}}

    # Cuisines — only valid Notion options pass through
    raw_cuisines = place.get("cuisines") or []
    valid_cuisines = [_CUISINE_MAP[c] for c in raw_cuisines if c in _CUISINE_MAP]
    if valid_cuisines:
        props["Cuisines"] = {"multi_select": [{"name": c} for c in valid_cuisines]}

    # Veg Friendly
    vf = place.get("veg_friendly")
    if vf is not None:
        props["Veg Friendly"] = {"select": {"name": "__YES__" if vf else "__NO__"}}

    # Price Tier
    pt = place.get("price_tier")
    if pt and int(pt) in _PRICE_TIER_MAP:
        props["Price Tier"] = {"select": {"name": _PRICE_TIER_MAP[int(pt)]}}

    # Ambience Rating
    ambience = place.get("ambience_rating")
    if ambience is not None:
        props["Ambience Rating"] = {
            "rich_text": [{"type": "text", "text": {"content": str(ambience)}}]
        }

    # Service Rating
    service = place.get("service_rating")
    if service is not None:
        props["Service Rating"] = {
            "rich_text": [{"type": "text", "text": {"content": str(service)}}]
        }

    # Status — active=True → published, anything else → draft
    active = place.get("active")
    props["Status"] = {"select": {"name": "published" if active else "draft"}}

    return props


# ── main ──────────────────────────────────────────────────────────────────────

def backfill_places_to_notion() -> dict:
    database_id = os.environ["NOTION_PLACES_DB_ID"]
    notion = get_notion_client()
    supabase = get_supabase_client()

    # Fetch all places
    places_resp = supabase.table("places_table").select("*").execute()
    places: list[dict] = places_resp.data or []

    # Fetch all items, group by place_id
    items_resp = supabase.table("items_table").select("*").execute()
    items_by_place: dict[int, list[dict]] = {}
    for item in (items_resp.data or []):
        pid = item.get("place_id")
        if pid is not None:
            items_by_place.setdefault(int(pid), []).append(item)

    # Slugs already in Notion — skip these
    existing_slugs = _fetch_existing_slugs(database_id)

    created = 0
    skipped = 0
    errors: list[dict] = []

    for place in places:
        name = (place.get("place_name") or "").strip()
        if not name:
            errors.append({"place_id": place.get("place_id"), "error": "missing name"})
            continue

        slug = place.get("slug") or _slugify(name)

        if slug in existing_slugs:
            print(f"Skipped: {name} (already exists)")
            skipped += 1
            continue

        try:
            properties = _build_properties(place, slug)
            place_items = items_by_place.get(int(place.get("place_id", 0)), [])
            blocks = _build_blocks(place, place_items)

            # pages.create accepts up to 100 children
            response = notion.pages.create(
                parent={"database_id": database_id},
                properties=properties,
                children=blocks[:100],
            )

            # Append any overflow blocks (>100) in batches of 100
            if len(blocks) > 100:
                page_id = response["id"]
                remaining = blocks[100:]
                while remaining:
                    notion.blocks.children.append(
                        block_id=page_id,
                        children=remaining[:100],
                    )
                    remaining = remaining[100:]

            print(f"Created: {name}")
            created += 1

        except Exception as exc:
            print(f"Error: {name} — {exc}")
            errors.append({"name": name, "error": str(exc)})

    return {"created": created, "skipped": skipped, "errors": errors}
