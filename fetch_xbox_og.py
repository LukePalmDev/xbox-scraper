"""
Xbox OG Scraper — recupera metadati giochi dalla Display Catalog API.

Uso:
  python3 fetch_xbox_og.py                            # menu interattivo
  python3 fetch_xbox_og.py --category xboxOG          # solo Xbox Original (OG)
  python3 fetch_xbox_og.py --category xbox360         # solo Xbox 360
  python3 fetch_xbox_og.py --category fullXboxOne     # catalogo Xbox One completo
  python3 fetch_xbox_og.py --category all             # tutti gli ID unici (~4277)
  python3 fetch_xbox_og.py --filter-market            # escludi giochi non disponibili in IT
  python3 fetch_xbox_og.py --out catalog.html         # nome output custom
  python3 fetch_xbox_og.py --resume                   # riprendi da failed_ids.json
  python3 fetch_xbox_og.py --batch 20 --delay 0.5    # parametri rete

Richiede Python 3 standard — nessuna libreria esterna necessaria.
"""

import json
import time
import html as html_escape
import re
import argparse
import sys
import logging
from pathlib import Path

from scraper_utils import (
    create_ssl_context,
    HEADERS_JSON,
    fetch_json,
    generate_ms_cv,
)

log = logging.getLogger(__name__)

CATALOG_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    "?bigIds={ids}&market={market}&languages={lang}&MS-CV={mscv}"
)

# Regex compilata per filtro mercato
_EXC_RE = re.compile(r'<exc>([^"]+)')


# ---------------------------------------------------------------------------
# Caricamento BigId da bigids.json (struttura con categorie)
# ---------------------------------------------------------------------------

def load_bigids_file(path: Path) -> dict:
    """
    Carica bigids.json. Supporta sia il vecchio formato flat
    che il nuovo formato con categories.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        # Formato legacy: lista piana
        return {"ids": data, "categories": {}}
    return data


def load_ids(
    ids_file: str | None,
    category_key: str,
) -> tuple[list[str], dict[str, str]]:
    """
    Carica i BigId per la categoria selezionata.
    Ritorna (lista_id, mappa_id→label_categoria).
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

    log.info("Caricamento BigId da: %s", p)

    # Formato JS (biUrls legacy)
    content_start = p.read_text(encoding="utf-8").strip()
    if not content_start.startswith("{") and not content_start.startswith("["):
        ids, _ = _parse_js_biurls(p)
        return ids, {gid: "unknown" for gid in ids}

    data = load_bigids_file(p)
    categories: dict[str, dict] = data.get("categories", {})

    if category_key == "all" or not categories:
        # Usa la lista piatta globale
        ids = data.get("ids", [])
        # Costruisci mappa id→categoria dalla struttura categories
        id_to_cat: dict[str, str] = {}
        for key, cat in categories.items():
            cat_ids = cat["ids"] if isinstance(cat, dict) else cat
            label = _cat_label(key, categories)
            for gid in cat_ids:
                if gid not in id_to_cat:
                    id_to_cat[gid] = label
        return list(dict.fromkeys(ids)), id_to_cat

    if category_key not in categories:
        available = ", ".join(categories.keys())
        sys.exit(f"Categoria '{category_key}' non trovata. Disponibili: {available}")

    cat_data = categories[category_key]
    ids = cat_data["ids"] if isinstance(cat_data, dict) else cat_data
    ids = list(dict.fromkeys(ids))
    label = _cat_label(category_key, categories)
    id_to_cat = {gid: label for gid in ids}

    log.info("  -> %d BigId unici [%s]", len(ids), label)
    return ids, id_to_cat


def _cat_label(key: str, categories: dict) -> str:
    cat = categories.get(key, {})
    if isinstance(cat, dict) and "label" in cat:
        return cat["label"]
    return key


def _parse_js_biurls(path: Path) -> tuple[list[str], dict[str, str]]:
    """Parsing legacy del file JS con biUrls = { ... }."""
    content = path.read_text(encoding="utf-8")
    match = re.search(r'biUrls\s*=\s*(\{.*\})', content, re.DOTALL)
    if not match:
        return [], {}
    obj = json.loads(match.group(1))
    urls: dict[str, str] = obj["items"]["urls"]
    return list(urls.keys()), urls


# ---------------------------------------------------------------------------
# FEATURE A — Menu interattivo da terminale
# ---------------------------------------------------------------------------

