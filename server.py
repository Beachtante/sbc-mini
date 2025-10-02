import asyncio, time, random, os
from pathlib import Path
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright

# --- Konfiguration ---
APP_TOKEN = os.getenv("APP_TOKEN", "change-me")  # nur für "Frisch laden (Admin)"
MAX_AGE_SECONDS = 6 * 3600                      # Cache-Zeit: 6 Stunden
CACHE = {"ts": 0, "data": {}}                   # In-Memory Cache
RATINGS_DEFAULT = "82-86"                       # Standardbereich

# --- FastAPI App ---
app = FastAPI(title="SBC Mini (No Chem)")

# Statische Dateien (liefert index.html aus)
PUBLIC_DIR = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

@app.get("/")
def root():
    idx = PUBLIC_DIR / "index.html"
    return FileResponse(str(idx)) if idx.exists() else HTMLResponse("<h1>index.html fehlt</h1>", 500)

# --- Scraper-Funktionen ---
async def scrape_rating(page, rating:int):
    url = f"https://www.futbin.com/squad-building-challenges/cheapest?r={rating}"
    await page.goto(url, timeout=60000).
    await page.wait_for_selector("table")
    await asyncio.sleep(1.2 + random.random())  # kurze Wartezeit für JS
    rows = await page.query_selector_all("table tbody tr")
    items = []
    for r in rows:
        try:
            name_el = await r.query_selector("td:nth-child(1)")
            price_el = await r.query_selector("td:nth-child(3)")
            name = (await name_el.inner_text()).strip() if name_el else ""
            price_txt = (await price_el.inner_text()).strip() if price_el else ""
            price = int("".join(ch for ch in price_txt if ch.isdigit()) or 0)
            if name and price > 0:
                items.append({"name": name, "price": price})
        except:
            pass
    items.sort(key=lambda x: x["price"])
    return items

def parse_ratings(s: str):
    s = (s or "").trim() if hasattr(str, 'trim') else (s or "").strip()
    if "-" in s:
        a, b = [int(x) for x in s.split("-")]
        return list(range(a, b + 1))
    return [int(x) for x in s.split(",") if x.strip().isdigit()]

async def scrape_many(ratings):
    data = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent="Mozilla/5.0 (SBC-Optimizer)")
        for r in ratings:
            data[str(r)] = await scrape_rating(page, r)
            await asyncio.sleep(1.0 + random.random()*1.5)  # höflich bleiben
        await browser.close()
    return data

# --- API-Endpunkt ---
@app.get("/prices")
async def get_prices(
    ratings: str = Query(RATINGS_DEFAULT),
    force: int = Query(0),
    x_api_key: str = Header(None)
):
    # Nur "force=1" braucht Token (für dich). Normale Nutzer brauchen keins.
    if force == 1 and x_api_key != APP_TOKEN:
        raise HTTPException(401, "Unauthorized")

    # gewünschte Ratings parsen
    want = parse_ratings(ratings)
    now = time.time()
    stale = (now - CACHE["ts"]) > MAX_AGE_SECONDS
    have_all = all(str(r) in CACHE["data"] and CACHE["data"][str(r)] for r in want)

    # Scrapen, wenn Cache alt/leer oder "force=1"
    if force == 1 or stale or not have_all:
        data = await scrape_many(want)
        CACHE["data"].update(data)
        CACHE["ts"] = now

    # Nur gewünschte Ratings zurückgeben
    out = {str(r): CACHE["data"].get(str(r), []) for r in want}
    return JSONResponse(out)
