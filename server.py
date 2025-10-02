import os, json, time
from typing import List, Dict
from fastapi import FastAPI, HTTPException, Request, Header, Query
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware

# ================== App & CORS ==================
app = FastAPI(title="SBC Live Ingest")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # simpel halten
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================== Speicher (RAM-Cache) ==================
# Struktur: CACHE["data"] = { "84": [ {name, price}, ... ], "unknown": [...] }
CACHE: Dict[str, List[Dict]] = {"ts": 0, "data": {}}

def _put_item(rkey: str, name: str, price: int):
    """Dedup pro Name: behalte günstigsten Preis."""
    bucket = CACHE["data"].setdefault(rkey, [])
    for x in bucket:
        if x["name"] == name:
            if price < x["price"]:
                x["price"] = price
            return
    bucket.append({"name": name, "price": price})

def _ingest_items(items: list, rating_guess: int | None) -> int:
    """Items in den Cache kippen; return: wie viele neu (ohne Duplikate)."""
    added = 0
    for it in items:
        try:
            name = str(it.get("name", "")).strip()
            price = int(it.get("price", 0) or 0)
            rating = it.get("rating", None)
        except Exception:
            continue
        if not name or price <= 0:
            continue
        if isinstance(rating, int) and 60 <= rating <= 99:
            rkey = str(rating)
        elif isinstance(rating_guess, int):
            rkey = str(rating_guess)
        else:
            rkey = "unknown"
        before = len(CACHE["data"].get(rkey, []))
        _put_item(rkey, name, price)
        after = len(CACHE["data"].get(rkey, []))
        if after > before:
            added += 1
    CACHE["ts"] = time.time()
    return added

# ================== Helpers ==================
def parse_ratings(s: str) -> List[int]:
    s = (s or "").strip()
    if "-" in s:
        a, b = [int(x) for x in s.split("-")]
        return list(range(a, b + 1))
    return [int(x) for x in s.split(",") if x.strip().isdigit()]

# ================== Health & Root ==================
@app.get("/health")
def health():
    return {"ok": True, "buckets": {k: len(v) for k, v in CACHE["data"].items()}}

@app.get("/")
def root():
    return HTMLResponse("<h3>SBC Live Ingest läuft</h3><p>Nutze /players_flat, /players_csv, /ingest oder /ingest_get</p>")

# ================== Ingest (normal, POST JSON) ==================
# Body akzeptiert EINE der beiden Formen:
#  A) { "items":[{name,price,rating?}, ...], "rating_guess":84 }
#  B) [ {name,price,rating?}, ... ]
@app.post("/ingest")
async def ingest(request: Request, x_api_key: str = Header(None)):
    if os.getenv("INGEST_KEY") and x_api_key != os.getenv("INGEST_KEY"):
        raise HTTPException(401, "Unauthorized")
    payload = await request.json()
    if isinstance(payload, dict):
        items = payload.get("items", [])
        rating_guess = payload.get("rating_guess")
    elif isinstance(payload, list):
        items = payload
        rating_guess = None
    else:
        raise HTTPException(400, "Expected list or object with 'items'")
    added = _ingest_items(items, rating_guess)
    return {"ok": True, "added": added, "buckets": {k: len(v) for k, v in CACHE["data"].items()}}

# ================== Ingest (Fallback, GET mit urlencoded JSON) ==================
# Aufruf: /ingest_get?data=<urlenc(json)>
@app.get("/ingest_get")
def ingest_get(data: str = Query(...)):
    try:
        payload = json.loads(data)
    except Exception:
        raise HTTPException(400, "Invalid JSON in 'data'")
    if isinstance(payload, dict):
        items = payload.get("items", [])
        rating_guess = payload.get("rating_guess")
    elif isinstance(payload, list):
        items = payload
        rating_guess = None
    else:
        raise HTTPException(400, "Expected list or object with 'items'")
    added = _ingest_items(items, rating_guess)
    return {"ok": True, "added": added, "buckets": {k: len(v) for k, v in CACHE["data"].items()}}

# ================== Auslesen (flache Liste für Tabelle) ==================
@app.get("/players_flat")
def players_flat(ratings: str = Query("82-86")):
    want = parse_ratings(ratings)
    flat = []
    for r in want:
        for it in CACHE["data"].get(str(r), []):
            flat.append({"rating": int(r), "name": it["name"], "price": it["price"]})
    # sortiere günstigste zuerst
    flat.sort(key=lambda x: (x["price"], -x["rating"], x["name"]))
    return JSONResponse(flat)

# ================== CSV-Download ==================
@app.get("/players_csv")
def players_csv(ratings: str = Query("82-86")):
    want = parse_ratings(ratings)
    lines = ["rating,name,price"]
    for r in want:
        for it in CACHE["data"].get(str(r), []):
            nm = it["name"].replace('"', '""')
            lines.append(f'{r},"{nm}",{it["price"]}')
    csv = "\n".join(lines)
    return Response(
        content=csv,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=players.csv"},
    )
