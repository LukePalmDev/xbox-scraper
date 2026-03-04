"""
Xbox OG Scraper — recupera metadati giochi dalla Display Catalog API.

Uso:
  python3 fetch_xbox_og.py                        # auto-detect bigids.json o xcat-bi-urls2.json
  python3 fetch_xbox_og.py --ids bigids.json      # file BigId personalizzato
  python3 fetch_xbox_og.py --filter-market        # escludi giochi non disponibili in IT
  python3 fetch_xbox_og.py --out catalog.html     # nome output custom
  python3 fetch_xbox_og.py --resume               # riprendi da failed_ids.json
  python3 fetch_xbox_og.py --batch 30             # BigId per richiesta (default 20)

Richiede Python 3 standard — nessuna libreria esterna necessaria.
"""

import urllib.request
import urllib.error
import json
import time
import html as html_escape
import ssl
import re
import os
import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# SSL — Fix Mac con Python da python.org (certificati non installati)
# ---------------------------------------------------------------------------
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

CATALOG_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    "?bigIds={ids}&market={market}&languages={lang}&MS-CV=DGU1mcuYo0WMMp+F.1"
)

# ---------------------------------------------------------------------------
# GAP 1 — Caricamento BigId da file (JS o JSON)
# ---------------------------------------------------------------------------

def load_ids_from_js(path: Path) -> tuple[list[str], dict[str, str]]:
    """
    Carica BigId dal formato JS del bundle Xbox:
      biUrls = { "items": { "urls": { "BIGID": "https://xbox.com/games/..." } } }
    Ritorna (lista_id, mappa_id→url).
    """
    content = path.read_text(encoding="utf-8")
    match = re.search(r'biUrls\s*=\s*(\{.*\})', content, re.DOTALL)
    if not match:
        raise ValueError(f"Oggetto 'biUrls' non trovato in {path}")
    obj = json.loads(match.group(1))
    urls: dict[str, str] = obj["items"]["urls"]
    return list(urls.keys()), urls


