"""
Xbox BigId Discovery — scarica il bundle JS da Xbox e estrae tutti i BigId.

Uso:
  python3 fetch_bigids.py                          # scraping automatico da Xbox
  python3 fetch_bigids.py --page URL               # URL pagina custom da cui cercare i bundle
  python3 fetch_bigids.py --bundle URL             # URL bundle JS diretto (skip discovery)
  python3 fetch_bigids.py --input FILE             # estrai BigId da file JS locale
  python3 fetch_bigids.py --out bigids.json        # file output (default: bigids.json)

Output: bigids.json con struttura:
  {
    "source": "...",
    "total": 1828,
    "categories": {
      "xboxOG":        { "label": "Xbox Original (OG)", "ids": [...] },
      "xbox360":       { "label": "Xbox 360",           "ids": [...] },
      "fullXboxOne":   { "label": "Xbox One",           "ids": [...] },
      "fpsBoostSeriesX": { "label": "FPS Boost Series X", "ids": [...] },
      "fpsBoostSeriesS": { "label": "FPS Boost Series S", "ids": [...] },
      "autoHDR":       { "label": "Auto HDR",           "ids": [...] },
      "startingat":    { "label": "Starting at...",     "ids": [...] }
    },
    "ids": [...tutti gli ID unici...]
  }

Richiede Python 3 standard — nessuna libreria esterna necessaria.
"""

import urllib.parse
import json
import re
import time
import argparse
import sys
import logging
from pathlib import Path

from scraper_utils import (
    create_ssl_context,
    HEADERS_HTML,
    fetch_with_retry,
)

log = logging.getLogger(__name__)

# Pagine Xbox candidate contenenti i bundle JS con biUrls
XBOX_PAGES = [
    "https://www.xbox.com/it-IT/games/backward-compatibility",
    "https://www.xbox.com/en-US/games/backward-compatibility",
    "https://www.xbox.com/it-IT/games",
    "https://www.xbox.com/en-US/games",
]

MS_STORE_LISTINGS = {
    "storeMostPopular": "https://www.microsoft.com/en-us/store/most-popular/games/xbox",
}


# ---------------------------------------------------------------------------
# GAP 2 — Discovery URL bundle JS dalla pagina Xbox
# ---------------------------------------------------------------------------

