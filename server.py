import os, re, time, random
from pathlib import Path
from typing import List, Dict
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# ========= Konfiguration =========
APP_TOKEN = os.getenv("APP_TOKEN", "change-me")   # für force=1
MAX_AGE_SECONDS = 6 * 3600                        # 6h Cache
CACHE = {"ts": 0, "data": {}}                     # { "84": [{name,price}, ...], ... }
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

# ========= FastAPI App =========
app = FastAPI(title="SBC Players – Live")

PUBLIC_DIR = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

@app.get("/")
def root():
    idx = PUBLIC_DIR / "index.html"
    return FileResponse(str(idx)) if idx.exists() else HTMLResponse("<h1>index.html fehlt</h1>", 404)

@app.head("/")
def head_root():
    return HTMLResponse("", status_code=200)

@app.get("/health")
def health():
    return {"ok": True}

# ========= Helpers =========
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

# ========= FUT.GG Scraper (ohne fragile CSS-Klassen) =========
# Beispiel-URL 84: https://www.fut.gg/players/?page=1&overall__gte=84&overall__lte=84
FUTGG_URL = "https://www.fut.gg/players/?page={page}&overall__gte={r}&overall__lte={r}"

PRICE_TOKEN = re.compile(r'(\d+(?:[.,]\d+)?)([kKmM]?)')  # 4.4K / 1.2M / 12,500 etc.

def parse_price_token(txt: str) -> int:
    t = (txt or "").strip().lower()
    if not t or any(bad in t for bad in ("extinct", "n/a", "—", "-", "unavailable")):
        return 0
    t = t.replace(" ", "")
    m = PRICE_TOKEN.search(t)
    if not m:
        return 0
    num = float(m.group(1).replace(",", "."))
    suf = m.group(2).lower()
    if suf == "k": num *= 1_000
    elif suf == "m": num *= 1_000_000
    return int(round(num))

def extract_name_from_html(chunk_html: str) -> str | None:
    # 1) alt="Name - 84 - ..."
    for m in re.finditer(r'alt="([^"]+)"', chunk_html):
        alt = m.group(1)
        if alt and not alt.lower().startswith(("coin", "sp", "sbc")):
            return alt.split(" - ")[0].strip()
    # 2) fallback: /players/<id>-barbra-banda/
    m = re.search(r'/players/\d+-([a-z0-9-]+)/', chunk_html)
    if m:
        slug = m.group(1)
        return " ".join(w.capitalize() for w in slug.split("-"))
    return None

def scrape_futgg_one_page(rating: int, page: int) -> List[Dict]:
    html = get_html(FUTGG_URL.format(page=page, r=rating))
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict] = []

    # Jede Karte als <a href="/players/...">…</a>; Preise erkennbar am Coin-Icon + Preistext
    for a in soup.find_all("a", href=re.compile(r"^/players/")):
        block = str(a)
        if "/public-assets/coin.webp" not in block:
            continue
        pm = (re.search(r'(\d+(?:\.\d+)?[kKmM])', block) or
              re.search(r'\b\d{1,3}(?:,\d{3})+\b', block))
        price_val = parse_price_token(pm.group(0)) if pm else 0
        if price_val <= 0:
            continue
        name = extract_name_from_html(block)
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
    print(f"[SCRAPER] rating {rating}: {len(all_items)} items")
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

# ========= Diagnose (optional, sehr nützlich) =========
@app.get("/debug_prices")
def debug_prices(ratings: str = Query(RATINGS_DEFAULT)):
    want = parse_ratings(ratings)
    data = scrape_many(want)  # frisch, ohne Cache
    counts = {str(r): len(data.get(str(r), [])) for r in want}
    sample = {str(r): data.get(str(r), [])[:5] for r in want}
    return {"counts": counts, "sample": sample}

@app.get("/raw")
def raw(rating: int = Query(84), page: int = Query(1)):
    url = FUTGG_URL.format(page=page, r=rating)
    try:
        html = get_html(url)
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}
    return {
        "ok": True, "url": url,
        "html_len": len(html),
        "has_coin_icon": ("/public-assets/coin.webp" in html),
        "snippet": html[:500]
    }

# ========= API: flache Liste für die Tabelle =========
@app.get("/players_flat")
def players_flat(
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

    flat = []
    for r in want:
        for item in CACHE["data"].get(str(r), []):
            flat.append({"name": item["name"], "price": item["price"], "rating": r})
    flat.sort(key=lambda x: (x["price"], -x["rating"], x["name"]))
    return JSONResponse(flat)
