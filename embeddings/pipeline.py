"""
Embedding pipeline for mmdb-admin.

Fetches unembedded (or changed) rows from Supabase, generates embeddings via
OpenAI text-embedding-3-small, and upserts them into the embeddings table.

Document-building logic mirrors app/tasks/embed.py in the backend, adapted
to work with plain dicts from the Supabase REST client instead of ORM objects.
"""

import hashlib
import logging
import os

from openai import OpenAI

from sync.supabase_client import get_client, upsert_embedding

logger = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"
BATCH = 32


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _openai() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _embed_batch(texts: list[str]) -> list[list[float]]:
    resp = _openai().embeddings.create(model=EMBED_MODEL, input=texts)
    return [item.embedding for item in resp.data]


def _price_label(tier) -> str:
    try:
        return {1: "budget", 2: "mid-range", 3: "premium"}.get(int(tier), "")
    except (TypeError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Document builders
# ---------------------------------------------------------------------------

def _place_doc(p: dict, sig_items: str | None, other_items: str | None) -> str:
    parts = [
        f"{p['place_name']}.",
        f"{p.get('type') or ''} | {p.get('area') or ''} | {_price_label(p.get('price_tier'))}.",
        ", ".join(p.get("cuisines") or []) + "." if p.get("cuisines") else "",
    ]
    if p.get("vibe"):
        parts.append(f"Vibe: {', '.join(p['vibe'])}.")
    if p.get("good_for"):
        parts.append(f"Good for: {', '.join(p['good_for'])}.")
    if p.get("meal_periods"):
        parts.append(f"Open for: {', '.join(p['meal_periods'])}.")
    if p.get("veg_friendly"):
        parts.append("Veg friendly.")
    if sig_items:
        parts.append(f"Must try: {sig_items}.")
    if other_items:
        parts.append(f"Also on the menu: {other_items}.")
    if p.get("description"):
        parts.append(p["description"][:200])
    return " ".join(x for x in parts if x)


def _item_doc(i: dict) -> str:
    parts = [
        f"{i['item']} at {i.get('place_name', '')}.",
        f"{i.get('diet') or ''} | {', '.join(i.get('course') or [])} | {', '.join(i.get('meal_time') or [])}.",
    ]
    if i.get("item_rating"):
        parts.append(f"Rating: {i['item_rating']}/10.")
    if i.get("signature"):
        parts.append("Signature dish.")
    if i.get("description"):
        parts.append(i["description"][:200])
    return " ".join(x for x in parts if x)


def _blog_doc(b: dict) -> str:
    parts = [b.get("title") or "", b.get("subtitle") or ""]
    body = b.get("body_md") or ""
    parts.append(body[:4000])
    return " ".join(x for x in parts if x)


# ---------------------------------------------------------------------------
# Per-source embedding runs
# ---------------------------------------------------------------------------

def _embed_places(client) -> int:
    places = client.table("places_table").select("*").execute().data
    items = (
        client.table("items_table")
        .select("item, place_id, item_rating, signature")
        .execute()
        .data
    )

    sig_map: dict[int, list[str]] = {}
    other_map: dict[int, list[str]] = {}
    for i in items:
        pid = i["place_id"]
        label = f"{i['item']} {i['item_rating']}" if i.get("item_rating") else i["item"]
        if i.get("signature"):
            sig_map.setdefault(pid, []).append(label)
        else:
            other_map.setdefault(pid, []).append(i["item"])

    existing = (
        client.table("embeddings")
        .select("source_id, meta")
        .eq("source_type", "place")
        .execute()
        .data
    )
    hash_map = {r["source_id"]: (r.get("meta") or {}).get("content_hash", "") for r in existing}

    delta = []
    for p in places:
        sig = ", ".join(sig_map.get(p["place_id"], []))
        other = ", ".join(other_map.get(p["place_id"], []))
        doc = _place_doc(p, sig or None, other or None)
        doc_hash = _hash(doc)
        if hash_map.get(p["place_id"]) != doc_hash:
            delta.append((p, doc, doc_hash))

    if not delta:
        logger.info("embed_places: all up to date")
        return 0

    count = 0
    for i in range(0, len(delta), BATCH):
        batch = delta[i : i + BATCH]
        vecs = _embed_batch([doc for _, doc, _ in batch])
        for (p, doc, doc_hash), vec in zip(batch, vecs):
            upsert_embedding({
                "source_type": "place",
                "source_id": p["place_id"],
                "source_id2": None,
                "chunk_idx": 0,
                "text": doc,
                "embedding": vec,
                "meta": {"place_name": p["place_name"], "content_hash": doc_hash},
            })
            count += 1
        logger.info("  embedded %d places", len(batch))

    return count


def _embed_items(client) -> int:
    items = client.table("items_table").select("*").execute().data

    existing = (
        client.table("embeddings")
        .select("source_id, source_id2")
        .eq("source_type", "item")
        .execute()
        .data
    )
    existing_keys = {(r["source_id"], r["source_id2"]) for r in existing}

    delta = [i for i in items if (i["item_id"], i["place_id"]) not in existing_keys]

    if not delta:
        logger.info("embed_items: all up to date")
        return 0

    count = 0
    for i in range(0, len(delta), BATCH):
        batch = delta[i : i + BATCH]
        vecs = _embed_batch([_item_doc(i) for i in batch])
        for item, vec in zip(batch, vecs):
            doc = _item_doc(item)
            upsert_embedding({
                "source_type": "item",
                "source_id": item["item_id"],
                "source_id2": item["place_id"],
                "chunk_idx": 0,
                "text": doc,
                "embedding": vec,
                "meta": {
                    "item": item["item"],
                    "item_rating": float(item["item_rating"]) if item.get("item_rating") else None,
                },
            })
            count += 1
        logger.info("  embedded %d items", len(batch))

    return count


def _embed_blogs(client) -> int:
    blogs = client.table("blogs").select("*").execute().data

    existing = (
        client.table("embeddings")
        .select("source_id, meta")
        .eq("source_type", "blog_chunk")
        .eq("chunk_idx", 0)
        .execute()
        .data
    )
    hash_map = {r["source_id"]: (r.get("meta") or {}).get("content_hash", "") for r in existing}

    delta = []
    for b in blogs:
        doc = _blog_doc(b)
        doc_hash = _hash(doc)
        if hash_map.get(b["blog_id"]) != doc_hash:
            delta.append((b, doc, doc_hash))

    if not delta:
        logger.info("embed_blogs: all up to date")
        return 0

    count = 0
    for i in range(0, len(delta), BATCH):
        batch = delta[i : i + BATCH]
        vecs = _embed_batch([doc for _, doc, _ in batch])
        for (b, doc, doc_hash), vec in zip(batch, vecs):
            upsert_embedding({
                "source_type": "blog_chunk",
                "source_id": b["blog_id"],
                "source_id2": None,
                "chunk_idx": 0,
                "text": doc,
                "embedding": vec,
                "meta": {"slug": b.get("slug"), "content_hash": doc_hash},
            })
            count += 1
        logger.info("  embedded %d blogs", len(batch))

    return count


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_embeddings(source_type: str = "all") -> dict:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    client = get_client()
    results: dict[str, int] = {}

    if source_type in ("all", "place"):
        results["places"] = _embed_places(client)
    if source_type in ("all", "item"):
        results["items"] = _embed_items(client)
    if source_type in ("all", "blog"):
        results["blogs"] = _embed_blogs(client)

    return results
