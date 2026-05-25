import os
from supabase import create_client, Client


def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def upsert_blog(row: dict) -> None:
    client = get_client()
    client.table("blogs").upsert(row, on_conflict="slug").execute()


def upsert_place(row: dict) -> int:
    """Insert or update a place by name. Returns the place_id."""
    client = get_client()
    existing = (
        client.table("places_table")
        .select("place_id")
        .eq("place_name", row["place_name"])
        .execute()
    )
    if existing.data:
        place_id = existing.data[0]["place_id"]
        client.table("places_table").update(row).eq("place_id", place_id).execute()
        return place_id
    result = client.table("places_table").insert(row).execute()
    return result.data[0]["place_id"]


def upsert_item(row: dict) -> None:
    """Insert or update a dish by (item name, place_id)."""
    client = get_client()
    existing = (
        client.table("items_table")
        .select("item_id")
        .eq("item", row["item"])
        .eq("place_id", row["place_id"])
        .execute()
    )
    if existing.data:
        item_id = existing.data[0]["item_id"]
        (
            client.table("items_table")
            .update(row)
            .eq("item_id", item_id)
            .eq("place_id", row["place_id"])
            .execute()
        )
    else:
        client.table("items_table").insert(row).execute()


def upsert_embedding(row: dict) -> None:
    """Insert or update a row in the embeddings table.

    Matches on (source_type, source_id, source_id2 IS NULL / = value, chunk_idx)
    because the unique index uses coalesce(source_id2, -1) which PostgREST
    cannot reference directly.
    """
    client = get_client()
    query = (
        client.table("embeddings")
        .select("id")
        .eq("source_type", row["source_type"])
        .eq("source_id", row["source_id"])
        .eq("chunk_idx", row.get("chunk_idx", 0))
    )
    if row.get("source_id2") is not None:
        query = query.eq("source_id2", row["source_id2"])
    else:
        query = query.is_("source_id2", "null")

    existing = query.execute()
    if existing.data:
        client.table("embeddings").update(row).eq("id", existing.data[0]["id"]).execute()
    else:
        client.table("embeddings").insert(row).execute()
