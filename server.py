import re
from bs4 import BeautifulSoup

# schon vorhanden:
# FUTGG_URL = "https://www.fut.gg/players/?page={page}&overall__gte={r}&overall__lte={r}"
PRICE_RE = re.compile(r'(\d+(?:[.,]\d+)?)([kKmM]?)')

def _parse_price_text(txt: str) -> int:
    """
    '12,500' / '12k' / '1.2m' -> int Coins.
    Ignoriere 'extinct', 'n/a', '—', '-' -> 0.
    """
    t = (txt or "").strip().lower()
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

def _name_from_chunk(chunk_html: str) -> str | None:
    """
    Versucht zuerst ALT-Text (z. B. 'Banda - 85 - Rare'),
    fallback: Name aus URL-Slug '/players/<id>-barbra-banda/...'
    """
    # ALT-Texte prüfen
    for m in re.finditer(r'alt="([^"]+)"', chunk_html):
        alt = m.group(1)
        if alt and not alt.lower().startswith(("coin", "sp", "sbc")):
            # häufig 'Name - 85 - Rare' -> nimm Teil vor dem ersten ' - '
            return alt.split(" - ")[0].strip()
    # fallback: URL-Slug
    m = re.search(r'/players/\d+-([a-z0-9-]+)/', chunk_html)
    if m:
        slug = m.group(1)
        # schöner Name aus dem Slug
        return " ".join([w.capitalize() for w in slug.split("-")])
    return None

def scrape_futgg_one_page(rating: int, page: int) -> list[dict]:
    """
    Parst *ohne* CSS-Klassen:
    - Finde alle <a href="/players/...">…</a>
    - Karte gilt als Treffer, wenn innerhalb der Karte das Coin-Icon vorkommt
      ('/public-assets/coin.webp') und danach ein Preistext steht (4.4K, 82K, 1.2M …)
    - Name via ALT oder URL-Slug, Preise per _parse_price_text()
    """
    html = get_html(FUTGG_URL.format(page=page, r=rating))
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    # alle Player-Karten als Anchor-Blöcke
    for a in soup.find_all("a", href=re.compile(r"^/players/")):
        block = str(a)
        if "/public-assets/coin.webp" not in block:
            continue  # keine Preiszeile in dieser Karte

        # Preis: suche z. B. '4.4K', '82K', '1.2M' im Block
        pm = re.search(r'(\d+(?:\.\d+)?[kKmM])', block)
        if not pm:
            # manchmal stehen Preise auch als "12,500" ohne K/M – optional:
            pm = re.search(r'\b\d{1,3}(?:,\d{3})+\b', block)
        price_val = _parse_price_text(pm.group(0)) if pm else 0
        if price_val <= 0:
            continue

        # Name
        name = _name_from_chunk(block)
        if not name:
            continue

        items.append({"name": name, "price": price_val})

    # deduplizieren + sortieren
    seen = set()
    uniq = []
    for it in items:
        key = (it["name"], it["price"])
        if key not in seen:
            seen.add(key)
            uniq.append(it)

    return sorted(uniq, key=lambda x: x["price"])
