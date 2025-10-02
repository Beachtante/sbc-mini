import os, re, time, random, json
from pathlib import Path
from typing import List, Dict, Tuple
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

USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)
REQ_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json",
    "Accept-Language": "de,en;q=0.9",
    "Connection": "close",
    "Referer": "https://www.google.com/",
}

# ---------------- App & static ----------------
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

def sleep_polite():
    time.sleep(0.5 + random.random()*0.6)

def get(url: str) -> httpx.Response:
    sleep_polite()
    with httpx.Client(follow_redirects=True, timeout=20.0, headers=REQ_HEADERS) as c:
        r = c.get(url)
        r.raise_for_status()
        return r

# -------- 1) Spieler-IDs pro Rating holen (serverseitig gerenderte Liste) --------
PLAYERS_LIST_URL = "https://www.futbin.com/players?rating={rating}&page={page}"

ID_HREF_RE = re.compile(r"/players/(\d+)/")

def fetch_player_ids_for_rating(rating: int, max_players: int = 120) -> List[Tuple[str, str]]:
    """
    Liefert bis zu max_players Einträge (name, id) für ein Rating.
    Wir gehen über ein paar Seiten, bis genug IDs gefunden sind.
    """
    found: List[Tuple[str, str]] = []
    for page in range(1, 6):  # bis zu 5 Seiten abklappern
        url = PLAYERS_LIST_URL.format(rating=rating, page=page)
        try:
            html = get(url).text
        except Exception:
            break
        soup = BeautifulSoup(html, "html.parser")
        # Finde Links, die wie /players/<id>/… aussehen
        for a in soup.select("a[href*='/players/']"):
            href = a.get("href") or ""
            m = ID_HREF_RE.search(href)
            if not m:
                continue
            pid = m.group(1)
            name = (a.get_text(strip=True) or "").replace("\n", " ").strip()
            if pid and name and (name, pid) not in found:
                found.append((name, pid))
            if len(found) >= max_players:
                return found
        # Wenn kaum Links auf der Seite -> vermutlich JS/Block, weiter versuchen
    return found

# -------- 2) FUTBIN JSON-Preise je Spieler-ID --------
# Wir probieren Jahr 25 und als Fallback 24 (EA FC 25/24)
PRICE_ENDPOINTS = [
    "https://www.futbin.com/25/playerPrices?player={pid}",
    "https://www.futbin.com/24/playerPrices?player={pid}",
]

def best_price_from_price_json(js: dict) -> int:
    """
    Nimmt die JSON-Struktur von /playerPrices und extrahiert den kleinsten sinnvollen Preis.
    Struktur (typisch):
      { "<id>": { "prices": { "ps": {...}, "xbox": {...}, "pc": {...} } } }
    In den Plattformobjekten gibt es oft Keys wie "LCPrice" oder "LC" (als Strings mit Kommas).
    """
    if not isinstance(js, dict) or not js:
        return 0
    root = next(iter(js.values()))  # das Objekt unter der ID
    prices = root.get("prices") if isinstance(root, dict) else None
    if not isinstance(prices, dict):
        return 0

    candidates: List[int] = []
    for plat, pdata in prices.items():
        if not isinstance(pdata, dict):
            continue
        # Sammele alle numerisch interpretierbaren Preisstrings
        for k in ("LCPrice", "LC", "MinPrice", "maxPrice", "PRP", "price"):
            v = pdata.get(k)
            if isinstance(v, str):
                digits = "".join(ch for ch in v if ch.isdigit())
                if digits:
                    candidates.append(int(digits))
            elif isinstance(v, (int, float)):
                candidates.append(int(v))
    return min(candidates) if candidates else 0

def fetch_price_for_player_id(pid: str) -> int:
    for tpl in PRICE_ENDPOINTS:
        url = tpl.format(pid=pid)
        try:
            r = get(url)
            # Kann text/html mit JSON als Text sein → robust parsen
            js = r.json()
            p = best_price_from_price_json(js)
            if p > 0:
                return p
        except Exception:
            continue
    return 0

def scrape_cheapest_for_rating_via_json(rating: int, per_rating_limit: int = 60) -> List[Dict]:
    """
    Pipeline:
      - IDs für Rating holen (HTML-Liste, meist serverseitig gerendert)
      - Für jede ID JSON-Preis holen
      - Günstigste Liste zurückgeben: [{name, price}, ...]
    """
    pairs = fetch_player_ids_for_rating(rating, max_players=per_rating_limit)
    items: List[Dict] = []
    for name, pid in pairs:
        price = fetch_price_for_player_id(pid)
        if price > 0:
            items.append({"name": name, "price": price})
    items.sort(key=lambda x: x["price"])
    return items

def scrape_many(ratings: List[int]) -> Dict[str, List[Dict]]:
    data: Dict[str, List[Dict]] = {}
    for r in ratings:
        try:
            data[str(r)] = scrape_cheapest_for_rating_via_json(r)
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
