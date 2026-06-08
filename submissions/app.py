import asyncio
import os
import re
import traceback
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
from notion_client import Client as NotionClient
from dotenv import load_dotenv
from sync.supabase_client import get_client as get_supabase, upsert_item, upsert_place

load_dotenv()

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "mubeen.browse@gmail.com")
SECRET_KEY = os.environ["SECRET_KEY"]
NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_PLACES_DB_ID = os.environ.get("NOTION_PLACES_DB_ID", "81ee0961-4bfd-4abe-bb58-c4bbed6eb787")
CALLBACK_URL = os.environ.get("CALLBACK_URL", "http://localhost:8080/auth/callback")

notion = NotionClient(auth=NOTION_API_KEY)

app = FastAPI(title="MMDb Admin — Submissions")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


@app.exception_handler(Exception)
async def _json_error_handler(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse(
        {"success": False, "error": str(exc)},
        status_code=500,
    )

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ── Auth helpers ──────────────────────────────────────────────

def get_session(request: Request) -> dict | None:
    return request.session.get("user")


def require_auth(request: Request):
    if not get_session(request):
        raise HTTPException(status_code=302, headers={"Location": "/login"})


# ── Routes ────────────────────────────────────────────────────

@app.get("/", response_class=RedirectResponse)
async def root(request: Request):
    return RedirectResponse("/submit" if get_session(request) else "/login")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.get("/auth/google")
async def auth_google(request: Request):
    return await oauth.google.authorize_redirect(request, CALLBACK_URL)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user = token.get("userinfo")
    print(f"[auth] google email: {user.get('email') if user else 'none'}, expected: {ADMIN_EMAIL}")
    if not user or user.get("email") != ADMIN_EMAIL:
        return HTMLResponse(f"<h2>Access denied.</h2><p>Signed in as: {user.get('email') if user else 'unknown'}</p>", status_code=403)
    request.session["user"] = {"email": user["email"], "name": user.get("name", "")}
    return RedirectResponse("/submit")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/submit", response_class=HTMLResponse)
async def submit_page(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse(request, "submit.html", {"user": get_session(request)})


@app.get("/api/places")
async def api_places(q: str = "", _=Depends(require_auth)):
    sb = await asyncio.to_thread(get_supabase)
    query = (
        sb.table("places_table")
        .select("place_id,place_name,area,type,cuisines,veg_friendly,price_tier,ambience_rating,service_rating,latitude,longitude")
        .order("place_name")
        .limit(20)
    )
    if q:
        query = query.ilike("place_name", f"%{q}%")
    result = await asyncio.to_thread(query.execute)
    return JSONResponse(result.data)


@app.get("/api/items")
async def api_items(q: str = "", place_id: int | None = None, _=Depends(require_auth)):
    sb = await asyncio.to_thread(get_supabase)
    query = (
        sb.table("items_table")
        .select("item_id,item,place_id,diet,course,meal_time,item_rating,description,signature")
        .order("item")
        .limit(20)
    )
    if q:
        query = query.ilike("item", f"%{q}%")
    if place_id is not None:
        query = query.eq("place_id", place_id)
    result = await asyncio.to_thread(query.execute)
    return JSONResponse(result.data)


@app.post("/submit")
async def submit_place(request: Request, _=Depends(require_auth)):
    data = await request.json()
    import json as _json
    print(f"[submit] payload: {_json.dumps(data, ensure_ascii=False, default=str)}")

    name = data.get("name", "").strip()
    if not name:
        return JSONResponse({"success": False, "error": "Name is required"}, status_code=400)

    slug = data.get("slug", "").strip() or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    lat = float(data["lat"]) if data.get("lat") else None
    lng = float(data["lng"]) if data.get("lng") else None
    price_tier_raw = data.get("price_tier")
    price_tier_int = int(price_tier_raw) if price_tier_raw else None

    # ── 1. Supabase place (must complete first — items need place_id) ─
    place_row = {
        "place_name":      name,
        "area":            data.get("area") or None,
        "type":            data.get("type") or None,
        "cuisines":        data.get("cuisines") or None,
        "veg_friendly":    bool(data.get("veg_friendly", False)),
        "price_tier":      price_tier_int,
        "description":     data.get("description") or None,
        "ambience_rating": data.get("ambience_rating"),
        "service_rating":  data.get("service_rating"),
        "latitude":        lat,
        "longitude":       lng,
    }

    try:
        place_id = await asyncio.to_thread(upsert_place, place_row)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(
            {"success": False, "error": f"Supabase error: {exc}"},
            status_code=502,
        )

    items = data.get("items", [])

    # ── 2. Notion (best-effort) ────────────────────────────────
    price_map = {"1": "1 - Budget", "2": "2 - Moderate", "3": "3 - Pricey", "4": "4 - Premium"}
    price_label = price_map.get(str(price_tier_raw or ""), None)

    properties = {
        "Name":   {"title": [{"text": {"content": name}}]},
        "Slug":   {"rich_text": [{"text": {"content": slug}}]},
        "Status": {"select": {"name": "draft"}},
    }
    if data.get("area"):
        properties["Area"] = {"rich_text": [{"text": {"content": data["area"]}}]}
    if data.get("type"):
        properties["Type"] = {"select": {"name": data["type"]}}
    if data.get("cuisines"):
        properties["Cuisines"] = {"multi_select": [{"name": c} for c in data["cuisines"]]}
    if "veg_friendly" in data:
        properties["Veg Friendly"] = {"checkbox": bool(data["veg_friendly"])}
    if price_label:
        properties["Price Tier"] = {"select": {"name": price_label}}
    if data.get("ambience_rating") is not None:
        properties["Ambience Rating"] = {"rich_text": [{"text": {"content": str(data["ambience_rating"])}}]}
    if data.get("service_rating") is not None:
        properties["Service Rating"] = {"rich_text": [{"text": {"content": str(data["service_rating"])}}]}

    blocks = [{"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Place Info"}}]}}]
    if lat and lng:
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": f"Location: {lat}° N, {lng}° E", "link": {"url": f"https://maps.google.com/?q={lat},{lng}"}}}
        ]}})
    if data.get("description"):
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": data["description"]}}]}})
    if data.get("ambience_rating") is not None:
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": f"Ambience: {data['ambience_rating']} / 10"}}]}})
    if data.get("service_rating") is not None:
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": f"Service: {data['service_rating']} / 10"}}]}})

    if items:
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Items"}}]}})
        for item in items:
            if not item.get("name"):
                continue
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": item["name"]}}]}})
            for part in [
                f"Diet: {item['diet']}" if item.get("diet") else None,
                f"Rating: {item.get('rating')} / 10" if item.get("rating") is not None else None,
                f"Course: {', '.join(item['course'])}" if item.get("course") else None,
                f"Meal Time: {', '.join(item['meal_time'])}" if item.get("meal_time") else None,
                item.get("description") or None,
            ]:
                if part:
                    blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": part}}]}})

    # ── 2. Supabase items + Notion page — run in parallel ─────────
    async def _write_items() -> list[str]:
        errors: list[str] = []
        for item in items:
            if not item.get("name"):
                continue
            item_row = {
                "item":        item["name"].strip(),
                "place_id":    place_id,
                "place_name":  name,
                "diet":        item.get("diet") or None,
                "course":      item.get("course") or None,
                "meal_time":   item.get("meal_time") or None,
                "item_rating": item.get("rating"),
                "description": item.get("description") or None,
                "signature":   False,
            }
            try:
                await asyncio.to_thread(upsert_item, item_row)
            except Exception as exc:
                traceback.print_exc()
                errors.append(f"{item['name']}: {exc}")
        return errors

    async def _write_notion() -> tuple[str, str]:
        try:
            page = await asyncio.to_thread(
                notion.pages.create,
                parent={"database_id": NOTION_PLACES_DB_ID},
                properties=properties,
                children=blocks[:100],
            )
            if len(blocks) > 100:
                await asyncio.to_thread(
                    notion.blocks.children.append,
                    page["id"],
                    children=blocks[100:200],
                )
            return page.get("url", ""), ""
        except Exception as exc:
            traceback.print_exc()
            return "", str(exc)

    (item_errors, (notion_url, notion_err)) = await asyncio.gather(
        _write_items(),
        _write_notion(),
    )

    notion_warning = f"Saved to Supabase, but Notion sync failed: {notion_err}" if notion_err else ""

    return JSONResponse({
        "success":     True,
        "notion_url":  notion_url,
        "warning":     notion_warning,
        "item_errors": item_errors,
    })
