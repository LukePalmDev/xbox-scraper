"""
Utilità condivise per gli script Xbox scraper.

Contiene: contesto SSL, headers HTTP, fetch con retry/backoff, generatore MS-CV.
"""

import urllib.request
import urllib.error
import json
import ssl
import time
import uuid
import re
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSL
# ---------------------------------------------------------------------------

def create_ssl_context(verify: bool = True) -> ssl.SSLContext:
    """Crea un contesto SSL. verify=True (default) abilita la verifica certificati."""
    if verify:
        return ssl.create_default_context()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------------------------------------------------------------------------
# Headers HTTP
# ---------------------------------------------------------------------------

HEADERS_JSON = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

HEADERS_HTML = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------------------
# MS-CV (Correlation Vector)
# ---------------------------------------------------------------------------

def generate_ms_cv() -> str:
    """Genera un nuovo MS-CV trace ID per le richieste API Microsoft."""
    return uuid.uuid4().hex[:16] + ".1"


# ---------------------------------------------------------------------------
# Fetch con retry e backoff esponenziale
# ---------------------------------------------------------------------------

def fetch_with_retry(
    url: str,
    headers: dict | None = None,
    ssl_ctx: ssl.SSLContext | None = None,
    max_retries: int = 3,
    timeout: int = 15,
    decode: bool = True,
) -> str | bytes:
    """
    Scarica contenuto da una URL con retry e backoff esponenziale.

    Gestisce rate limiting (429/503) con backoff più aggressivo.
    Se decode=True ritorna str, altrimenti bytes.
    """
    if headers is None:
        headers = HEADERS_JSON
    if ssl_ctx is None:
        ssl_ctx = create_ssl_context()

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                raw = resp.read()
                if not decode:
                    return raw
                ct = resp.headers.get("Content-Type", "")
                enc_match = re.search(r'charset=([^\s;]+)', ct)
                encoding = enc_match.group(1) if enc_match else "utf-8"
                return raw.decode(encoding, errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 503):
                wait = 2 ** (attempt + 2)
                log.warning("Rate limit (%d), attendo %ds...", e.code, wait)
                time.sleep(wait)
            elif attempt == max_retries - 1:
                raise
            else:
                wait = 2 ** attempt
                log.debug("HTTP %d, retry %d/%d in %ds", e.code, attempt + 1, max_retries, wait)
                time.sleep(wait)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = 2 ** attempt
            log.debug("Errore %s, retry %d/%d in %ds", e, attempt + 1, max_retries, wait)
            time.sleep(wait)
    return "" if decode else b""


def fetch_json(
    url: str,
    ssl_ctx: ssl.SSLContext | None = None,
    max_retries: int = 3,
    timeout: int = 15,
) -> dict:
    """Scarica e parsa JSON da una URL con retry."""
    raw = fetch_with_retry(
        url, headers=HEADERS_JSON, ssl_ctx=ssl_ctx,
        max_retries=max_retries, timeout=timeout, decode=False,
    )
    return json.loads(raw) if raw else {}
