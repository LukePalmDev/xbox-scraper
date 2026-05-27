"""
Xbox BigId Discovery — estrae ProductId/BigId dalle fonti pubbliche Xbox.

Uso:
  python3 fetch_bigids.py                          # discovery combinata Browse + legacy + Store
  python3 fetch_bigids.py --source browse          # solo pagina Xbox Browse paginata
  python3 fetch_bigids.py --page URL               # URL pagina Browse custom
  python3 fetch_bigids.py --bundle URL             # URL bundle JS diretto (skip discovery)
  python3 fetch_bigids.py --input FILE             # estrai BigId da file JS locale
  python3 fetch_bigids.py --out bigids.json        # file output (default: bigids.json)

Output: bigids.json con struttura:
  {
    "source": "...",
    "total": 16482,
    "categories": {
      "xboxBrowse":    { "label": "Xbox Browse - All games", "ids": [...] },
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

import base64
import urllib.parse
import urllib.request
import urllib.error
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
    generate_ms_cv,
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

XBOX_BROWSE_PAGE = "https://www.xbox.com/it-IT/games/browse"
XBOX_BROWSE_ENDPOINT = "https://emerald.xboxservices.com/xboxcomfd/browse"
XBOX_BROWSE_CHANNEL_KEY = "BROWSE_CHANNELID=_FILTERS="
EMPTY_BROWSE_FILTERS = "e30="  # base64("{}")


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
    "xboxBrowse":     "Xbox Browse - All games",
    "xboxBrowseRecovery": "Xbox Browse - Sort recovery",
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


def extract_preloaded_state(html: str) -> dict:
    """
    Estrae e parsa window.__PRELOADED_STATE__ dalla pagina Xbox.

    La variabile contiene JSON valido, ma non è comodo usare una regex greedy
    perché dopo l'oggetto possono esserci altre istruzioni nello stesso script.
    """
    marker = "window.__PRELOADED_STATE__"
    marker_pos = html.find(marker)
    if marker_pos < 0:
        return {}

    start = html.find("{", marker_pos)
    if start < 0:
        return {}

    depth = 0
    in_string = False
    escaped = False
    for pos in range(start, len(html)):
        ch = html[pos]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[start:pos + 1])

    return {}


def extract_browse_channel_data(
    state: dict,
    channel_key: str = XBOX_BROWSE_CHANNEL_KEY,
) -> tuple[list[str], str | None, int | None]:
    """Estrae ProductId, continuation token e totale dal canale Browse pre-caricato."""
    channel = (
        state.get("core2", {})
        .get("channels", {})
        .get("channelData", {})
        .get(channel_key, {})
        .get("data", {})
    )
    products = channel.get("products") or []
    ids = [
        str(product.get("productId", "")).upper()
        for product in products
        if product.get("productId")
    ]
    return list(dict.fromkeys(ids)), channel.get("encodedCT"), channel.get("totalItems")


def browse_has_more(encoded_ct: str | None) -> bool:
    """Ritorna True se il continuation token del Browse dichiara altre pagine."""
    if not encoded_ct:
        return False
    try:
        payload = json.loads(base64.b64decode(encoded_ct).decode("utf-8"))
    except Exception:
        return True
    return bool(payload.get("HasMore"))


def encode_browse_filters(filters: dict | None) -> str:
    """Codifica i filtri Browse nello stesso formato usato dal frontend Xbox."""
    return base64.b64encode(
        json.dumps(filters or {}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def build_browse_channel_key(
    filters: dict | None,
    channel_id: str = "",
) -> str:
    """Replica la chiave canale frontend: BROWSE_CHANNELID=<id>_FILTERS=<filtri>."""
    parts: list[str] = []
    for filter_def in (filters or {}).values():
        choices = filter_def.get("choices") or []
        if not choices:
            continue
        choice_ids = ",".join(sorted(str(choice["id"]).upper() for choice in choices))
        parts.append(f"{str(filter_def['id']).upper()}={choice_ids}")
    encoded_filters = "&".join(sorted(parts))
    return f"BROWSE_CHANNELID={channel_id}_FILTERS={encoded_filters}".upper()


def post_json_with_retry(
    url: str,
    payload: dict,
    headers: dict,
    ssl_ctx=None,
    max_retries: int = 3,
    timeout: int = 30,
) -> dict:
    """POST JSON con retry/backoff, usato dall'endpoint Emerald Browse."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                wait = 2 ** (attempt + 2)
                log.warning("Rate limit Browse (%d), attendo %ds...", e.code, wait)
                time.sleep(wait)
            elif attempt == max_retries - 1:
                raise
            else:
                wait = 2 ** attempt
                log.debug("HTTP Browse %d, retry %d/%d in %ds", e.code, attempt + 1, max_retries, wait)
                time.sleep(wait)
        except Exception:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            log.debug("Errore Browse, retry %d/%d in %ds", attempt + 1, max_retries, wait)
            time.sleep(wait)
    return {}


