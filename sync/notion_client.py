from notion_client import Client
import os


def get_client() -> Client:
    return Client(auth=os.environ["NOTION_API_KEY"])


def _query_published_pages(database_id: str, mapper) -> list[dict]:
    client = get_client()
    results = []
    cursor = None

    while True:
        kwargs: dict = {
            "database_id": database_id,
            "filter": {
                "property": "Status",
                "select": {"equals": "Published"},
            },
        }
        if cursor:
            kwargs["start_cursor"] = cursor

        response = client.databases.query(**kwargs)
        results.extend(response["results"])

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    return [mapper(page) for page in results]


def get_published_pages(database_id: str) -> list[dict]:
    return _query_published_pages(database_id, _map_page)


def get_restaurant_pages(database_id: str) -> list[dict]:
    return _query_published_pages(database_id, _map_restaurant_page)


def update_last_synced(page_id: str, date_str: str) -> None:
    client = get_client()
    client.pages.update(
        page_id=page_id,
        properties={
            "Last Synced": {
                "date": {"start": date_str},
            }
        },
    )


def _map_page(page: dict) -> dict:
    props = page["properties"]

    def text(prop_name: str) -> str:
        prop = props.get(prop_name, {})
        rich = prop.get("rich_text") or prop.get("title") or []
        return "".join(t["plain_text"] for t in rich)

    def select(prop_name: str) -> str:
        prop = props.get(prop_name, {})
        val = prop.get("select")
        return val["name"] if val else ""

    def multi_select(prop_name: str) -> list[str]:
        prop = props.get(prop_name, {})
        return [item["name"] for item in prop.get("multi_select", [])]

    def date(prop_name: str) -> str | None:
        prop = props.get(prop_name, {})
        val = prop.get("date")
        return val["start"] if val else None

    def url(prop_name: str) -> str:
        prop = props.get(prop_name, {})
        return prop.get("url") or ""

    return {
        "_page_id": page["id"],
        "title": text("Title"),
        "slug": text("Slug"),
        "subtitle": text("Subtitle"),
        "body_md": text("Body"),
        "hero_image": url("Hero Image URL"),
        "author": text("Author") or "maven",
        "theme": select("Theme"),
        "tags": multi_select("Tags"),
        "published_at": date("Published At"),
    }


def _map_restaurant_page(page: dict) -> dict:
    props = page["properties"]

    def text(prop_name: str) -> str:
        prop = props.get(prop_name, {})
        rich = prop.get("rich_text") or prop.get("title") or []
        return "".join(t["plain_text"] for t in rich)

    return {
        "_page_id": page["id"],
        "name": text("Title"),
        "slug": text("Slug"),
        "raw_notes": text("Raw Notes"),
    }
