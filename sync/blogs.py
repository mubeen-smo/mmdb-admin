import os
from datetime import date

from .notion_client import get_published_pages, update_last_synced
from .supabase_client import upsert_blog


def sync_blogs() -> dict:
    database_id = os.environ["NOTION_DATABASE_ID"]
    today = date.today().isoformat()

    pages = get_published_pages(database_id)

    synced = 0
    skipped = 0
    errors = []

    for page in pages:
        page_id = page.pop("_page_id")

        if not page.get("slug"):
            skipped += 1
            continue

        if not page.get("published_at"):
            page["published_at"] = today

        try:
            upsert_blog(page)
            update_last_synced(page_id, today)
            synced += 1
        except Exception as exc:
            errors.append({"slug": page.get("slug"), "error": str(exc)})

    return {"synced": synced, "skipped": skipped, "errors": errors}