def fetch_browse_channel_page(
    encoded_ct: str | None,
    locale: str = "it-IT",
    channel_key: str = XBOX_BROWSE_CHANNEL_KEY,
    encoded_filters: str = EMPTY_BROWSE_FILTERS,
    return_filters: bool = False,
    ssl_ctx=None,
) -> dict:
    """Scarica una pagina successiva del catalogo Xbox Browse via continuation token."""
    endpoint = f"{XBOX_BROWSE_ENDPOINT}?{urllib.parse.urlencode({'locale': locale})}"
    headers = {
        "Accept": "application/json",
        "Accept-Language": locale,
        "Content-Type": "application/json",
        "Origin": "https://www.xbox.com",
        "Referer": "https://www.xbox.com/",
        "User-Agent": HEADERS_HTML["User-Agent"],
        "MS-CV": generate_ms_cv(),
        "X-MS-API-Version": "1.1",
    }
    payload = {
        "Filters": encoded_filters,
        "ReturnFilters": return_filters,
        "ChannelKeyToBeUsedInResponse": channel_key,
        "ChannelId": "",
    }
    if encoded_ct:
        payload["EncodedCT"] = encoded_ct
    return post_json_with_retry(endpoint, payload, headers=headers, ssl_ctx=ssl_ctx, timeout=45)


def discover_browse_categories(
    page_url: str = XBOX_BROWSE_PAGE,
    locale: str = "it-IT",
    ssl_ctx=None,
    max_pages: int = 0,
    delay: float = 0.05,
) -> tuple[dict[str, list[str]], str, int | None]:
    """
    Scopre gli ID dalla pagina Xbox Browse ufficiale.

    La pagina espone i primi 25 prodotti in window.__PRELOADED_STATE__ e un
    encodedCT. L'endpoint Emerald restituisce i successivi 25 prodotti e il
    token nuovo fino a esaurimento.
    """
    log.info("--- Provo Xbox Browse: %s", page_url)
    html = fetch_with_retry(page_url, headers=HEADERS_HTML, ssl_ctx=ssl_ctx, timeout=30)
    state = extract_preloaded_state(html)
    ids, encoded_ct, total_items = extract_browse_channel_data(state)
    if not ids:
        log.warning("Nessun prodotto nello stato iniziale Xbox Browse")
        return {}, "", None

    seen: set[str] = set(ids)
    ordered_ids = list(ids)
    pages = 1
    log.info("  xboxBrowse pagina iniziale -> %d ID (totale sito: %s)", len(ids), total_items or "N/D")

    while encoded_ct and browse_has_more(encoded_ct):
        if max_pages and pages >= max_pages:
            log.info("Limite pagine Browse raggiunto: %d", max_pages)
            break

        data = fetch_browse_channel_page(
            encoded_ct,
            locale=locale,
            channel_key=XBOX_BROWSE_CHANNEL_KEY,
            ssl_ctx=ssl_ctx,
        )
        channel = data.get("channels", {}).get(XBOX_BROWSE_CHANNEL_KEY, {})
        page_ids = [
            str(product.get("productId", "")).upper()
            for product in channel.get("products", [])
            if product.get("productId")
        ]
        if not page_ids:
            log.warning("Pagina Browse senza prodotti dopo %d pagine", pages)
            break
        for gid in page_ids:
            if gid not in seen:
                seen.add(gid)
                ordered_ids.append(gid)

        pages += 1
        encoded_ct = channel.get("encodedCT")
        if pages % 50 == 0:
            log.info("  xboxBrowse pagine=%d -> %d ID unici", pages, len(ordered_ids))
        if delay > 0:
            time.sleep(delay)

    log.info("  xboxBrowse completato -> %d ID unici in %d pagine", len(ordered_ids), pages)
    return {"xboxBrowse": ordered_ids}, page_url, total_items


BROWSE_RECOVERY_SORTS = [
    "Title Asc",
    "Title Desc",
    "ReleaseDate desc",
    "MostPopular desc",
]


