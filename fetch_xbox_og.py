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
import re
import argparse
import sys
import logging
from pathlib import Path

from html_builder import build_html

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
) -> tuple[list[str], dict[str, list[str]]]:
    """
    Carica i BigId per la categoria selezionata.
    Ritorna (lista_id, mappa_id→lista_label_categorie).
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
        return ids, {gid: ["unknown"] for gid in ids}

    data = load_bigids_file(p)
    categories: dict[str, dict] = data.get("categories", {})

    if category_key == "all" or not categories:
        # Usa la lista piatta globale
        ids = data.get("ids", [])
        # Costruisci mappa id→categoria dalla struttura categories
        id_to_cat: dict[str, list[str]] = {}
        for key, cat in categories.items():
            cat_ids = cat["ids"] if isinstance(cat, dict) else cat
            label = _cat_label(key, categories)
            for gid in cat_ids:
                id_to_cat.setdefault(gid, [])
                if label not in id_to_cat[gid]:
                    id_to_cat[gid].append(label)
        return list(dict.fromkeys(ids)), id_to_cat

    if category_key not in categories:
        available = ", ".join(categories.keys())
        sys.exit(f"Categoria '{category_key}' non trovata. Disponibili: {available}")

    cat_data = categories[category_key]
    ids = cat_data["ids"] if isinstance(cat_data, dict) else cat_data
    ids = list(dict.fromkeys(ids))
    label = _cat_label(category_key, categories)
    id_to_cat = {gid: [label] for gid in ids}

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
    match = re.search(r'biUrls\s*=\s*', content)
    if not match:
        return [], {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(content[match.end():].lstrip())
    except json.JSONDecodeError:
        return [], {}
    urls: dict[str, str] = obj["items"]["urls"]
    return list(urls.keys()), urls


def load_market_url_map(ids_file: str | None) -> dict[str, str]:
    """Carica la mappa BigId->URL usata per il filtro <exc>MARKET."""
    candidates: list[Path] = []
    if ids_file:
        candidates.append(Path(ids_file))
    candidates.append(Path("xcat-bi-urls2.json"))

    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        _, url_map = _parse_js_biurls(path)
        if url_map:
            log.info("Mappa URL mercato caricata da %s (%d ID)", path, len(url_map))
            return url_map
    return {}


def validate_args(args: argparse.Namespace) -> None:
    """Valida i parametri CLI prima di avviare scraping o I/O distruttivo."""
    if args.ids and not Path(args.ids).exists():
        sys.exit(f"Errore: file BigId non trovato: {args.ids}")
    if not 1 <= args.batch <= 50:
        sys.exit("Errore: --batch deve essere compreso tra 1 e 50")
    if args.delay < 0:
        sys.exit("Errore: --delay deve essere maggiore o uguale a 0")
    if not 1 <= args.workers <= 10:
        sys.exit("Errore: --workers deve essere compreso tra 1 e 10")


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


def parse_product(p: dict, game_id: str, source_category: str, source_categories: list[str] | None = None) -> dict:
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
    price_status = "unknown"
    try:
        avs = p.get("DisplaySkuAvailabilities", [{}])[0].get("Availabilities", [])
        for av in avs:
            lp = av.get("OrderManagementData", {}).get("Price", {}).get("ListPrice", 0)
            price = av.get("OrderManagementData", {}).get("Price", {})
            if "ListPrice" not in price:
                continue
            if lp > 0:
                cc = price.get("CurrencyCode", "EUR")
                price_str = f"{lp:.2f} {cc}"
                price_num = float(lp)
                price_status = "paid"
                break
            if lp == 0:
                price_str = "Gratis"
                price_num = 0.0
                price_status = "free"
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
        "price_status": price_status,
        "source_category": source_category,
        "source_categories": source_categories or ([source_category] if source_category else []),
        "genre": genre,
        "url": store_url,
    }


def _process_batch_result(
    batch: list[str],
    products: list[dict],
    id_to_cat: dict[str, list[str]],
    seen_ids: set[str],
) -> tuple[list[dict], list[str]]:
    """Processa i risultati di un batch: parse, deduplica, traccia missing."""
    new_games: list[dict] = []
    batch_missing: list[str] = []
    returned_ids: set[str] = set()
    for p in products:
        pid = p.get("ProductId", "")
        returned_ids.add(pid)
        source_categories = id_to_cat.get(pid, id_to_cat.get(batch[0], []))
        source_cat = source_categories[0] if source_categories else ""
        parsed = parse_product(p, pid, source_cat, source_categories)
        if parsed["id"] not in seen_ids:
            new_games.append(parsed)
            seen_ids.add(parsed["id"])
    for bid in batch:
        if bid not in returned_ids and bid not in seen_ids:
            batch_missing.append(bid)
    return new_games, batch_missing


def scrape(
    ids: list[str],
    id_to_cat: dict[str, list[str]],
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

    validate_args(args)

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
        id_to_cat: dict[str, list[str]] = {}
        log.info("Resume: %d ID da ritentare", len(ids))
    else:
        ids, id_to_cat = load_ids(args.ids, category_key)

    if args.filter_market:
        url_map = load_market_url_map(args.ids)
        if url_map:
            if len(url_map) < len(ids):
                log.warning("Filtro mercato parziale: mappa URL copre %d/%d ID", len(url_map), len(ids))
            ids = filter_by_market(ids, url_map, args.market)
        else:
            log.warning("Filtro mercato richiesto ma nessuna mappa URL <exc> disponibile")

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
