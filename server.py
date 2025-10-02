from fastapi import Request

@app.post("/ingest")
async def ingest(request: Request, x_api_key: str = Header(None)):
    # Optional: setze INGEST_KEY als Env-Var, dann brauchen Freunde den Key
    if os.getenv("INGEST_KEY") and x_api_key != os.getenv("INGEST_KEY"):
        raise HTTPException(401, "Unauthorized")

    payload = await request.json()
    if not isinstance(payload, list):
        raise HTTPException(400, "Expected JSON list")

    added = 0
    for item in payload:
        try:
            name = str(item.get("name", "")).strip()
            price = int(item.get("price", 0))
            rating = int(item.get("rating", 0)) if item.get("rating") else None
        except Exception:
            continue
        if not name or price <= 0:
            continue
        key = str(rating) if rating else "unknown"
        CACHE["data"].setdefault(key, [])
        if not any(x["name"]==name and x["price"]==price for x in CACHE["data"][key]):
            CACHE["data"][key].append({"name": name, "price": price})
            added += 1

    CACHE["ts"] = time.time()
    return {"ok": True, "added": added}