def discover_browse_recovery_category(
    existing_ids: set[str],
    target_total: int,
    locale: str = "it-IT",
    ssl_ctx=None,
    delay: float = 0.0,
) -> dict[str, list[str]]:
    """
    Recupera ID mancanti usando ordinamenti ufficiali del Browse endpoint.

    Il canale base puo dichiarare totalItems=16482 ma chiudere prima con
    HasMore=false. Gli ordinamenti espongono alcuni ID non presenti nella
    paginazione base; qui aggiungiamo solo quanto basta a raggiungere il target.
    """
    recovered: list[str] = []
    for sort_id in BROWSE_RECOVERY_SORTS:
        if len(existing_ids) + len(recovered) >= target_total:
            break
        filters = {"orderby": {"id": "orderby", "choices": [{"id": sort_id}]}}
        channel_key = build_browse_channel_key(filters)
        encoded_filters = encode_browse_filters(filters)
        encoded_ct = None
        pages = 0
        log.info("Recovery Browse con ordinamento: %s", sort_id)

        while True:
            data = fetch_browse_channel_page(
                encoded_ct,
                locale=locale,
                channel_key=channel_key,
                encoded_filters=encoded_filters,
                return_filters=encoded_ct is None,
                ssl_ctx=ssl_ctx,
            )
            channel = data.get("channels", {}).get(channel_key, {})
            page_ids = [
                str(product.get("productId", "")).upper()
                for product in channel.get("products", [])
                if product.get("productId")
            ]
            if not page_ids:
                break

            for gid in page_ids:
                if gid in existing_ids or gid in recovered:
                    continue
                recovered.append(gid)
                if len(existing_ids) + len(recovered) >= target_total:
                    break

            pages += 1
            if len(existing_ids) + len(recovered) >= target_total:
                break

            encoded_ct = channel.get("encodedCT")
            if not browse_has_more(encoded_ct):
                break
            if pages % 100 == 0:
                log.info(
                    "  recovery %s pagine=%d -> +%d ID (totale stimato %d/%d)",
                    sort_id,
                    pages,
                    len(recovered),
                    len(existing_ids) + len(recovered),
                    target_total,
                )
            if delay > 0:
                time.sleep(delay)

        log.info("  recovery %s completato in %d pagine -> +%d ID", sort_id, pages, len(recovered))

    return {"xboxBrowseRecovery": recovered} if recovered else {}


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


def collect_unique_ids(categories: dict[str, list[str]]) -> list[str]:
    """Restituisce gli ID unici tra categorie preservando ordine di discovery."""
    seen: set[str] = set()
    ordered: list[str] = []
    for ids in categories.values():
        for gid in ids:
            if gid not in seen:
                seen.add(gid)
                ordered.append(gid)
    return ordered


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
    parser.add_argument("--source", choices=["browse", "xbox", "store", "combined"], default="combined",
                        help="Fonte discovery automatica: browse, xbox legacy, store o combined (default)")
    parser.add_argument("--browse-pages", type=int, default=0,
                        help="Numero massimo di pagine Xbox Browse, 0 = tutte (default)")
    parser.add_argument("--browse-delay", type=float, default=0.05,
                        help="Pausa tra pagine Xbox Browse in secondi (default: 0.05)")
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
        browse_target_total: int | None = None
        if args.source in ("browse", "combined"):
            browse_page = args.page or XBOX_BROWSE_PAGE
            browse_categories, browse_source, browse_target_total = discover_browse_categories(
                page_url=browse_page,
                ssl_ctx=ssl_ctx,
                max_pages=args.browse_pages,
                delay=args.browse_delay,
            )
            merge_categories(categories, browse_categories)
            if browse_source:
                sources.append(browse_source)
        if args.source == "xbox" or (args.source == "combined" and not args.page):
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
        if args.source == "combined" and browse_target_total:
            current_ids = collect_unique_ids(categories)
            if len(current_ids) < browse_target_total:
                log.info(
                    "Totale sotto al target Browse (%d/%d), avvio recovery ordinamenti",
                    len(current_ids),
                    browse_target_total,
                )
                recovery_categories = discover_browse_recovery_category(
                    set(current_ids),
                    browse_target_total,
                    ssl_ctx=ssl_ctx,
                    delay=args.browse_delay,
                )
                merge_categories(categories, recovery_categories)
                if recovery_categories:
                    sources.append("xbox-browse-recovery")
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
