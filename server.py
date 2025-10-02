import os, re, time, random
from pathlib import Path
from typing import List, Dict
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# ---------------- Config ----------------
APP_TOKEN = os.getenv("APP_TOKEN", "change-me")   # dein Admin-Code
MAX_AGE_SECONDS = 6 * 3600                        # Cache: 6 Stunden
CACHE = {"ts": 0, "data": {}}
RATINGS_DEFAULT = "82-86"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de,en;q=0.9",
    "Connection": "close",
    "Referer": "https://www.google.com/"
}

# ---------------- FastAPI App ----------------
app = FastAPI(title="SBC Mini (No Chem)")

PUBLIC_DIR = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

@app.get("/")
def root():
    idx = PUBLIC_DIR / "index.html"
    return FileResponse(str(idx)) if idx.exists() else HTMLResponse("<h1>index.html fehlt</h1>", 500)

@app.get("/health")
def health():
    return {"ok": True}

# ---------------- Helpers ----------------
def parse_ratings(s: str) -> List[int]:
    s = (s or "").strip()
    if "-" in s:
        a, b = [int(x) for x in s.split("-")]
        return list(range(a, b + 1))
    return [int(x) for x in s.split(",") if x.strip().isdigit()]

def polite_sleep():
    time.sleep(0.5 + random.random()*0.6)

def get_html(url: str) -> str:
    polite_sleep()
    with httpx.Client(follow_redirects=True, timeout=20.0, headers=HEADERS) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text

# ---------------- FUT.GG Scraper ----------------
# URL pro Rating (z. B. R=84):
# https://www.fut.gg/players/?page=1&overall__gte=84&overall__lte=84
FUTGG_URL = "https://www.fut.gg/players/?page={page}&overall__gte={r}&overall__lte={r}"

PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)([kKmM]?)")

def parse_price(text: str) -> int:
    """
    Wandelt Preisstrings wie '12,500', '12k', '1.2m' in int (Coins).
    Extinct/N/A/— -> 0
    """
    t = (text or "").strip().lower()
    if not t or any(bad in t for bad in ("extinct", "n/a", "—", "-", "unavailable")):
        return 0
    t = t.replace(" ", "")
    m = PRICE_RE.search(t)
    if not m:
        return 0
    num = float(m.group(1).replace(",", "."))
    suf = m.group(2).lower()
    if suf == "k":
        num *= 1_000
    elif suf == "m":
        num *= 1_000_000
    return int(round(num))

def scrape_futgg_one_page(rating: int, page: int) -> List[Dict]:
    html = get_html(FUTGG_URL.format(page=page, r=rating))
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []

    # Karten-Layout
    cards = soup.select(".player-card, [class*='PlayerCard'], [class*='PlayersList_item__card']")
    for card in cards:
        name_el = (card.select_one(".player-name") or
                   card.select_one("[class*='name']") or
                   card.select_one("a[href*='/players/']"))
        price_el = (card.select_one(".price") or
                    card.select_one(".price-value") or
                    card.select_one("[class*='price']"))
        name = (name_el.get_text(strip=True) if name_el else "").strip()
        price = parse_price(price_el.get_text() if price_el else "")
        if name and price > 0:
            items.append({"name": name, "price": price})

    # Tabellen-Layout Fallback
    if not items:
        for tr in soup.select("table tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            name_el = tr.select_one("td:nth-child(2) a") or tds[1]
            price_el = tr.select_one("td:nth-child(3), td .price") or tds[2]
            name = (name_el.get_text(strip=True) if name_el else "").strip()
            price = parse_price(price_el.get_text(strip=True) if price_el else "")
            if name and price > 0:
                items.append({"name": name, "price": price})

    # Deduplizieren
    seen = set()
    uniq = []
    for it in items:
        key = (it["name"], it["price"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)

    return sorted(uniq, key=lambda x: x["price"])

def scrape_futgg_for_rating(rating: int, max_pages: int = 3, min_needed: int = 25) -> List[Dict]:
    all_items: List[Dict] = []
    for p in range(1, max_pages + 1):
        page_items = scrape_futgg_one_page(rating, p)
        page_items = [x for x in page_items if x["price"] > 0]
        all_items.extend(page_items)
        if len(all_items) >= min_needed:
            break
    all_items.sort(key=lambda x: x["price"])
    return all_items

def scrape_many(ratings: List[int]) -> Dict[str, List[Dict]]:
    data: Dict[str, List[Dict]] = {}
    for r in ratings:
        try:
            data[str(r)] = scrape_futgg_for_rating(r)
        except Exception:
            data[str(r)] = []
    return data

# ---------------- API ----------------
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
