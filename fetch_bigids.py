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
    "ids": ["BIGID1", "BIGID2", ...],
    "urls": { "BIGID1": "https://xbox.com/games/...", ... },
    "total": 4000,
    "source": "https://..."
  }

Richiede Python 3 standard — nessuna libreria esterna necessaria.
"""

import urllib.request
import urllib.error
import urllib.parse
import json
import re
import ssl
import time
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
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}

# Pagine Xbox candidate contenenti i bundle JS con biUrls
XBOX_PAGES = [
    "https://www.xbox.com/it-IT/games/backward-compatibility",
    "https://www.xbox.com/en-US/games/backward-compatibility",
    "https://www.xbox.com/it-IT/games",
    "https://www.xbox.com/en-US/games",
]


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_text(url: str, timeout: int = 20, max_retries: int = 3) -> str:
    """Scarica il contenuto testuale di una URL con retry."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                # Rileva encoding dall'header Content-Type
                ct = resp.headers.get("Content-Type", "")
                enc_match = re.search(r'charset=([^\s;]+)', ct)
                encoding = enc_match.group(1) if enc_match else "utf-8"
                return resp.read().decode(encoding, errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                wait = 2 ** (attempt + 2)
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
    return ""


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
    seen = set()
    unique = []
    for s in scripts:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def discover_biurls_bundle(page_url: str) -> tuple[str | None, str | None]:
    """
    Cerca il bundle JS contenente 'biUrls' tra gli script della pagina.
    Ritorna (url_bundle, contenuto_js) o (None, None) se non trovato.
    """
    print(f"  Fetching pagina: {page_url}")
    try:
        html = fetch_text(page_url)
    except Exception as e:
        print(f"  ✗ Impossibile scaricare la pagina: {e}")
        return None, None

    script_urls = find_script_urls(html, page_url)
    print(f"  Trovati {len(script_urls)} script tag")

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

        print(f"  [{i:03d}/{len(script_urls)}] Checking: {src_url[-80:]}", end="", flush=True)
        try:
            js_content = fetch_text(src_url, timeout=30)
            if "biUrls" in js_content:
                print(f" ✓ biUrls trovato! ({len(js_content)//1024}KB)")
                return src_url, js_content
            else:
                print(f" — ({len(js_content)//1024}KB)")
        except Exception as e:
            print(f" ✗ {e}")

        time.sleep(0.1)

    return None, None


# ---------------------------------------------------------------------------
# GAP 3 — Estrazione BigId dal contenuto JS
# ---------------------------------------------------------------------------

BIGID_PATTERN = re.compile(
    r'"([A-Z0-9]{9,12})"\s*:\s*"(https://www\.xbox\.com/[^"]*)"'
)

def extract_biurls_object(js_content: str) -> dict[str, str]:
    """
    Estrae la mappa BigId → URL dall'oggetto biUrls nel bundle JS.
    Tenta prima il parsing strutturato, poi il fallback regex.
    """
    # Tentativo 1: estrazione strutturata dell'oggetto biUrls
    match = re.search(
        r'biUrls\s*[=:]\s*(\{[^;]{50,}\})',
        js_content,
        re.DOTALL
    )
    if match:
        try:
            raw = match.group(1)
            # Il JS potrebbe avere trailing comma o chiavi non quotate — normalizza
            # Rimuovi trailing comma prima di } o ]
            raw_clean = re.sub(r',\s*([}\]])', r'\1', raw)
            obj = json.loads(raw_clean)
            if "items" in obj and "urls" in obj.get("items", {}):
                return obj["items"]["urls"]
        except (json.JSONDecodeError, KeyError):
            pass

    # Tentativo 2: fallback regex — estrae direttamente le coppie BigId:URL
    result: dict[str, str] = {}
    for m in BIGID_PATTERN.finditer(js_content):
        bigid, url = m.group(1), m.group(2)
        result[bigid] = url

    return result


def load_from_local_file(path: str) -> dict[str, str]:
    """Estrae BigId da un file JS locale (es. bundle scaricato manualmente)."""
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    return extract_biurls_object(content)


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
    args = parser.parse_args()

    url_map: dict[str, str] = {}
    source = "local"

    if args.input:
        # Modalità locale: file JS già scaricato
        print(f"Lettura da file locale: {args.input}")
        url_map = load_from_local_file(args.input)
        source = args.input

    elif args.bundle:
        # Modalità bundle diretto: URL del JS noto
        print(f"Download bundle diretto: {args.bundle}")
        js_content = fetch_text(args.bundle, timeout=60)
        url_map = extract_biurls_object(js_content)
        source = args.bundle

    else:
        # Modalità auto: discovery dalla pagina Xbox
        pages = [args.page] if args.page else XBOX_PAGES
        for page_url in pages:
            print(f"\n--- Provo: {page_url}")
            bundle_url, js_content = discover_biurls_bundle(page_url)
            if js_content:
                url_map = extract_biurls_object(js_content)
                source = bundle_url or page_url
                if url_map:
                    break
                else:
                    print("  ⚠ Bundle trovato ma nessun BigId estratto, provo la prossima pagina...")

    if not url_map:
        print("\n✗ Nessun BigId trovato.")
        print("\nSuggerimenti:")
        print("  1. Scarica manualmente il bundle JS dal DevTools di Chrome (tab Network → JS)")
        print("     e salvalo come bundle.js, poi esegui:")
        print("     python3 fetch_bigids.py --input bundle.js")
        print("  2. Verifica che la pagina Xbox non abbia cambiato struttura")
        sys.exit(1)

    ids = list(dict.fromkeys(url_map.keys()))  # deduplicazione
    print(f"\n✅ Estratti {len(ids)} BigId unici")

    output = {
        "total": len(ids),
        "source": source,
        "ids": ids,
        "urls": url_map,
    }
    Path(args.out).write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   Salvato in: {args.out}")

    # Mostra anteprima
    print(f"\nPrime 5 entry:")
    for gid in ids[:5]:
        print(f"  {gid}: {url_map[gid][:70]}")


if __name__ == "__main__":
    main()
