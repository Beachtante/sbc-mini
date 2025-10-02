# --- am Anfang stehen bleiben: Imports, App, HEADERS etc. ---

# ========= FUT.GG SCRAPER (aktualisiert) =========

# exakte URL je Rating (R): overall__gte=R & overall__lte=R
FUTGG_URL = "https://www.fut.gg/players/?page={page}&overall__gte={r}&overall__lte={r}"

import re
PRICE_RE = re.compile(r"(\d+(?:[.,]\d+)?)([kKmM]?)")

def parse_price(text: str) -> int:
    """
    Wandelt Preisstrings wie '12,500', '12k', '1.2m' in int (Coins) um.
    Extinct/N/A/— -> 0
    """
    t = (text or "").strip().lower()
    if not t or any(bad in t for bad in ("extinct", "n/a", "—", "-", "unavailable")):
        return 0
    # Leerzeichen entfernen, Komma zu Punkt
    t = t.replace(" ", "")
    m = PRICE_RE.search(t)
    if not m:
        return 0
    num = float(m.group(1).replace(",", "."))
    suf = m.group(2)
    if suf == "k" or suf == "K":
        num *= 1_000
    elif suf == "m" or suf == "M":
        num *= 1_000_000
    return int(round(num))

def scrape_futgg_one_page(rating: int, page: int) -> list[dict]:
    """
    Holt eine Seite von fut.gg für ein Rating und parst Name + Preis.
    Entfernt Duplikate und filtert Preis==0 (Extinct etc.).
    """
    html = get_html(FUTGG_URL.format(page=page, r=rating))
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    # A) Karten-Layout
    # (fut.gg ändert Klassen gelegentlich – wir nehmen mehrere Varianten.)
    cards = soup.select(
        ".player-card, [class*='PlayerCard'], [class*='PlayersList_item__card']"
    )
    for card in cards:
        name_el = (
            card.select_one(".player-name")
            or card.select_one("[class*='name']")
            or card.select_one("a[href*='/players/']")
        )
        price_el = (
            card.select_one(".price")
            or card.select_one(".price-value")
            or card.select_one("[class*='price']")
        )
        name = (name_el.get_text(strip=True) if name_el else "").strip()
        price = parse_price(price_el.get_text() if price_el else "")
        if name and price > 0:
            items.append({"name": name, "price": price})

    # B) Fallback: Tabellen-Layout
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

    # deduplizieren (gleiches (name, price) nur einmal)
    seen = set()
    uniq = []
    for it in items:
        key = (it["name"], it["price"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)

    return sorted(uniq, key=lambda x: x["price"])

def scrape_futgg_for_rating(rating: int, max_pages: int = 3, min_needed: int = 25) -> list[dict]:
    """
    Läuft Seite 1..max_pages ab, bis genügend Spieler mit Preis>0 gefunden sind.
    """
    all_items: list[dict] = []
    for p in range(1, max_pages + 1):
        page_items = scrape_futgg_one_page(rating, p)
        # nur >0 Preise behalten (Extinct etc. raus)
        page_items = [x for x in page_items if x["price"] > 0]
        all_items.extend(page_items)
        if len(all_items) >= min_needed:
            break
    all_items.sort(key=lambda x: x["price"])
    return all_items

def scrape_many(ratings: list[int]) -> dict[str, list[dict]]:
    data: dict[str, list[dict]] = {}
    for r in ratings:
        try:
            data[str(r)] = scrape_futgg_for_rating(r)
        except Exception:
            data[str(r)] = []
    return data
# ========= ENDE FUT.GG SCRAPER =========