def select_category_interactive(bigids_path: str | None = None) -> str:
    """
    Mostra un menu con le categorie disponibili in bigids.json
    e ritorna la chiave scelta.
    """
    # Carica le categorie dal file per mostrare i conteggi reali
    categories: dict[str, dict] = {}
    try:
        p = Path(bigids_path) if bigids_path else next(
            (Path(c) for c in ["bigids.json", "xcat-bi-urls2.json"] if Path(c).exists()),
            None
        )
        if p and p.exists():
            data = load_bigids_file(p)
            categories = data.get("categories", {})
    except Exception:
        pass

    print()
    print("+" + "=" * 46 + "+")
    print("|        XBOX SCRAPER — Selezione categoria    |")
    print("+" + "=" * 46 + "+")
    print()

    options = [("all", "Tutti i giochi", sum(
        len(c["ids"] if isinstance(c, dict) else c)
        for c in categories.values()
    ) if categories else 0)]

    for key, cat in categories.items():
        label = cat["label"] if isinstance(cat, dict) else key
        count = cat["count"] if isinstance(cat, dict) and "count" in cat else len(
            cat["ids"] if isinstance(cat, dict) else cat
        )
        options.append((key, label, count))

    for i, (key, label, count) in enumerate(options, 1):
        count_str = f"({count} giochi)" if count else ""
        print(f"  [{i}] {label} {count_str}")

    print()
    while True:
        try:
            raw = input(f"  Scelta [1-{len(options)}] (default 1): ").strip()
            if raw == "":
                chosen_key = options[0][0]
                break
            n = int(raw)
            if 1 <= n <= len(options):
                chosen_key = options[n - 1][0]
                break
        except (ValueError, EOFError):
            pass
        print(f"  Inserisci un numero tra 1 e {len(options)}.")

    chosen_label = next(label for key, label, _ in options if key == chosen_key)
    print(f"\n  -> Selezionato: {chosen_label}")
    return chosen_key


# ---------------------------------------------------------------------------
# GAP 5 — Filtro mercato (da xcat-bi-urls2.json)
# ---------------------------------------------------------------------------

def filter_by_market(ids: list[str], url_map: dict[str, str], market: str) -> list[str]:
    if not url_map:
        return ids
    market_upper = market.upper()
    filtered, excluded = [], 0
    for game_id in ids:
        url = url_map.get(game_id, "")
        exc_match = _EXC_RE.search(url)
        if exc_match:
            excl = [m.strip().upper() for m in exc_match.group(1).split(",")]
            if any(market_upper in m for m in excl):
                excluded += 1
                continue
        filtered.append(game_id)
    log.info("  -> Filtro mercato %s: %d esclusi, %d rimasti", market, excluded, len(filtered))
    return filtered


# ---------------------------------------------------------------------------
# Fetch con retry e backoff esponenziale
# ---------------------------------------------------------------------------

def fetch_batch(
    ids: list[str], market: str, lang: str,
    ssl_ctx=None, ms_cv: str = "",
    max_retries: int = 3,
) -> list[dict]:
    """Scarica un batch di prodotti dalla Display Catalog API."""
    url = CATALOG_URL.format(
        ids=",".join(ids), market=market, lang=lang,
        mscv=ms_cv or generate_ms_cv(),
    )
    data = fetch_json(url, ssl_ctx=ssl_ctx, max_retries=max_retries, timeout=15)
    return data.get("Products", [])


def parse_product(p: dict, game_id: str, source_category: str) -> dict:
    """Estrae i campi utili da un Product della Display Catalog API."""
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

    # Prezzo con valore numerico per sort
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

    # Genere da Categories[] API
    props = p.get("Properties", {})
    categories: list[str] = props.get("Categories") or []
    genre = categories[0] if categories else (props.get("Category") or "")

    # Link allo store Xbox
    pid = p.get("ProductId", game_id)
    store_url = f"https://www.xbox.com/games/store/-/{pid}"

    return {
        "id": pid,
        "title": title,
        "img": img_url,
        "price": price_str,
        "price_num": price_num,
        "source_category": source_category,
        "genre": genre,
        "url": store_url,
    }


