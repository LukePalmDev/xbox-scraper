"""
Xbox OG Scraper — recupera metadati giochi dalla Display Catalog API.

Uso:
  python3 fetch_xbox_og.py                            # menu interattivo
  python3 fetch_xbox_og.py --category all             # tutti i giochi
  python3 fetch_xbox_og.py --category xone            # solo Xbox One
  python3 fetch_xbox_og.py --category 360             # solo Xbox 360
  python3 fetch_xbox_og.py --category og              # solo OG Xbox
  python3 fetch_xbox_og.py --category series          # solo Series X|S enhanced
  python3 fetch_xbox_og.py --filter-market            # escludi giochi non disponibili in IT
  python3 fetch_xbox_og.py --out catalog.html         # nome output custom
  python3 fetch_xbox_og.py --resume                   # riprendi da failed_ids.json
  python3 fetch_xbox_og.py --batch 30 --delay 0.5    # parametri rete

Richiede Python 3 standard — nessuna libreria esterna necessaria.
"""

import urllib.request
import urllib.error
import json
import time
import html as html_escape
import ssl
import re
import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# SSL
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

# Mappa codici categoria → generazione console API
CONSOLE_GENS = {
    "og":     "ConsoleGen6",
    "360":    "ConsoleGen7",
    "xone":   "ConsoleGen8",
    "series": "ConsoleGen9",
    "all":    None,
}

CATEGORY_LABELS = {
    "all":    "Tutti i giochi",
    "og":     "Xbox Original (OG)",
    "360":    "Xbox 360",
    "xone":   "Xbox One",
    "series": "Xbox Series X|S Enhanced",
}


# ---------------------------------------------------------------------------
# FEATURE A — Menu interattivo da terminale
# ---------------------------------------------------------------------------

def select_category_interactive() -> str:
    """Mostra un menu numerato e restituisce il codice categoria scelto."""
    print()
    print("╔══════════════════════════════════════╗")
    print("║        XBOX SCRAPER — Categoria      ║")
    print("╚══════════════════════════════════════╝")
    print()
    print("  Seleziona la generazione di giochi da scaricare:")
    print()

    options = ["all", "xone", "360", "og", "series"]
    for i, code in enumerate(options, 1):
        print(f"  [{i}] {CATEGORY_LABELS[code]}")

    print()
    while True:
        try:
            raw = input("  Scelta [1-5] (default 1): ").strip()
            if raw == "":
                return "all"
            n = int(raw)
            if 1 <= n <= len(options):
                chosen = options[n - 1]
                print(f"\n  → Selezionato: {CATEGORY_LABELS[chosen]}")
                return chosen
        except (ValueError, EOFError):
            pass
        print("  Inserisci un numero tra 1 e 5.")


# ---------------------------------------------------------------------------
# GAP 1 — Caricamento BigId da file (JS o JSON)
# ---------------------------------------------------------------------------

def load_ids_from_js(path: Path) -> tuple[list[str], dict[str, str]]:
    content = path.read_text(encoding="utf-8")
    match = re.search(r'biUrls\s*=\s*(\{.*\})', content, re.DOTALL)
    if not match:
        raise ValueError(f"Oggetto 'biUrls' non trovato in {path}")
    obj = json.loads(match.group(1))
    urls: dict[str, str] = obj["items"]["urls"]
    return list(urls.keys()), urls


