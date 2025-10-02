import time, os, random
from pathlib import Path
from typing import List, Dict
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# --- Konfiguration ---
APP_TOKEN = os.getenv("APP_TOKEN", "change-me")   # nur für force=1
MAX_AGE_SECONDS = 6 * 3600                        # Cache 6h
CACHE = {"ts": 0, "data": {}}                     # In-Memory Cache
RATINGS_DEFAULT = "82-86"

# --- FastAPI ---
app = FastAPI(title="SBC Mini (No Chem)")
PUBLIC_DIR = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

@app.get("/")
def root():
    idx = PUBLIC_DIR / "index.html"
    return FileResponse(str(idx)) if idx.exists() else HTMLResponse("<h1>index.html fehlt</h1>", 500)

# --- Helpers ---
def parse_ratings(s: str) -> List[int]:
    s = (s or "").strip()
    if "-" in s:
        a, b = [int(x) for x in s.split("-")]
        return list(range(a, b + 1))
    return [int(x) for x in s.split(",") if x.strip().isdigit()]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SBC-Optimizer/1.0; +https://example.com)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Connection": "close",
}

def fetch_html(url: str) -> str:
    # kleine Random-Delay, höflich bleiben
    time.sleep(0.8 + random.random()*0.7)
    with httpx.Client(follow_redirects=True, timeout=20.0, headers=HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text

def parse_cheapest_table(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    # Generisch: nimm die erste Tabelle mit tbody/rows
    table = soup.select_one("table tbody")
    items = []
    if not table:
        return items
    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        name = tds[0].get_text(strip=True)
        price_txt = tds[2].get_text(strip=True)
        # Zahlen rausziehen, z.B. "2,300" -> 2300
        digits = "".join(ch for ch in price_txt if ch.isdigit())
        if not name or not digits:
            continue
        price = int(digits)
        if price > 0:
            items.append({"name": name, "price": price})
    # günstigste zuerst
    items.sort(key=lambda x: x["price"])
    return items

def scrape_cheapest_for_rating(rating: int) -> List[Dict]:
    url = f"https://www.futbin.com/squad-building-challenges/cheapest?r={rating}"
    html = fetch_html(url)
    return parse_cheapest_table(html)

def scrape_many(ratings: List[int]) -> Dict[str, List[Dict]]:
    data = {}
    for r in ratings:
        try:
            data[str(r)] = scrape_cheapest_for_rating(r)
        except Exception:
            # Bei Block/Fehler: leere Liste, damit Service trotzdem antwortet
            data[str(r)] = []
    return data

# --- API ---
@app.get("/prices")
def get_prices(
    ratings: str = Query(RATINGS_DEFAULT),
    force: int = Query(0),
    x_api_key: str = Header(None)
):
    if force == 1 and x_api_key != APP_TOKEN:
        raise HTTPException(401, "Unauthorized")

    want = parse_ratings(ratings)
    now = time.time()
    stale = (now - CACHE["ts"]) > MAX_AGE_SECONDS
    have_all = all(str(r) in CACHE["data"] and CACHE["data"][str(r)] for r in want)

    if force == 1 or stale or not have_all:
        CACHE["data"].update(scrape_many(want))
        CACHE["ts"] = now

    out = {str(r): CACHE["data"].get(str(r), []) for r in want}
    return JSONResponse(out)