def _process_batch_result(
    batch: list[str],
    products: list[dict],
    id_to_cat: dict[str, str],
    seen_ids: set[str],
) -> tuple[list[dict], list[str]]:
    """Processa i risultati di un batch: parse, deduplica, traccia missing."""
    new_games: list[dict] = []
    batch_missing: list[str] = []
    returned_ids: set[str] = set()
    for p in products:
        pid = p.get("ProductId", "")
        returned_ids.add(pid)
        source_cat = id_to_cat.get(pid, id_to_cat.get(batch[0], ""))
        parsed = parse_product(p, pid, source_cat)
        if parsed["id"] not in seen_ids:
            new_games.append(parsed)
            seen_ids.add(parsed["id"])
    for bid in batch:
        if bid not in returned_ids and bid not in seen_ids:
            batch_missing.append(bid)
    return new_games, batch_missing


def scrape(
    ids: list[str],
    id_to_cat: dict[str, str],
    market: str,
    lang: str,
    batch_size: int,
    delay: float,
    ssl_ctx=None,
    workers: int = 1,
) -> tuple[list[dict], list[str], list[str]]:
    """
    Scraping con retry e tracciamento errori per singolo ID.
    workers > 1 abilita fetching concorrente.

    Ritorna (games, failed_ids, missing_ids) dove:
    - failed_ids: batch falliti per errore di rete
    - missing_ids: ID richiesti ma non restituiti dall'API (delisted/invalidi)
    """
    games: list[dict] = []
    failed: list[str] = []
    missing: list[str] = []
    seen_ids: set[str] = set()
    ms_cv = generate_ms_cv()

    # Prepara tutti i batch
    batches = [ids[i:i + batch_size] for i in range(0, len(ids), batch_size)]
    total_batches = len(batches)

    if workers <= 1:
        # Modalita sequenziale (default)
        for batch_num, batch in enumerate(batches, 1):
            log.info("[%03d/%d] batch %d-%d ...", batch_num, total_batches,
                     (batch_num - 1) * batch_size + 1, (batch_num - 1) * batch_size + len(batch))
            try:
                products = fetch_batch(batch, market, lang, ssl_ctx=ssl_ctx, ms_cv=ms_cv)
                new_games, batch_missing = _process_batch_result(batch, products, id_to_cat, seen_ids)
                games.extend(new_games)
                missing.extend(batch_missing)
                log.info("  -> %d ricevuti, %d aggiunti", len(products), len(new_games))
            except Exception as e:
                failed.extend(batch)
                log.error("  -> ERRORE: %s", e)
            if batch_num < total_batches:
                time.sleep(delay)
    else:
        # Modalita concorrente
        from concurrent.futures import ThreadPoolExecutor, as_completed
        log.info("Fetching concorrente con %d workers", workers)

        def _fetch_one(batch_idx: int, batch: list[str]):
            # Sfalsamento SOLO per il primo wave (0..workers-1) per non saturare l'API.
            # batch_idx % workers mantiene il ritardo fisso e non crescente.
            if batch_idx < workers:
                time.sleep(delay * batch_idx / workers)
            return batch, fetch_batch(batch, market, lang, ssl_ctx=ssl_ctx, ms_cv=ms_cv)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_fetch_one, i, batch): i
                for i, batch in enumerate(batches)
            }
            completed = 0
            for future in as_completed(futures):
                completed += 1
                try:
                    batch, products = future.result()
                    new_games, batch_missing = _process_batch_result(batch, products, id_to_cat, seen_ids)
                    games.extend(new_games)
                    missing.extend(batch_missing)
                    log.info("[%03d/%d] %d ricevuti, %d aggiunti",
                             completed, total_batches, len(products), len(new_games))
                except Exception as e:
                    batch_idx = futures[future]
                    failed.extend(batches[batch_idx])
                    log.error("[%03d/%d] ERRORE: %s", completed, total_batches, e)

    return games, failed, missing


# ---------------------------------------------------------------------------
# FEATURE C — HTML Builder con filtri avanzati
# ---------------------------------------------------------------------------