def find_script_urls(html: str, base_url: str) -> list[str]:
    """
    Estrae tutti gli URL <script src="..."> dalla pagina HTML.
    Risolve URL relativi in assoluti usando base_url come riferimento.
    """
    parsed_base = urllib.parse.urlparse(base_url)
    base_root = f"{parsed_base.scheme}://{parsed_base.netloc}"

    scripts = []
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE):
        if src.startswith("http"):
            scripts.append(src)
        elif src.startswith("//"):
            scripts.append("https:" + src)
        elif src.startswith("/"):
            scripts.append(base_root + src)
        else:
            scripts.append(base_url.rstrip("/") + "/" + src)

    # Deduplicazione mantenendo ordine
    seen: set[str] = set()
    unique: list[str] = []
    for s in scripts:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def discover_biurls_bundle(
    page_url: str, ssl_ctx=None,
) -> tuple[str | None, str | None]:
    """
    Cerca il bundle JS contenente 'biUrls' tra gli script della pagina.
    Ritorna (url_bundle, contenuto_js) o (None, None) se non trovato.
    """
    log.info("Fetching pagina: %s", page_url)
    try:
        html = fetch_with_retry(page_url, headers=HEADERS_HTML, ssl_ctx=ssl_ctx, timeout=20)
    except Exception as e:
        log.error("Impossibile scaricare la pagina: %s", e)
        return None, None

    script_urls = find_script_urls(html, page_url)
    log.info("Trovati %d script tag", len(script_urls))

    # Filtra: i bundle webpack/next hanno nomi tipo chunk-*.js, main-*.js, pages-*.js
    priority_patterns = [
        r'chunk',
        r'main',
        r'pages',
        r'catalog',
        r'game',
        r'backward',
        r'compat',
    ]

    def bundle_priority(url: str) -> int:
        u = url.lower()
        for i, pat in enumerate(priority_patterns):
            if re.search(pat, u):
                return i
        return len(priority_patterns)

    script_urls.sort(key=bundle_priority)

    for i, src_url in enumerate(script_urls, 1):
        # Salta file chiaramente irrilevanti (analytics, fonts, ecc.)
        if any(skip in src_url.lower() for skip in ["analytics", "gtm", "fontawesome", "polyfill"]):
            continue

        log.info("[%03d/%d] Checking: %s", i, len(script_urls), src_url[-80:])
        try:
            js_content = fetch_with_retry(src_url, headers=HEADERS_HTML, ssl_ctx=ssl_ctx, timeout=30)
            if "gameIdArrays" in js_content or "biUrls" in js_content:
                found = "gameIdArrays" if "gameIdArrays" in js_content else "biUrls"
                log.info("%s trovato! (%dKB)", found, len(js_content) // 1024)
                return src_url, js_content
            else:
                log.debug("Nessun match (%dKB)", len(js_content) // 1024)
        except Exception as e:
            log.debug("Errore: %s", e)

        time.sleep(0.1)

    return None, None


# ---------------------------------------------------------------------------
# GAP 3 — Estrazione BigId dal contenuto JS
# ---------------------------------------------------------------------------

# Label leggibili per ogni chiave gameIdArrays
CATEGORY_LABELS = {
    "xboxOG":          "Xbox Original (OG)",
    "xbox360":         "Xbox 360",
    "fullXboxOne":     "Xbox One",
    "fpsBoostSeriesX": "FPS Boost Series X",
    "fpsBoostSeriesS": "FPS Boost Series S",
    "autoHDR":         "Auto HDR",
    "startingat":      "Starting at...",
    "xboxone":         "Xbox One (legacy)",
    "storeMostPopular": "Microsoft Store - Most popular",
    "storeTopPaid":     "Microsoft Store - Top paid",
    "storeTopFree":     "Microsoft Store - Top free",
    "storeNew":         "Microsoft Store - New",
    "storeDeals":       "Microsoft Store - Deals",
}

BIGID_RE = re.compile(r'"([A-Z0-9]{9,12})"')
STORE_PRODUCT_ID_RE = re.compile(r'"productId"\s*:\s*"([A-Za-z0-9]{9,12})"')


def extract_game_id_arrays(js_content: str) -> dict[str, list[str]]:
    """
    Estrae gameIdArrays dal bundle JS Xbox.
    Formato sorgente:
      gameIdArrays["xboxOG"] = ["ID1","ID2",...];
      gameIdArrays["xbox360"] = ["ID1",...];
    Ritorna { "xboxOG": [...], "xbox360": [...], ... }
    """
    result: dict[str, list[str]] = {}
    pattern = re.compile(r'gameIdArrays\["(\w+)"\]\s*=\s*\[([^\]]*)\]')
    for m in pattern.finditer(js_content):
        key = m.group(1)
        ids = BIGID_RE.findall(m.group(2))
        if ids:  # ignora array vuoti (es. xboxone = [])
            result[key] = ids
    return result


def extract_biurls_object(js_content: str) -> dict[str, str]:
    """Fallback: estrae biUrls se gameIdArrays non è presente."""
    match = re.search(r'biUrls\s*[=:]\s*(\{[^;]{50,}\})', js_content, re.DOTALL)
    if match:
        try:
            raw_clean = re.sub(r',\s*([}\]])', r'\1', match.group(1))
            obj = json.loads(raw_clean)
            if "items" in obj and "urls" in obj.get("items", {}):
                return obj["items"]["urls"]
        except (json.JSONDecodeError, KeyError):
            pass
    result: dict[str, str] = {}
    for m in re.finditer(r'"([A-Z0-9]{9,12})"\s*:\s*"(https://www\.xbox\.com/[^"]*)"', js_content):
        result[m.group(1)] = m.group(2)
    return result


def load_from_local_file(path: str) -> dict[str, list[str]]:
    """
    Estrae gameIdArrays da un file JS locale.
    Se non presente, tenta il fallback biUrls mettendo tutti gli ID in 'unknown'.
    """
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    categories = extract_game_id_arrays(content)
    if categories:
        return categories
    # Fallback biUrls
    url_map = extract_biurls_object(content)
    if url_map:
        return {"unknown": list(url_map.keys())}
    return {}


def extract_store_product_ids(html: str) -> list[str]:
    """Estrae ProductId dalle pagine listing di microsoft.com/store."""
    ids = [m.upper() for m in STORE_PRODUCT_ID_RE.findall(html)]
    return list(dict.fromkeys(ids))


def discover_xbox_categories(pages: list[str], ssl_ctx=None) -> tuple[dict[str, list[str]], str]:
    """Scopre categorie BigId dai bundle Xbox."""
    for page_url in pages:
        log.info("--- Provo Xbox: %s", page_url)
        bundle_url, js_content = discover_biurls_bundle(page_url, ssl_ctx=ssl_ctx)
        if js_content:
            categories = extract_game_id_arrays(js_content)
            if not categories:
                url_map = extract_biurls_object(js_content)
                if url_map:
                    categories = {"unknown": list(url_map.keys())}
            if categories:
                return categories, bundle_url or page_url
            log.warning("Bundle trovato ma nessun BigId estratto, provo la prossima pagina...")
    return {}, ""


def discover_store_categories(
    ssl_ctx=None,
    max_pages: int = 20,
    delay: float = 0.1,
) -> tuple[dict[str, list[str]], str]:
    """
    Scopre ProductId dalle pagine Microsoft Store paginate.
    Ogni listing restituisce fino a 50 prodotti per pagina via skipitems.
    """
    categories: dict[str, list[str]] = {}
    for key, base_url in MS_STORE_LISTINGS.items():
        ids: list[str] = []
        for page_idx in range(max_pages):
            skip = page_idx * 50
            separator = "&" if "?" in base_url else "?"
            url = base_url if skip == 0 else f"{base_url}{separator}skipitems={skip}"
            log.info("[%s] pagina skipitems=%d", key, skip)
            try:
                html = fetch_with_retry(url, headers=HEADERS_HTML, ssl_ctx=ssl_ctx, timeout=30)
            except Exception as e:
                log.warning("[%s] errore pagina %s: %s", key, url, e)
                break
            page_ids = extract_store_product_ids(html)
            if not page_ids:
                break
            for gid in page_ids:
                if gid not in ids:
                    ids.append(gid)
            if len(page_ids) < 50:
                break
            time.sleep(delay)
        if ids:
            categories[key] = ids
            log.info("  %-20s -> %4d ID", key, len(ids))
    return categories, "microsoft-store"


def merge_categories(target: dict[str, list[str]], incoming: dict[str, list[str]]) -> None:
    """Unisce categorie deduplicando gli ID e preservando ordine."""
    for key, ids in incoming.items():
        bucket = target.setdefault(key, [])
        for gid in ids:
            if gid not in bucket:
                bucket.append(gid)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Xbox BigId discovery scraper")
    parser.add_argument("--page", metavar="URL",
                        help="URL pagina Xbox da cui cercare i bundle (default: auto)")
    parser.add_argument("--bundle", metavar="URL",
                        help="URL diretto del bundle JS (skip discovery)")
    parser.add_argument("--input", metavar="FILE",
                        help="File JS locale da cui estrarre i BigId")
    parser.add_argument("--out", default="bigids.json",
                        help="File JSON di output (default: bigids.json)")
    parser.add_argument("--source", choices=["xbox", "store", "combined"], default="combined",
                        help="Fonte discovery automatica: xbox, store o combined (default)")
    parser.add_argument("--store-pages", type=int, default=10,
                        help="Numero massimo di pagine Microsoft Store per listing (default: 10)")
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

    categories: dict[str, list[str]] = {}
    source = "local"

    if args.input:
        log.info("Lettura da file locale: %s", args.input)
        categories = load_from_local_file(args.input)
        source = args.input

    elif args.bundle:
        log.info("Download bundle diretto: %s", args.bundle)
        js_content = fetch_with_retry(args.bundle, headers=HEADERS_HTML, ssl_ctx=ssl_ctx, timeout=60)
        categories = extract_game_id_arrays(js_content)
        if not categories:
            url_map = extract_biurls_object(js_content)
            if url_map:
                categories = {"unknown": list(url_map.keys())}
        source = args.bundle

    else:
        # Modalità auto: discovery dalle fonti selezionate
        sources: list[str] = []
        if args.source in ("xbox", "combined"):
            pages = [args.page] if args.page else XBOX_PAGES
            xbox_categories, xbox_source = discover_xbox_categories(pages, ssl_ctx=ssl_ctx)
            merge_categories(categories, xbox_categories)
            if xbox_source:
                sources.append(xbox_source)
        if args.source in ("store", "combined") and not args.page:
            store_categories, store_source = discover_store_categories(
                ssl_ctx=ssl_ctx,
                max_pages=args.store_pages,
            )
            merge_categories(categories, store_categories)
            if store_source:
                sources.append(store_source)
        source = " + ".join(sources) if sources else args.source

    if not categories:
        log.error("Nessun BigId trovato.")
        log.info("Suggerimenti:")
        log.info("  1. Scarica manualmente il bundle JS dal DevTools di Chrome (tab Network -> JS)")
        log.info("     e salvalo come bundle.js, poi esegui:")
        log.info("     python3 fetch_bigids.py --input bundle.js")
        log.info("  2. Verifica che la pagina Xbox non abbia cambiato struttura")
        sys.exit(1)

    # Costruisci output con struttura per categoria
    all_ids_seen: set[str] = set()
    all_ids_ordered: list[str] = []
    cats_out: dict[str, dict] = {}
    for key, ids in categories.items():
        deduped = list(dict.fromkeys(ids))
        cats_out[key] = {
            "label": CATEGORY_LABELS.get(key, key),
            "count": len(deduped),
            "ids": deduped,
        }
        for gid in deduped:
            if gid not in all_ids_seen:
                all_ids_seen.add(gid)
                all_ids_ordered.append(gid)
        log.info("  %-20s -> %4d ID  (%s)", key, len(deduped), CATEGORY_LABELS.get(key, key))

    log.info("Totale ID unici: %d", len(all_ids_ordered))

    output = {
        "source": source,
        "total": len(all_ids_ordered),
        "categories": cats_out,
        "ids": all_ids_ordered,
    }
    Path(args.out).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Salvato in: %s", args.out)


if __name__ == "__main__":
    main()
