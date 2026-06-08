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


@app.post("/submit")
async def submit_place(request: Request, _=Depends(require_auth)):
    data = await request.json()

    name = data.get("name", "").strip()
    slug = data.get("slug", "").strip() or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

    price_map = {"1": "1 - Budget", "2": "2 - Moderate", "3": "3 - Pricey", "4": "4 - Premium"}
    price_tier = price_map.get(str(data.get("price_tier", "")), None)

    properties = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Slug": {"rich_text": [{"text": {"content": slug}}]},
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
    if price_tier:
        properties["Price Tier"] = {"select": {"name": price_tier}}
    if data.get("ambience_rating") is not None:
        properties["Ambience Rating"] = {"rich_text": [{"text": {"content": str(data["ambience_rating"])}}]}
    if data.get("service_rating") is not None:
        properties["Service Rating"] = {"rich_text": [{"text": {"content": str(data["service_rating"])}}]}

    blocks = []
    blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Place Info"}}]}})

    lat, lng = data.get("lat"), data.get("lng")
    if lat and lng:
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": f"Location: {lat}° N, {lng}° E", "link": {"url": f"https://maps.google.com/?q={lat},{lng}"}}}
        ]}})

    if data.get("description"):
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Description:"}}]}})
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": data["description"]}}]}})

    if data.get("ambience_rating") is not None:
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": f"Ambience Rating: {data['ambience_rating']} / 10"}}]}})
    if data.get("service_rating") is not None:
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": f"Service Rating: {data['service_rating']} / 10"}}]}})
    if data.get("notes"):
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": data["notes"]}}]}})

    items = data.get("items", [])
    if items:
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Items"}}]}})
        for item in items:
            if not item.get("name"):
                continue
            blocks.append({"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": item["name"]}}]}})
            for part in [
                f"Diet: {item['diet'].replace('_', '-').title()}" if item.get("diet") else None,
                f"Rating: {item['rating']} / 10" if item.get("rating") is not None else None,
                f"Course: {', '.join(item['course'])}" if item.get("course") else None,
                f"Meal Time: {', '.join(item['meal_time'])}" if item.get("meal_time") else None,
            ]:
                if part:
                    blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": part}}]}})
            if item.get("description"):
                blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": item["description"]}}]}})

    try:
        page = notion.pages.create(
            parent={"database_id": NOTION_PLACES_DB_ID},
            properties=properties,
            children=blocks[:100],
        )
        if len(blocks) > 100:
            notion.blocks.children.append(page["id"], children=blocks[100:200])
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(
            {"success": False, "error": f"Notion error: {exc}"},
            status_code=502,
        )

    return JSONResponse({"success": True, "notion_url": page.get("url", "")})