def load_ids_from_json(path: Path) -> tuple[list[str], dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data, {}
    if isinstance(data, dict) and "ids" in data:
        return data["ids"], data.get("urls", {})
    if isinstance(data, dict):
        return list(data.keys()), data
    raise ValueError(f"Formato non riconosciuto in {path}")


def load_ids(ids_file: str | None) -> tuple[list[str], dict[str, str]]:
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
    content_start = p.read_text(encoding="utf-8").strip()
    if p.suffix == ".json" and not content_start.startswith("biUrls"):
        ids, urls = load_ids_from_json(p)
    else:
        ids, urls = load_ids_from_js(p)

    ids = list(dict.fromkeys(ids))
    print(f"  → {len(ids)} BigId unici trovati")
    return ids, urls


# ---------------------------------------------------------------------------
# GAP 5 — Filtro mercato
# ---------------------------------------------------------------------------

def filter_by_market(ids: list[str], url_map: dict[str, str], market: str) -> list[str]:
    if not url_map:
        return ids
    market_upper = market.upper()
    filtered, excluded = [], 0
    for game_id in ids:
        url = url_map.get(game_id, "")
        exc_match = re.search(r'<exc>([^"]+)', url)
        if exc_match:
            excluded_markets = [m.strip().upper() for m in exc_match.group(1).split(",")]
            if any(market_upper in m for m in excluded_markets):
                excluded += 1
                continue
        filtered.append(game_id)
    print(f"  → Filtro mercato {market}: {excluded} esclusi, {len(filtered)} rimasti")
    return filtered


# ---------------------------------------------------------------------------
# GAP 4 — Fetch con retry e backoff esponenziale
# ---------------------------------------------------------------------------

def fetch_batch(ids: list[str], market: str, lang: str, max_retries: int = 3) -> list[dict]:
    url = CATALOG_URL.format(ids=",".join(ids), market=market, lang=lang)
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
                data = json.loads(resp.read().decode())
            return data.get("Products", [])
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                wait = 2 ** (attempt + 2)
                print(f"\n  ⚠ Rate limit ({e.code}), attendo {wait}s...")
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


# ---------------------------------------------------------------------------
# FEATURE A+B — Rilevamento generazione console dall'API
# ---------------------------------------------------------------------------

CONSOLE_GEN_LABEL = {
    "ConsoleGen6": "OG Xbox",
    "ConsoleGen7": "Xbox 360",
    "ConsoleGen8": "Xbox One",
    "ConsoleGen9": "Series X|S",
}

def detect_console_gen(props: dict) -> str:
    """
    Determina la generazione originale del gioco da XboxConsoleGenOptimized.
    Regola: usa la generazione più bassa tra quelle ottimizzate
    (= la console per cui il gioco è stato originariamente sviluppato).
    """
    optimized: list[str] = props.get("XboxConsoleGenOptimized") or []
    if not optimized:
        compatible: list[str] = props.get("XboxConsoleGenCompatible") or []
        optimized = compatible

    # Ordina per numero generazione (ConsoleGen6 < ConsoleGen7 < ...)
    gen_nums = sorted(
        (int(re.search(r'\d+', g).group()) for g in optimized if re.search(r'\d+', g))
    )
    if not gen_nums:
        return "Unknown"

    lowest = gen_nums[0]
    return CONSOLE_GEN_LABEL.get(f"ConsoleGen{lowest}", f"Gen{lowest}")


def matches_category(gen_label: str, category_code: str) -> bool:
    """Ritorna True se il gioco appartiene alla categoria selezionata."""
    if category_code == "all":
        return True
    target_gen = CONSOLE_GEN_LABEL.get(CONSOLE_GENS[category_code], "")
    return gen_label == target_gen


def parse_product(p: dict, game_id: str) -> dict:
    loc = p.get("LocalizedProperties", [{}])[0]
    title = loc.get("ProductTitle") or game_id

    # Immagine
    images = loc.get("Images", [])
    img_url = None
    for purpose in ["SuperHeroArt", "TitledHeroArt", "BrandedKeyArt", "BoxArt", "Tile"]:
        img = next((i for i in images if i.get("ImagePurpose") == purpose), None)
        if img:
            raw = img.get("Uri", "")
            img_url = ("https:" + raw) if raw.startswith("//") else raw
            break

    # Prezzo — estrae anche valore numerico per sort HTML
    price_str = None
    price_num = 0.0
    try:
        avs = p.get("DisplaySkuAvailabilities", [{}])[0].get("Availabilities", [])
        for av in avs:
            lp = av.get("OrderManagementData", {}).get("Price", {}).get("ListPrice", 0)
            if lp and lp > 0:
                cc = av["OrderManagementData"]["Price"].get("CurrencyCode", "EUR")
                price_str = f"{lp:.2f} {cc}"
                price_num = float(lp)
                break
    except Exception:
        pass

    # FEATURE A+B — Generazione console
    props = p.get("Properties", {})
    console_gen = detect_console_gen(props)

    # FEATURE C — Genere gioco (da Categories)
    categories: list[str] = props.get("Categories") or []
    genre = categories[0] if categories else (props.get("Category") or "")

    return {
        "id": p.get("ProductId", game_id),
        "title": title,
        "img": img_url,
        "price": price_str,
        "price_num": price_num,
        "console_gen": console_gen,
        "genre": genre,
    }


def scrape(
    ids: list[str],
    market: str,
    lang: str,
    batch_size: int,
    delay: float,
    category_code: str,
) -> tuple[list[dict], list[str]]:
    games: list[dict] = []
    failed: list[str] = []
    skipped = 0
    total_batches = (len(ids) + batch_size - 1) // batch_size

    for batch_num, start in enumerate(range(0, len(ids), batch_size), 1):
        batch = ids[start:start + batch_size]
        print(f"[{batch_num:03d}/{total_batches}] batch {start+1}–{start+len(batch)} ... ", end="", flush=True)
        try:
            products = fetch_batch(batch, market, lang)
            added = 0
            for p in products:
                parsed = parse_product(p, "?")
                # FEATURE A — Filtro per generazione console post-fetch
                if not matches_category(parsed["console_gen"], category_code):
                    skipped += 1
                    continue
                if not any(g["id"] == parsed["id"] for g in games):
                    games.append(parsed)
                    added += 1
            print(f"✓  {len(products)} ricevuti, {added} aggiunti")
        except Exception as e:
            failed.extend(batch)
            print(f"✗  {e}")

        if batch_num < total_batches:
            time.sleep(delay)

    if skipped:
        print(f"  → {skipped} giochi esclusi per categoria '{category_code}'")
    return games, failed


# ---------------------------------------------------------------------------
# FEATURE C — HTML Builder con filtri avanzati
# ---------------------------------------------------------------------------

def slugify(s: str) -> str:
    """Normalizza una stringa per usarla come valore data-*."""
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def build_html(games: list[dict], market: str, category_code: str) -> str:
    # Raccogli tutti i generi e le generazioni presenti nei dati
    all_genres = sorted({g["genre"] for g in games if g["genre"]})
    all_gens = sorted({g["console_gen"] for g in games if g["console_gen"] and g["console_gen"] != "Unknown"})

    # Genera pill-button per generi
    genre_pills = '<button class="pill active" data-filter-genre="all" onclick="setFilter(\'genre\',\'all\',this)">Tutti i generi</button>\n'
    for genre in all_genres:
        slug = slugify(genre)
        esc = html_escape.escape(genre)
        genre_pills += f'    <button class="pill" data-filter-genre="{slug}" onclick="setFilter(\'genre\',\'{slug}\',this)">{esc}</button>\n'

    # Genera pill-button per generazioni console
    gen_pills = '<button class="pill active" data-filter-gen="all" onclick="setFilter(\'gen\',\'all\',this)">Tutte le console</button>\n'
    for gen in all_gens:
        slug = slugify(gen)
        esc = html_escape.escape(gen)
        gen_pills += f'    <button class="pill" data-filter-gen="{slug}" onclick="setFilter(\'gen\',\'{slug}\',this)">{esc}</button>\n'

    # Card HTML
    cards = ""
    for g in sorted(games, key=lambda x: x["title"].lower()):
        t = html_escape.escape(g["title"])
        img_tag = (
            f'<img src="{g["img"]}" alt="{t}" loading="lazy">'
            if g["img"] else '<div class="no-img">🎮</div>'
        )
        price_display = g["price"] or "—"
        genre_slug = slugify(g["genre"]) if g["genre"] else "unknown"
        gen_slug = slugify(g["console_gen"]) if g["console_gen"] else "unknown"
        genre_label = html_escape.escape(g["genre"]) if g["genre"] else "—"
        gen_label = html_escape.escape(g["console_gen"]) if g["console_gen"] else "—"

        cards += f"""
        <div class="game-card"
             data-title="{t.lower()}"
             data-genre="{genre_slug}"
             data-gen="{gen_slug}"
             data-price-num="{g['price_num']:.2f}">
          <div class="img-wrap">{img_tag}</div>
          <div class="card-body">
            <div class="card-title">{t}</div>
            <div class="card-tags">
              <span class="tag tag-gen">{gen_label}</span>
              <span class="tag tag-genre">{genre_label}</span>
            </div>
            <div class="card-meta">
              <span class="card-price">{price_display}</span>
            </div>
            <div class="card-id">{g["id"]}</div>
          </div>
        </div>"""

    total = len(games)
    cat_label = html_escape.escape(CATEGORY_LABELS.get(category_code, category_code))

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Xbox — {total} giochi ({cat_label} · {market})</title>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
  :root {{ --green:#00e676; --green-dim:#00b357; --dark:#060a0e; --panel:#0c1318; --border:#1a2a1a; --text:#c8e6c9; --muted:#4a6a4a; --pill-bg:#0f1f0f; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--dark); color:var(--text); font-family:'Rajdhani',sans-serif; min-height:100vh; }}
  body::before {{ content:''; position:fixed; inset:0; background-image:linear-gradient(rgba(0,230,118,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,230,118,.03) 1px,transparent 1px); background-size:40px 40px; pointer-events:none; z-index:0; }}

  header {{ position:relative; z-index:1; padding:28px 40px 18px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; gap:20px; flex-wrap:wrap; }}
  .logo {{ display:flex; align-items:center; gap:16px; }}
  .ring {{ width:48px; height:48px; border-radius:50%; border:2px solid var(--green); display:flex; align-items:center; justify-content:center; box-shadow:0 0 20px rgba(0,230,118,.3); font-size:1.3rem; animation:pulse 3s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100%{{box-shadow:0 0 20px rgba(0,230,118,.3)}} 50%{{box-shadow:0 0 40px rgba(0,230,118,.55)}} }}
  h1 {{ font-size:1.8rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }}
  h1 span {{ color:var(--green); }}
  .sub {{ font-family:'Share Tech Mono',monospace; font-size:.65rem; color:var(--muted); letter-spacing:.2em; margin-top:3px; }}
  .count {{ font-family:'Share Tech Mono',monospace; font-size:.85rem; color:var(--green); white-space:nowrap; }}

  .controls {{ position:relative; z-index:1; padding:12px 40px; border-bottom:1px solid var(--border); display:flex; flex-direction:column; gap:10px; }}
  .ctrl-row {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .search-wrap {{ flex:1; min-width:180px; max-width:340px; position:relative; }}
  .search-wrap input {{ width:100%; background:var(--panel); border:1px solid var(--border); color:var(--text); padding:8px 12px 8px 32px; font-family:'Share Tech Mono',monospace; font-size:.82rem; outline:none; transition:border-color .2s; }}
  .search-wrap input:focus {{ border-color:var(--green); }}
  .search-wrap input::placeholder {{ color:var(--muted); }}
  .si {{ position:absolute; left:9px; top:50%; transform:translateY(-50%); color:var(--muted); font-size:.9rem; }}
  select {{ background:var(--panel); border:1px solid var(--border); color:var(--text); padding:8px 12px; font-family:'Share Tech Mono',monospace; font-size:.78rem; outline:none; cursor:pointer; }}

  .filter-row {{ display:flex; align-items:center; gap:6px; flex-wrap:wrap; }}
  .filter-label {{ font-family:'Share Tech Mono',monospace; font-size:.65rem; color:var(--muted); letter-spacing:.1em; white-space:nowrap; }}
  .pill {{ background:var(--pill-bg); border:1px solid var(--border); color:var(--muted); padding:4px 10px; font-family:'Share Tech Mono',monospace; font-size:.65rem; cursor:pointer; transition:all .15s; white-space:nowrap; }}
  .pill:hover {{ border-color:var(--green-dim); color:var(--text); }}
  .pill.active {{ background:var(--green-dim); border-color:var(--green); color:var(--dark); font-weight:600; }}

  .no-results {{ display:none; position:relative; z-index:1; padding:60px 40px; text-align:center; font-family:'Share Tech Mono',monospace; color:var(--muted); }}
  .game-grid {{ position:relative; z-index:1; padding:18px 40px 60px; display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; }}
  .game-card {{ background:var(--panel); border:1px solid var(--border); overflow:hidden; transition:border-color .2s,transform .15s; }}
  .game-card:hover {{ border-color:var(--green-dim); transform:translateY(-3px); }}
  .img-wrap {{ width:100%; aspect-ratio:16/9; background:#0a180a; overflow:hidden; }}
  .img-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .no-img {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:2rem; color:var(--border); }}
  .card-body {{ padding:9px 11px; }}
  .card-title {{ font-size:.88rem; font-weight:600; line-height:1.3; margin-bottom:5px; }}
  .card-tags {{ display:flex; gap:4px; flex-wrap:wrap; margin-bottom:5px; }}
  .tag {{ font-family:'Share Tech Mono',monospace; font-size:.52rem; padding:2px 5px; }}
  .tag-gen {{ background:#001a0d; border:1px solid var(--green-dim); color:var(--green); }}
  .tag-genre {{ background:#0a0a1a; border:1px solid #334; color:#8899bb; }}
  .card-meta {{ display:flex; align-items:center; justify-content:space-between; }}
  .card-price {{ font-family:'Share Tech Mono',monospace; font-size:.7rem; color:var(--green); }}
  .card-id {{ font-family:'Share Tech Mono',monospace; font-size:.55rem; color:var(--muted); margin-top:3px; }}

  @media(max-width:600px) {{
    header,.controls,.game-grid {{ padding-left:16px; padding-right:16px; }}
    h1 {{ font-size:1.4rem; }}
    .game-grid {{ grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); }}
  }}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="ring">🎮</div>
    <div>
      <h1>Xbox <span>Catalog</span></h1>
      <div class="sub">{cat_label.upper()} · {market}</div>
    </div>
  </div>
  <div class="count"><span id="vis-count">{total}</span> / {total} giochi</div>
</header>

<div class="controls">
  <div class="ctrl-row">
    <div class="search-wrap">
      <span class="si">⌕</span>
      <input type="text" id="search" placeholder="Cerca titolo..." oninput="applyFilters()">
    </div>
    <select id="sort" onchange="applyFilters()">
      <option value="name-asc">Nome A→Z</option>
      <option value="name-desc">Nome Z→A</option>
      <option value="price-asc">Prezzo ↑</option>
      <option value="price-desc">Prezzo ↓</option>
      <option value="gen">Console (gen)</option>
    </select>
  </div>

  <div class="filter-row">
    <span class="filter-label">CONSOLE:</span>
    {gen_pills}  </div>

  <div class="filter-row">
    <span class="filter-label">GENERE:</span>
    {genre_pills}  </div>
</div>

<div class="no-results" id="no-results">// nessun risultato</div>
<div class="game-grid" id="grid">{cards}</div>

<script>
var activeGenre = 'all';
var activeGen = 'all';

function setFilter(type, value, btn) {{
  if (type === 'genre') {{
    activeGenre = value;
    document.querySelectorAll('[data-filter-genre]').forEach(b => b.classList.remove('active'));
  }} else {{
    activeGen = value;
    document.querySelectorAll('[data-filter-gen]').forEach(b => b.classList.remove('active'));
  }}
  btn.classList.add('active');
  applyFilters();
}}

function applyFilters() {{
  var q = document.getElementById('search').value.toLowerCase().trim();
  var sort = document.getElementById('sort').value;
  var cards = [...document.querySelectorAll('.game-card')];
  var total = {total};

  cards.forEach(function(c) {{
    var matchQ = !q || c.dataset.title.includes(q);
    var matchGenre = activeGenre === 'all' || c.dataset.genre === activeGenre;
    var matchGen = activeGen === 'all' || c.dataset.gen === activeGen;
    c.style.display = (matchQ && matchGenre && matchGen) ? '' : 'none';
  }});

  var vis = cards.filter(c => c.style.display !== 'none');

  var grid = document.getElementById('grid');
  if (sort === 'name-asc') {{
    vis.sort((a,b) => a.dataset.title.localeCompare(b.dataset.title));
  }} else if (sort === 'name-desc') {{
    vis.sort((a,b) => b.dataset.title.localeCompare(a.dataset.title));
  }} else if (sort === 'price-asc') {{
    vis.sort((a,b) => parseFloat(a.dataset.priceNum||0) - parseFloat(b.dataset.priceNum||0));
  }} else if (sort === 'price-desc') {{
    vis.sort((a,b) => parseFloat(b.dataset.priceNum||0) - parseFloat(a.dataset.priceNum||0));
  }} else if (sort === 'gen') {{
    vis.sort((a,b) => a.dataset.gen.localeCompare(b.dataset.gen) || a.dataset.title.localeCompare(b.dataset.title));
  }}
  vis.forEach(c => grid.appendChild(c));

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
    parser.add_argument("--ids", metavar="FILE",
                        help="File BigId (JSON o JS). Default: auto-detect")
    parser.add_argument("--market", default="IT",
                        help="Codice mercato (default: IT)")
    parser.add_argument("--lang", default="it-it",
                        help="Locale API (default: it-it)")
    parser.add_argument("--category", choices=list(CONSOLE_GENS.keys()),
                        help="Filtro generazione console. Se omesso: menu interattivo")
    parser.add_argument("--filter-market", action="store_true",
                        help="Escludi giochi con <exc>MARKET nella URL")
    parser.add_argument("--out", default="xbox_og_games.html",
                        help="File HTML di output (default: xbox_og_games.html)")
    parser.add_argument("--batch", type=int, default=20,
                        help="BigId per richiesta API (default: 20)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Secondi tra batch (default: 0.3)")
    parser.add_argument("--resume", action="store_true",
                        help="Riprendi da failed_ids.json")
    args = parser.parse_args()

    # FEATURE A — Selezione categoria
    if args.category:
        category_code = args.category
    else:
        category_code = select_category_interactive()

    # GAP 1 — Caricamento BigId
    if args.resume and Path("failed_ids.json").exists():
        failed_data = json.loads(Path("failed_ids.json").read_text())
        ids = failed_data if isinstance(failed_data, list) else failed_data.get("ids", [])
        url_map: dict[str, str] = {}
        print(f"\nResume: {len(ids)} ID da ritentare")
    else:
        print()
        ids, url_map = load_ids(args.ids)

    # GAP 5 — Filtro mercato
    if args.filter_market and url_map:
        ids = filter_by_market(ids, url_map, args.market)

    cat_label = CATEGORY_LABELS.get(category_code, category_code)
    print(f"\nAvvio scraping: {len(ids)} giochi · categoria={cat_label} · batch={args.batch} · delay={args.delay}s\n")

    # GAP 4 — Scraping con retry + FEATURE A filtro generazione
    games, failed = scrape(ids, args.market, args.lang, args.batch, args.delay, category_code)

    # Salva ID falliti
    if failed:
        Path("failed_ids.json").write_text(json.dumps(failed, indent=2))
        print(f"\n⚠ {len(failed)} ID falliti salvati in failed_ids.json")
    elif Path("failed_ids.json").exists():
        Path("failed_ids.json").unlink()

    # FEATURE C — Genera HTML con filtri avanzati
    output = build_html(games, args.market, category_code)
    Path(args.out).write_text(output, encoding="utf-8")

    print(f"\n✅ Completato: {len(games)} giochi, {len(failed)} errori")
    print(f"   File generato: {args.out}")
    print(f"   Apri con: open {args.out}")


if __name__ == "__main__":
    main()