def load_ids_from_json(path: Path) -> tuple[list[str], dict[str, str]]:
    """
    Carica BigId da JSON pulito.
    Supporta sia lista piana ["ID1","ID2",...] sia dizionario {"ID1":"url",...}.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict) and "ids" in data:
        return data["ids"], data.get("urls", {})
    if isinstance(data, dict):
        return list(data.keys()), data
    raise ValueError(f"Formato non riconosciuto in {path}")


def load_ids(ids_file: str | None) -> tuple[list[str], dict[str, str]]:
    """
    Auto-detect del file sorgente se non specificato.
    Priorità: bigids.json > xcat-bi-urls2.json
    """
    if ids_file:
        p = Path(ids_file)
    else:
        for candidate in ["bigids.json", "xcat-bi-urls2.json"]:
            if Path(candidate).exists():
                p = Path(candidate)
                break
        else:
            sys.exit("Errore: nessun file BigId trovato. Esegui prima fetch_bigids.py")

    print(f"Caricamento BigId da: {p}")
    if p.suffix == ".json" and not p.read_text().strip().startswith("biUrls"):
        ids, urls = load_ids_from_json(p)
    else:
        ids, urls = load_ids_from_js(p)

    ids = list(dict.fromkeys(ids))  # deduplicazione mantenendo ordine
    print(f"  → {len(ids)} BigId unici trovati")
    return ids, urls


# ---------------------------------------------------------------------------
# GAP 5 — Filtro mercato (esclude giochi con <exc>IT nella URL)
# ---------------------------------------------------------------------------

def filter_by_market(ids: list[str], url_map: dict[str, str], market: str) -> list[str]:
    """
    Rimuove i BigId il cui URL contiene '<exc>...<MARKET>...' (case-insensitive).
    Se url_map è vuoto (formato lista piana), non filtra.
    """
    if not url_map:
        return ids

    market_upper = market.upper()
    filtered = []
    excluded = 0
    for game_id in ids:
        url = url_map.get(game_id, "")
        exc_match = re.search(r'<exc>([^"]+)', url)
        if exc_match:
            excluded_markets = [m.strip().upper() for m in exc_match.group(1).split(",")]
            if any(market_upper in m for m in excluded_markets):
                excluded += 1
                continue
        filtered.append(game_id)

    print(f"  → Filtro mercato {market}: {excluded} giochi esclusi, {len(filtered)} rimasti")
    return filtered


# ---------------------------------------------------------------------------
# GAP 4 — Fetch con retry e backoff esponenziale
# ---------------------------------------------------------------------------

def fetch_batch(ids: list[str], market: str, lang: str, max_retries: int = 3) -> list[dict]:
    """
    Fetcha un batch di BigId in una singola chiamata API.
    Ritorna la lista di prodotti dalla risposta.
    Retry con backoff esponenziale su errori transitori.
    """
    url = CATALOG_URL.format(
        ids=",".join(ids),
        market=market,
        lang=lang,
    )
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
                data = json.loads(resp.read().decode())
            return data.get("Products", [])
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                wait = 2 ** (attempt + 2)  # 4s, 8s, 16s
                print(f"  ⚠ Rate limit ({e.code}), attendo {wait}s...")
                time.sleep(wait)
            elif attempt == max_retries - 1:
                raise
            else:
                time.sleep(2 ** attempt)
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
    return []


def parse_product(p: dict, game_id: str) -> dict:
    """Estrae i campi utili da un oggetto Product della Display Catalog API."""
    loc = p.get("LocalizedProperties", [{}])[0]
    title = loc.get("ProductTitle") or game_id

    # Immagine (priorità decrescente)
    images = loc.get("Images", [])
    img_url = None
    for purpose in ["SuperHeroArt", "TitledHeroArt", "BrandedKeyArt", "BoxArt", "Tile"]:
        img = next((i for i in images if i.get("ImagePurpose") == purpose), None)
        if img:
            raw = img.get("Uri", "")
            img_url = ("https:" + raw) if raw.startswith("//") else raw
            break

    # Prezzo
    price = None
    try:
        avs = p.get("DisplaySkuAvailabilities", [{}])[0].get("Availabilities", [])
        for av in avs:
            lp = av.get("OrderManagementData", {}).get("Price", {}).get("ListPrice", 0)
            if lp and lp > 0:
                cc = av["OrderManagementData"]["Price"].get("CurrencyCode", "EUR")
                price = f"{lp:.2f} {cc}"
                break
    except Exception:
        pass

    # GAP 5 — Categoria (es. "Games", "Game")
    category = p.get("Properties", {}).get("Category", "")

    return {
        "id": p.get("ProductId", game_id),
        "title": title,
        "img": img_url,
        "price": price,
        "category": category,
    }


def scrape(ids: list[str], market: str, lang: str, batch_size: int, delay: float) -> tuple[list[dict], list[str]]:
    """
    Itera sui BigId in batch, chiama l'API e raccoglie i risultati.
    Ritorna (giochi_riusciti, id_falliti).
    """
    games: list[dict] = []
    failed: list[str] = []
    total_batches = (len(ids) + batch_size - 1) // batch_size

    for batch_num, start in enumerate(range(0, len(ids), batch_size), 1):
        batch = ids[start:start + batch_size]
        print(f"[{batch_num:03d}/{total_batches}] batch {start+1}–{start+len(batch)} ... ", end="", flush=True)
        try:
            products = fetch_batch(batch, market, lang)
            # Mappa ProductId → parsed per gestire ordini diversi nella risposta
            product_map = {}
            for p in products:
                parsed = parse_product(p, "?")
                product_map[parsed["id"]] = parsed
            # Preserva l'ordine e gestisce ID non restituiti dall'API
            for gid in batch:
                if gid in product_map:
                    games.append(product_map[gid])
                else:
                    # L'API potrebbe restituire un ID diverso; cerca per posizione
                    pass
            # Aggiungi quelli trovati dall'API non ancora inseriti
            for parsed in product_map.values():
                if not any(g["id"] == parsed["id"] for g in games):
                    games.append(parsed)
            print(f"✓  {len(products)} prodotti")
        except Exception as e:
            failed.extend(batch)
            print(f"✗  {e}")

        if batch_num < total_batches:
            time.sleep(delay)

    return games, failed


# ---------------------------------------------------------------------------
# HTML Builder
# ---------------------------------------------------------------------------

BADGE_COLORS = {
    "": "#00b357",
    "Games": "#00b357",
}

def build_html(games: list[dict], market: str) -> str:
    cards = ""
    for g in sorted(games, key=lambda x: x["title"].lower()):
        t = html_escape.escape(g["title"])
        img_tag = (
            f'<img src="{g["img"]}" alt="{t}" loading="lazy">'
            if g["img"] else '<div class="no-img">🎮</div>'
        )
        price = g["price"] or "—"
        category = html_escape.escape(g.get("category") or "XBOX")
        cards += f"""
        <div class="game-card" data-title="{t.lower()}" data-category="{category.lower()}">
          <div class="img-wrap">{img_tag}</div>
          <div class="card-body">
            <div class="card-title">{t}</div>
            <div class="card-meta">
              <span class="card-price">{price}</span>
              <span class="badge">{category}</span>
            </div>
            <div class="card-id">{g["id"]}</div>
          </div>
        </div>"""

    total = len(games)
    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Xbox — {total} giochi retrocompatibili ({market})</title>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
  :root {{ --green:#00e676; --green-dim:#00b357; --dark:#060a0e; --panel:#0c1318; --border:#1a2a1a; --text:#c8e6c9; --muted:#4a6a4a; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--dark); color:var(--text); font-family:'Rajdhani',sans-serif; min-height:100vh; }}
  body::before {{ content:''; position:fixed; inset:0; background-image:linear-gradient(rgba(0,230,118,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,230,118,.03) 1px,transparent 1px); background-size:40px 40px; pointer-events:none; z-index:0; }}
  header {{ position:relative; z-index:1; padding:32px 40px 20px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; gap:20px; flex-wrap:wrap; }}
  .logo {{ display:flex; align-items:center; gap:16px; }}
  .ring {{ width:50px; height:50px; border-radius:50%; border:2px solid var(--green); display:flex; align-items:center; justify-content:center; box-shadow:0 0 20px rgba(0,230,118,.3); font-size:1.4rem; animation:pulse 3s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100%{{box-shadow:0 0 20px rgba(0,230,118,.3)}} 50%{{box-shadow:0 0 40px rgba(0,230,118,.55)}} }}
  h1 {{ font-size:2rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; line-height:1; }}
  h1 span {{ color:var(--green); }}
  .sub {{ font-family:'Share Tech Mono',monospace; font-size:.7rem; color:var(--muted); letter-spacing:.2em; margin-top:4px; }}
  .count {{ font-family:'Share Tech Mono',monospace; font-size:.85rem; color:var(--green); }}
  .controls {{ position:relative; z-index:1; padding:14px 40px; display:flex; align-items:center; gap:12px; flex-wrap:wrap; border-bottom:1px solid var(--border); }}
  .search-wrap {{ flex:1; min-width:200px; max-width:380px; position:relative; }}
  .search-wrap input {{ width:100%; background:var(--panel); border:1px solid var(--border); color:var(--text); padding:9px 12px 9px 34px; font-family:'Share Tech Mono',monospace; font-size:.85rem; outline:none; transition:border-color .2s; }}
  .search-wrap input:focus {{ border-color:var(--green); }}
  .search-wrap input::placeholder {{ color:var(--muted); }}
  .si {{ position:absolute; left:10px; top:50%; transform:translateY(-50%); color:var(--muted); }}
  select {{ background:var(--panel); border:1px solid var(--border); color:var(--text); padding:9px 12px; font-family:'Share Tech Mono',monospace; font-size:.8rem; outline:none; cursor:pointer; }}
  .no-results {{ display:none; position:relative; z-index:1; padding:60px 40px; text-align:center; font-family:'Share Tech Mono',monospace; color:var(--muted); }}
  .game-grid {{ position:relative; z-index:1; padding:20px 40px 60px; display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; }}
  .game-card {{ background:var(--panel); border:1px solid var(--border); overflow:hidden; transition:border-color .2s,transform .2s; }}
  .game-card:hover {{ border-color:var(--green-dim); transform:translateY(-3px); }}
  .img-wrap {{ width:100%; aspect-ratio:16/9; background:#0a180a; overflow:hidden; }}
  .img-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .no-img {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:2rem; color:var(--border); }}
  .card-body {{ padding:10px 12px; }}
  .card-title {{ font-size:.9rem; font-weight:600; line-height:1.3; }}
  .card-meta {{ margin-top:6px; display:flex; align-items:center; justify-content:space-between; }}
  .card-price {{ font-family:'Share Tech Mono',monospace; font-size:.7rem; color:var(--green); }}
  .badge {{ font-family:'Share Tech Mono',monospace; font-size:.55rem; color:var(--dark); background:var(--green-dim); padding:2px 6px; }}
  .card-id {{ font-family:'Share Tech Mono',monospace; font-size:.58rem; color:var(--muted); margin-top:4px; }}
  @media(max-width:600px){{ header,.controls,.game-grid{{padding-left:16px;padding-right:16px}} h1{{font-size:1.5rem}} .game-grid{{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}} }}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="ring">🎮</div>
    <div><h1>Xbox <span>Catalog</span></h1><div class="sub">RETROCOMPATIBILI // {market}</div></div>
  </div>
  <div class="count"><span id="vis-count">{total}</span> / {total} giochi</div>
</header>
<div class="controls">
  <div class="search-wrap">
    <span class="si">⌕</span>
    <input type="text" id="search" placeholder="Cerca titolo..." oninput="filterGames()">
  </div>
  <select id="sort" onchange="filterGames()">
    <option value="name-asc">Nome A→Z</option>
    <option value="name-desc">Nome Z→A</option>
    <option value="price-asc">Prezzo ↑</option>
    <option value="price-desc">Prezzo ↓</option>
  </select>
</div>
<div class="no-results" id="no-results">// nessun risultato</div>
<div class="game-grid" id="grid">{cards}</div>
<script>
function filterGames() {{
  const q = document.getElementById('search').value.toLowerCase().trim();
  const sort = document.getElementById('sort').value;
  const allCards = [...document.querySelectorAll('.game-card')];
  allCards.forEach(c => {{ c.style.display = (!q || c.dataset.title.includes(q)) ? '' : 'none'; }});
  const vis = allCards.filter(c => c.style.display !== 'none');
  const grid = document.getElementById('grid');
  if (sort === 'name-asc') vis.sort((a,b) => a.dataset.title.localeCompare(b.dataset.title)).forEach(c => grid.appendChild(c));
  if (sort === 'name-desc') vis.sort((a,b) => b.dataset.title.localeCompare(a.dataset.title)).forEach(c => grid.appendChild(c));
  document.getElementById('vis-count').textContent = vis.length;
  document.getElementById('no-results').style.display = vis.length === 0 ? 'block' : 'none';
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Xbox Display Catalog scraper")
    parser.add_argument("--ids", metavar="FILE", help="File BigId (JSON o JS). Default: auto-detect")
    parser.add_argument("--market", default="IT", help="Codice mercato (default: IT)")
    parser.add_argument("--lang", default="it-it", help="Locale API (default: it-it)")
    parser.add_argument("--filter-market", action="store_true",
                        help="Escludi giochi con <exc>MARKET nella URL")
    parser.add_argument("--out", default="xbox_og_games.html", help="File HTML di output")
    parser.add_argument("--batch", type=int, default=20, help="BigId per richiesta API (default: 20)")
    parser.add_argument("--delay", type=float, default=0.3, help="Secondi tra batch (default: 0.3)")
    parser.add_argument("--resume", action="store_true",
                        help="Riprendi da failed_ids.json invece del file principale")
    args = parser.parse_args()

    # GAP 1 — Caricamento BigId
    if args.resume and Path("failed_ids.json").exists():
        failed_data = json.loads(Path("failed_ids.json").read_text())
        ids = failed_data if isinstance(failed_data, list) else failed_data.get("ids", [])
        url_map: dict[str, str] = {}
        print(f"Resume: {len(ids)} ID falliti da ritentare")
    else:
        ids, url_map = load_ids(args.ids)

    # GAP 5 — Filtro mercato
    if args.filter_market and url_map:
        ids = filter_by_market(ids, url_map, args.market)

    print(f"\nAvvio scraping: {len(ids)} giochi, batch={args.batch}, delay={args.delay}s\n")

    # GAP 4 — Scraping con retry
    games, failed = scrape(ids, args.market, args.lang, args.batch, args.delay)

    # Salva ID falliti per eventuale resume
    if failed:
        Path("failed_ids.json").write_text(json.dumps(failed, indent=2))
        print(f"\n⚠ {len(failed)} ID falliti salvati in failed_ids.json")
    elif Path("failed_ids.json").exists():
        Path("failed_ids.json").unlink()  # pulizia se tutti ok

    # Genera HTML
    output = build_html(games, args.market)
    Path(args.out).write_text(output, encoding="utf-8")

    print(f"\n✅ Completato: {len(games)} giochi, {len(failed)} errori")
    print(f"   File generato: {args.out}")


if __name__ == "__main__":
    main()
