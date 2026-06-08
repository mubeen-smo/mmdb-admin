import asyncio
import json as _json
import os
import re
import traceback

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware

from sync.supabase_client import get_client as get_supabase, upsert_item, upsert_place

load_dotenv()

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "mubeen.browse@gmail.com")
SECRET_KEY = os.environ["SECRET_KEY"]
CALLBACK_URL = os.environ.get("CALLBACK_URL", "http://localhost:8080/auth/callback")

app = FastAPI(title="MMDb Admin — Submissions")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)


@app.exception_handler(Exception)
async def _json_error_handler(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


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
        return HTMLResponse(
            f"<h2>Access denied.</h2><p>Signed in as: {user.get('email') if user else 'unknown'}</p>",
            status_code=403,
        )
    request.session["user"] = {"email": user["email"], "name": user.get("name", "")}
    return RedirectResponse("/submit")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/submit", response_class=HTMLResponse)
async def submit_page(request: Request, _=Depends(require_auth)):
    return templates.TemplateResponse(request, "submit.html", {"user": get_session(request)})


# ── Supabase search endpoints ─────────────────────────────────

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


# ── Submit ────────────────────────────────────────────────────

@app.post("/submit")
async def submit_place(request: Request, _=Depends(require_auth)):
    data = await request.json()
    print(f"[submit] payload: {_json.dumps(data, ensure_ascii=False, default=str)}")

    name = data.get("name", "").strip()
    if not name:
        return JSONResponse({"success": False, "error": "Name is required"}, status_code=400)

    lat = float(data["lat"]) if data.get("lat") else None
    lng = float(data["lng"]) if data.get("lng") else None
    price_tier_raw = data.get("price_tier")
    price_tier_int = int(price_tier_raw) if price_tier_raw else None

    # 1. Upsert place — must finish first so items get place_id
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
        return JSONResponse({"success": False, "error": f"Supabase error: {exc}"}, status_code=502)

    # 2. Upsert items concurrently
    items = [i for i in data.get("items", []) if i.get("name")]

    async def _upsert_item(item: dict) -> str | None:
        row = {
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
            await asyncio.to_thread(upsert_item, row)
            return None
        except Exception as exc:
            traceback.print_exc()
            return f"{item['name']}: {exc}"

    item_results = await asyncio.gather(*[_upsert_item(i) for i in items])
    item_errors = [e for e in item_results if e]

    return JSONResponse({
        "success":     True,
        "place_id":    place_id,
        "item_errors": item_errors,
    })