def slugify(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def build_html(games: list[dict], market: str, category_label: str) -> str:
    # Raccogli valori unici per filtri
    all_source_cats = sorted({g["source_category"] for g in games if g["source_category"]})
    all_genres = sorted({g["genre"] for g in games if g["genre"]})

    def make_pills(items: list[str], filter_type: str, all_label: str) -> str:
        pills = f'<button class="pill active" data-filter-{filter_type}="all">{all_label}</button>\n'
        for item in items:
            slug = slugify(item)
            esc = html_escape.escape(item)
            pills += f'    <button class="pill" data-filter-{filter_type}="{slug}">{esc}</button>\n'
        return pills

    cat_pills = make_pills(all_source_cats, "cat", "Tutte le console")
    genre_pills = make_pills(all_genres, "genre", "Tutti i generi")

    # Statistiche per dashboard
    priced_games = [g for g in games if g["price_num"] > 0]
    avg_price = sum(g["price_num"] for g in priced_games) / len(priced_games) if priced_games else 0
    free_count = sum(1 for g in games if g["price_num"] == 0)
    cat_counts = {}
    for g in games:
        cat = g["source_category"] or "Sconosciuto"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    stats_items = "".join(
        f'<div class="stat-item"><div class="stat-num">{count}</div><div class="stat-lbl">{html_escape.escape(cat)}</div></div>'
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1])
    )
    stats_html = f"""
    <div class="stats-bar">
      <div class="stat-item"><div class="stat-num">{len(games)}</div><div class="stat-lbl">Totale</div></div>
      <div class="stat-item"><div class="stat-num">{free_count}</div><div class="stat-lbl">Gratis</div></div>
      <div class="stat-item"><div class="stat-num">{avg_price:.2f}</div><div class="stat-lbl">Prezzo medio</div></div>
      {stats_items}
    </div>"""

    cards = ""
    for g in sorted(games, key=lambda x: x["title"].lower()):
        t = html_escape.escape(g["title"])
        store_url = html_escape.escape(g.get("url", ""))
        img_tag = (
            f'<img src="{g["img"]}" alt="{t}" loading="lazy">'
            if g["img"] else '<div class="no-img">&#x1f3ae;</div>'
        )
        price_display = g["price"] or "—"
        cat_slug = slugify(g["source_category"]) if g["source_category"] else "unknown"
        genre_slug = slugify(g["genre"]) if g["genre"] else "unknown"
        cat_label_esc = html_escape.escape(g["source_category"] or "—")
        genre_label_esc = html_escape.escape(g["genre"]) if g["genre"] else "—"

        title_html = f'<a href="{store_url}" target="_blank" rel="noopener" class="card-link">{t}</a>' if store_url else t

        cards += f"""
        <div class="game-card" role="listitem"
             data-title="{t.lower()}"
             data-cat="{cat_slug}"
             data-genre="{genre_slug}"
             data-price-num="{g['price_num']:.2f}">
          <div class="img-wrap">{img_tag}</div>
          <div class="card-body">
            <div class="card-title">{title_html}</div>
            <div class="card-tags">
              <span class="tag tag-cat">{cat_label_esc}</span>
              <span class="tag tag-genre">{genre_label_esc}</span>
            </div>
            <div class="card-meta">
              <span class="card-price">{price_display}</span>
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
<title>Xbox — {total} giochi ({category_label} · {market})</title>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
  :root {{ --green:#00e676; --green-dim:#00b357; --dark:#060a0e; --panel:#0c1318; --border:#1a2a1a; --text:#c8e6c9; --muted:#7a9a7a; --pill-bg:#0f1f0f; }}
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
  .pill:focus-visible {{ outline:2px solid var(--green); outline-offset:2px; }}
  .no-results {{ display:none; position:relative; z-index:1; padding:60px 40px; text-align:center; font-family:'Share Tech Mono',monospace; color:var(--muted); }}
  .game-grid {{ position:relative; z-index:1; padding:18px 40px 60px; display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; }}
  .game-card {{ background:var(--panel); border:1px solid var(--border); overflow:hidden; transition:border-color .2s,transform .15s; content-visibility:auto; contain-intrinsic-size:200px 280px; }}
  .game-card:hover {{ border-color:var(--green-dim); transform:translateY(-3px); }}
  .img-wrap {{ width:100%; aspect-ratio:16/9; background:#0a180a; overflow:hidden; }}
  .img-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .no-img {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:2rem; color:var(--border); }}
  .card-body {{ padding:9px 11px; }}
  .card-title {{ font-size:.88rem; font-weight:600; line-height:1.3; margin-bottom:5px; }}
  .card-tags {{ display:flex; gap:4px; flex-wrap:wrap; margin-bottom:5px; }}
  .tag {{ font-family:'Share Tech Mono',monospace; font-size:.52rem; padding:2px 5px; }}
  .tag-cat {{ background:#001a0d; border:1px solid var(--green-dim); color:var(--green); }}
  .tag-genre {{ background:#0a0a1a; border:1px solid #334; color:#8899bb; }}
  .card-meta {{ display:flex; align-items:center; justify-content:space-between; }}
  .card-price {{ font-family:'Share Tech Mono',monospace; font-size:.7rem; color:var(--green); }}
  .card-link {{ color:var(--text); text-decoration:none; }}
  .card-link:hover {{ color:var(--green); }}
  .card-id {{ font-family:'Share Tech Mono',monospace; font-size:.55rem; color:var(--muted); margin-top:3px; }}
  .stats-bar {{ position:relative; z-index:1; display:flex; gap:2px; padding:10px 40px; border-bottom:1px solid var(--border); flex-wrap:wrap; }}
  .stat-item {{ background:var(--panel); border:1px solid var(--border); padding:8px 14px; text-align:center; min-width:80px; }}
  .stat-num {{ font-family:'Share Tech Mono',monospace; font-size:1.1rem; color:var(--green); font-weight:700; }}
  .stat-lbl {{ font-family:'Share Tech Mono',monospace; font-size:.55rem; color:var(--muted); letter-spacing:.05em; margin-top:2px; }}
  @media(max-width:600px) {{
    .stats-bar {{ padding-left:16px; padding-right:16px; }}
    header,.controls,.game-grid {{ padding-left:16px; padding-right:16px; }}
    h1 {{ font-size:1.4rem; }}
    .game-grid {{ grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); }}
  }}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="ring">&#x1f3ae;</div>
    <div>
      <h1>Xbox <span>Catalog</span></h1>
      <div class="sub">{html_escape.escape(category_label).upper()} &middot; {market}</div>
    </div>
  </div>
  <div class="count"><span id="vis-count">{total}</span> / {total} giochi</div>
</header>

{stats_html}

<div class="controls" role="search">
  <div class="ctrl-row">
    <div class="search-wrap">
      <span class="si">&#x2315;</span>
      <input type="text" id="search" placeholder="Cerca titolo..." aria-label="Cerca gioco per titolo">
    </div>
    <select id="sort" aria-label="Ordina per">
      <option value="name-asc">Nome A-Z</option>
      <option value="name-desc">Nome Z-A</option>
      <option value="price-asc">Prezzo crescente</option>
      <option value="price-desc">Prezzo decrescente</option>
      <option value="cat">Console</option>
    </select>
  </div>
  <div class="filter-row" role="group" aria-label="Filtra per console">
    <span class="filter-label">CONSOLE:</span>
    {cat_pills}  </div>
  <div class="filter-row" role="group" aria-label="Filtra per genere">
    <span class="filter-label">GENERE:</span>
    {genre_pills}  </div>
</div>

<div class="no-results" id="no-results" aria-live="polite">// nessun risultato</div>
<div class="game-grid" id="grid" role="list" aria-label="Catalogo giochi">{cards}</div>

<script>
var activeCat = 'all';
var activeGenre = 'all';
var _debounceTimer;

// Event delegation — niente inline onclick
document.querySelector('.controls').addEventListener('click', function(e) {{
  var pill = e.target.closest('.pill');
  if (!pill) return;
  var type = pill.hasAttribute('data-filter-cat') ? 'cat' : 'genre';
  var value = pill.getAttribute('data-filter-' + type);
  if (type === 'cat') {{
    activeCat = value;
    document.querySelectorAll('[data-filter-cat]').forEach(function(b) {{ b.classList.remove('active'); }});
  }} else {{
    activeGenre = value;
    document.querySelectorAll('[data-filter-genre]').forEach(function(b) {{ b.classList.remove('active'); }});
  }}
  pill.classList.add('active');
  applyFilters();
}});

document.getElementById('search').addEventListener('input', function() {{
  clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(applyFilters, 200);
}});

document.getElementById('sort').addEventListener('change', applyFilters);

function applyFilters() {{
  var q = document.getElementById('search').value.toLowerCase().trim();
  var sort = document.getElementById('sort').value;
  var cards = [].slice.call(document.querySelectorAll('.game-card'));

  cards.forEach(function(c) {{
    var ok = (!q || c.dataset.title.indexOf(q) !== -1)
          && (activeCat === 'all' || c.dataset.cat === activeCat)
          && (activeGenre === 'all' || c.dataset.genre === activeGenre);
    c.style.display = ok ? '' : 'none';
  }});

  var vis = cards.filter(function(c) {{ return c.style.display !== 'none'; }});
  var grid = document.getElementById('grid');

  if (sort === 'name-asc')        vis.sort(function(a,b) {{ return a.dataset.title.localeCompare(b.dataset.title); }});
  else if (sort === 'name-desc')  vis.sort(function(a,b) {{ return b.dataset.title.localeCompare(a.dataset.title); }});
  else if (sort === 'price-asc')  vis.sort(function(a,b) {{ return parseFloat(a.dataset.priceNum||0) - parseFloat(b.dataset.priceNum||0); }});
  else if (sort === 'price-desc') vis.sort(function(a,b) {{ return parseFloat(b.dataset.priceNum||0) - parseFloat(a.dataset.priceNum||0); }});
  else if (sort === 'cat')        vis.sort(function(a,b) {{ return a.dataset.cat.localeCompare(b.dataset.cat) || a.dataset.title.localeCompare(b.dataset.title); }});

  vis.forEach(function(c) {{ grid.appendChild(c); }});
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
                        help="File BigId (bigids.json). Default: auto-detect")
    parser.add_argument("--market", default="IT",
                        help="Codice mercato (default: IT)")
    parser.add_argument("--lang", default="it-it",
                        help="Locale API (default: it-it)")
    parser.add_argument("--category", metavar="KEY",
                        help="Chiave categoria (es: xboxOG, xbox360, fullXboxOne, all). "
                             "Se omesso: menu interattivo")
    parser.add_argument("--filter-market", action="store_true",
                        help="Escludi giochi con <exc>MARKET nella URL (richiede url_map)")
    parser.add_argument("--out", default="index.html",
                        help="File HTML di output (default: index.html)")
    parser.add_argument("--json-out", metavar="FILE",
                        help="File JSON con i dati dei giochi (default: games.json)")
    parser.add_argument("--batch", type=int, default=50,
                        help="BigId per richiesta API (default: 50, max: 50)")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="Secondi tra batch (default: 0.3)")
    parser.add_argument("--resume", action="store_true",
                        help="Riprendi da failed_ids.json")
    parser.add_argument("--workers", type=int, default=1,
                        help="Worker concorrenti per fetch (default: 1, max consigliato: 3)")
    parser.add_argument("--no-verify-ssl", action="store_true",
                        help="Disabilita verifica certificati SSL")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Output dettagliato")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    ssl_ctx = create_ssl_context(verify=not args.no_verify_ssl)

    # FEATURE A — Selezione categoria (non-interattivo in CI)
    if args.category:
        category_key = args.category
    elif not sys.stdin.isatty():
        category_key = "all"
        log.info("Modalita non-interattiva: uso --category all")
    else:
        category_key = select_category_interactive(args.ids)

    # Caricamento BigId
    if args.resume and Path("failed_ids.json").exists():
        failed_data = json.loads(Path("failed_ids.json").read_text())
        ids = failed_data if isinstance(failed_data, list) else failed_data.get("ids", [])
        id_to_cat: dict[str, str] = {}
        log.info("Resume: %d ID da ritentare", len(ids))
    else:
        ids, id_to_cat = load_ids(args.ids, category_key)

    # Ottieni la label della categoria per il titolo HTML
    cat_label = category_key
    try:
        p = Path(args.ids) if args.ids else next(
            (Path(c) for c in ["bigids.json"] if Path(c).exists()), None
        )
        if p and p.exists():
            data = load_bigids_file(p)
            cats = data.get("categories", {})
            if category_key in cats:
                c = cats[category_key]
                cat_label = c["label"] if isinstance(c, dict) else category_key
            elif category_key == "all":
                cat_label = "Tutti i giochi"
    except Exception:
        pass

    log.info("Avvio scraping: %d giochi - [%s] - batch=%d - delay=%.1fs",
             len(ids), cat_label, args.batch, args.delay)

    # Scraping con retry
    games, failed, missing = scrape(
        ids, id_to_cat, args.market, args.lang, args.batch, args.delay,
        ssl_ctx=ssl_ctx, workers=args.workers,
    )

    if failed:
        Path("failed_ids.json").write_text(json.dumps(failed, indent=2))
        log.warning("%d ID falliti salvati in failed_ids.json", len(failed))
    elif Path("failed_ids.json").exists():
        Path("failed_ids.json").unlink()

    if missing:
        log.info("%d ID non restituiti dall'API (delisted/invalidi)", len(missing))

    # Output JSON
    json_out = args.json_out or "games.json"
    Path(json_out).write_text(
        json.dumps(games, indent=None, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Dati JSON salvati in: %s", json_out)

    # Output HTML
    output = build_html(games, args.market, cat_label)
    Path(args.out).write_text(output, encoding="utf-8")

    log.info("Completato: %d giochi, %d errori, %d missing", len(games), len(failed), len(missing))
    log.info("File generato: %s", args.out)


if __name__ == "__main__":
    main()
