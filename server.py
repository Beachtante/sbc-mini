import os, re, time, random
from pathlib import Path
from typing import List, Dict
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# ---------------- Config ----------------
APP_TOKEN = os.getenv("APP_TOKEN", "change-me")
MAX_AGE_SECONDS = 6 * 3600
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
app = FastAPI(title="SBC Optimizer (No Chem)")

PUBLIC_DIR = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

@app.get("/")
def root():
    idx = PUBLIC_DIR / "index.html"
    return FileResponse(str(idx)) if idx.exists() else HTMLResponse("<h1>index.html fehlt</h1>", 500)

@app.head("/")
def head_root():
    return HTMLResponse("", status_code=200)

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
    with httpx.Client(follow_redirects=True, timeout=25.0, headers=HEADERS) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.text

# ---------------- FUT.GG Scraper (robust, ohne CSS-Klassen) ----------------
FUTGG_URL = "https://www.fut.gg/players/?page={page}&overall__gte={r}&overall__lte={r}"
PRICE_RE = re.compile(r'(\d+(?:[.,]\d+)?)([kKmM]?)')

def _parse_price_text(txt: str) -> int:
    t = (txt or "").strip().lower()
    if not t or any(bad in t for bad in ("extinct", "n/a", "—", "-", "unavailable")):
        return 0
    t = t.replace(" ", "")
    m = PRICE_RE.search(t)
    if not m:
        return 0
    num = float(m.group(1).replace(",", "."))
    suf = m.group(2).lower()
    if suf == "k": num *= 1_000
    elif suf == "m": num *= 1_000_000
    return int(round(num))

def _name_from_chunk(chunk_html: str) -> str | None:
    # ALT-Text (z. B. 'Banda - 85 - Rare')
    for m in re.finditer(r'alt="([^"]+)"', chunk_html):
        alt = m.group(1)
        if alt and not alt.lower().startswith(("coin", "sp", "sbc")):
            return alt.split(" - ")[0].strip()
    # Fallback: Name aus URL-Slug
    m = re.search(r'/players/\d+-([a-z0-9-]+)/', chunk_html)
    if m:
        slug = m.group(1)
        return " ".join(w.capitalize() for w in slug.split("-"))
    return None

def scrape_futgg_one_page(rating: int, page: int) -> List[Dict]:
    html = get_html(FUTGG_URL.format(page=page, r=rating))
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []

    # Jede Karte ist ein <a href="/players/...">…</a>
    # Preis erkennbar am Coin-Icon + Preis-Token (4.4K, 82K, 1.2M oder 12,500)
    anchors = soup.find_all("a", href=re.compile(r"^/players/"))
    for a in anchors:
        block = str(a)
        if "/public-assets/coin.webp" not in block:
            continue
        pm = re.search(r'(\d+(?:\.\d+)?[kKmM])', block) or re.search(r'\b\d{1,3}(?:,\d{3})+\b', block)
        price_val = _parse_price_text(pm.group(0)) if pm else 0
        if price_val <= 0:
            continue
        name = _name_from_chunk(block)
        if not name:
            continue
        items.append({"name": name, "price": price_val})

    # de-dupe + sort
    seen = set(); uniq = []
    for it in items:
        key = (it["name"], it["price"])
        if key not in seen:
            seen.add(key); uniq.append(it)
    uniq.sort(key=lambda x: x["price"])
    return uniq

def scrape_futgg_for_rating(rating: int, max_pages: int = 3, min_needed: int = 25) -> List[Dict]:
    all_items: List[Dict] = []
    for p in range(1, max_pages + 1):
        page_items = scrape_futgg_one_page(rating, p)
        all_items.extend(x for x in page_items if x["price"] > 0)
        if len(all_items) >= min_needed:
            break
    all_items.sort(key=lambda x: x["price"])
    print(f"[SCRAPER] rating {rating}: {len(all_items)} items")  # Log für Render
    return all_items

def scrape_many(ratings: List[int]) -> Dict[str, List[Dict]]:
    data: Dict[str, List[Dict]] = {}
    for r in ratings:
        try:
            data[str(r)] = scrape_futgg_for_rating(r)
        except Exception as e:
            print(f"[SCRAPER] rating {r}: error {e}")
            data[str(r)] = []
    return data

# ---------------- Debug-Routen ----------------
@app.get("/debug_prices")
def debug_prices(ratings: str = Query(RATINGS_DEFAULT)):
    want = parse_ratings(ratings)
    data = scrape_many(want)  # immer frisch
    counts = {str(r): len(data.get(str(r), [])) for r in want}
    samples = {str(r): data.get(str(r), [])[:5] for r in want}
    return {"counts": counts, "sample": samples}

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
