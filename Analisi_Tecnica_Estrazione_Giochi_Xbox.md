# Xbox Scraper — Analisi Tecnica e Roadmap

## Obiettivo

Recuperare l'elenco completo (~4000 titoli) dei giochi Xbox retrocompatibili
tramite le API Microsoft Display Catalog, senza scaricare i giochi,
e presentarli in un'interfaccia HTML navigabile (ricerca, ordinamento, immagini, prezzi).

---

## Architettura del sistema Microsoft

```
[Pagina Xbox retrocompatibilità]
        |
        v
[Bundle JS] — contiene oggetto `biUrls` con mappa BigId → URL pubblico Xbox
        |
        v
[BigId] — identificatore univoco prodotto Microsoft Store (es. "BRVM8RNWLXH1")
        |
        v
[Display Catalog API] — displaycatalog.mp.microsoft.com
  GET /v7.0/products?bigIds=ID1,ID2,...&market=IT&languages=it-it&MS-CV=...
        |
        v
[JSON Response] — metadati completi: titolo, immagini, prezzi, disponibilità
```

### Struttura oggetto `biUrls` nel bundle JS

```javascript
biUrls = {
  "items": {
    "urls": {
      "BRVM8RNWLXH1": "https://www.xbox.com/games/ace-combat-7-skies-unknown",
      "9NXXNTRZBS0Z": "https://www.xbox.com/games/destiny-2<exc>ko-kr",
      ...
    }
  }
}
```

Le chiavi sono i BigId; i valori sono gli URL pubblici Xbox con eventuale
suffisso `<exc>REGIONI` che indica mercati in cui il gioco non è disponibile.

### Endpoint API Display Catalog

```
Host:     https://displaycatalog.mp.microsoft.com
Endpoint: /v7.0/products
Metodo:   GET

Parametri obbligatori:
  bigIds     — lista BigId separati da virgola (max ~20-50 per request)
  market     — codice mercato (es. "IT")
  languages  — locale (es. "it-it")
  MS-CV      — header di tracciamento Microsoft (valore statico accettato)

Risposta JSON struttura:
  Products[].LocalizedProperties[0].ProductTitle     — titolo
  Products[].LocalizedProperties[0].Images[]         — immagini (Purpose: SuperHeroArt, BoxArt, ecc.)
  Products[].DisplaySkuAvailabilities[0].Availabilities[].OrderManagementData.Price.ListPrice
  Products[].Properties.Category                     — categoria prodotto
  Products[].Properties.IsBackwardsCompatible        — flag retrocompatibilità (da verificare)
```

---

## Stato attuale (proof of concept)

| File | Contenuto | Stato |
|------|-----------|-------|
| `xcat-bi-urls2.json` | 109 BigId estratti manualmente dal bundle JS (formato JS, non JSON puro) | Parziale |
| `fetch_xbox_og.py` | Script che chiama l'API e genera HTML | Funzionante ma limitato |
| `xbox_og_games.html` | Output HTML generato | Obsoleto (61 giochi hardcodati) |

**Problema critico:** lo script usa 61 ID hardcodati che non coincidono
con quelli nel file JSON. I due asset non sono collegati.
Copertura reale: ~61/4000 titoli (< 2%).

---

## Gap identificati e Roadmap implementativa

### GAP 1 — Script non legge il JSON
**Problema:** i 61 BigId in `fetch_xbox_og.py` sono hardcodati manualmente;
il file `xcat-bi-urls2.json` non viene mai importato.

**Soluzione:** refactoring dello script per caricare i BigId dal file
`xcat-bi-urls2.json` con parsing dell'oggetto JS `biUrls`.

**File coinvolti:** `fetch_xbox_og.py`, `xcat-bi-urls2.json`
**Output atteso:** script che legge dinamicamente i 109 ID dal file.
**Stato:** [x] implementato in `fetch_xbox_og.py` (`load_ids()`)

---

### GAP 2+3 — Lista BigId incompleta + nessuna automazione discovery
**Problema:** il file `xcat-bi-urls2.json` contiene solo 109 entry su ~4000.
Non esiste logica per trovare e scaricare il bundle JS da Xbox.

**Soluzione:** nuovo script `fetch_bigids.py` che:
1. Accede alla pagina Xbox retrocompatibilità per trovare i riferimenti ai bundle JS
2. Scarica il bundle JS che contiene l'oggetto `biUrls`
3. Estrae tutti i BigId tramite regex
4. Salva il risultato in `bigids.json` (lista pulita, formato JSON valido)

**File coinvolti:** `fetch_bigids.py` (nuovo), `bigids.json` (output)
**Output atteso:** file `bigids.json` con l'elenco completo dei BigId.
**Stato:** [x] implementato in `fetch_bigids.py` (discovery + regex + fallback locale)

---

### GAP 4 — Rate limiting e retry assenti
**Problema:** l'unica gestione è un `time.sleep(0.15)` fisso.
Con ~4000 ID in batch da 20 = ~200 request; senza retry i fallimenti sono persi.

**Soluzione:**
- Retry con backoff esponenziale (max 3 tentativi per batch)
- Delay adattivo (aumenta dopo errori HTTP 429/503)
- Logging degli ID falliti con salvataggio in `failed_ids.json`
- Possibilità di riprendere da dove si era rimasti

**File coinvolti:** `fetch_xbox_og.py`
**Output atteso:** scraper robusto che completa anche in caso di errori transitori.
**Stato:** [x] implementato in `fetch_xbox_og.py` (`fetch_batch()`, backoff esponenziale, `failed_ids.json`, `--resume`)

---

### GAP 5 — Nessun filtro per retrocompatibilità
**Problema:** il bundle JS include giochi di categorie diverse. Il badge "OG"
nell'HTML è decorativo, non semantico. Non c'è distinzione tra OG Xbox,
Xbox 360 e Xbox One retrocompatibili.

**Soluzione:**
- Parsing del suffisso `<exc>` per escludere mercati non supportati per IT
- Argomento CLI `--filter og|360|xone|all` per selezionare la categoria
- Lettura del campo categoria dalla risposta API per taggare ogni gioco

**File coinvolti:** `fetch_xbox_og.py`
**Output atteso:** HTML con tag categoria per ogni gioco, filtrabile da CLI.
**Stato:** [x] implementato in `fetch_xbox_og.py` (`--filter-market`, `filter_by_market()`, campo categoria in HTML)

---

## Feature aggiuntive

### FEATURE A — Selettore categoria console (terminale interattivo)
**Obiettivo:** permettere all'utente di scegliere quale generazione di giochi
scaricare prima di avviare lo scraping, con un menu numerato da terminale.

**Generazioni supportate** (da `XboxConsoleGenOptimized` nell'API):

| Codice | Console | Campo API |
|--------|---------|-----------|
| `all`  | Tutte le generazioni | — |
| `og`   | Xbox Original (2001) | `ConsoleGen6` |
| `360`  | Xbox 360 (2005) | `ConsoleGen7` |
| `xone` | Xbox One (2013) | `ConsoleGen8` (non Gen9) |
| `series` | Xbox Series X\|S enhanced | `ConsoleGen9` |

**Comportamento:**
- Senza argomenti: menu interattivo da terminale con selezione numerata
- Con `--category CODICE`: non-interattivo (per automazione/scripting)
- La categoria viene rilevata **post-fetch** dall'API e usata per filtrare i risultati

**File coinvolti:** `fetch_xbox_og.py`
**Stato:** [x] implementato (`select_category_interactive()`, `--category`, `detect_console_gen()`)

---

### FEATURE B — Download catalogo retrocompatibile completo
**Obiettivo:** scaricare l'intero elenco di giochi retrocompatibili Xbox (stimato
~4000 titoli) con supporto per specificare la categoria/generazione target.

**Pagine Xbox note per categoria:**

| Categoria | URL |
|-----------|-----|
| Tutte | `https://www.xbox.com/en-US/games/backward-compatibility` |
| OG Xbox | stessa pagina, sezione dedicata |
| Xbox 360 | stessa pagina, sezione dedicata |

**Approccio:** `fetch_bigids.py --page URL` tenta discovery automatica;
se fallisce, usa `--input bundle.js` con il file JS scaricato manualmente
dal DevTools (Network → JS → cerca "biUrls").

**File coinvolti:** `fetch_bigids.py`
**Stato:** [x] implementato (discovery automatica + fallback locale)

---

### FEATURE C — HTML: filtri per genere, generazione e sort prezzo numerico
**Obiettivo:** interfaccia HTML con filtri per:
- **Genere gioco** (da `Categories` API, es. "Action & adventure", "Role playing")
- **Generazione console** (da `XboxConsoleGenOptimized`, es. "Xbox One", "Xbox 360")
- **Sort prezzo** (numerico, non lessicografico — valore estratto e messo in `data-price`)
- **Sort alfabetico** (già presente)

**Dati aggiuntivi per card HTML:**
```
data-genre="action--adventure"   ← Categories[0] normalizzato
data-gen="xone"                   ← generazione rilevata
data-price-num="19.99"            ← prezzo numerico per sort
```

**UI:** pill-button per ogni filtro, toggle multiplo, reset "Tutti"

**File coinvolti:** `fetch_xbox_og.py` (generazione HTML)
**Stato:** [x] implementato (filtri dinamici generati dai dati reali)

---

## Ordine di implementazione

```
[GAP 1] Lettura BigId da file                    ← fetch_xbox_og.py legge xcat-bi-urls2.json
    ↓
[GAP 2+3] Scraper bundle JS                      ← nuovo fetch_bigids.py → bigids.json (~4000 ID)
    ↓
[GAP 4] Rate limiting + retry                    ← integrato in fetch_xbox_og.py
    ↓
[GAP 5] Filtro retrocompatibilità + tag          ← argomento CLI + logica in fetch_xbox_og.py
    ↓
[FEATURE A] Terminal selector generazione        ← menu interattivo + --category CLI
    ↓
[FEATURE B] Download catalogo completo           ← fetch_bigids.py multi-pagina
    ↓
[FEATURE C] HTML: filtri genere/gen + sort fix   ← pill-button + data attributes
```

---

## Dettagli tecnici implementativi

### Parsing del file xcat-bi-urls2.json (formato JS)

```python
import re, json

def load_bigids_from_js(path):
    with open(path) as f:
        content = f.read()
    # Estrai il JSON dall'assegnazione JS: biUrls = { ... }
    match = re.search(r'biUrls\s*=\s*(\{.*\})', content, re.DOTALL)
    obj = json.loads(match.group(1))
    urls = obj["items"]["urls"]
    return list(urls.keys()), urls  # (lista id, mappa id→url)
```

### Batching delle richieste API

```python
def chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

BATCH_SIZE = 20  # conservativo; Microsoft accetta fino a ~50
```

### Retry con backoff esponenziale

```python
import time

def fetch_with_retry(url, headers, ctx, max_retries=3):
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)  # 1s, 2s, 4s
```

### Scoperta URL bundle JS

```python
# La pagina Xbox carica un HTML che referenzia i bundle JS via <script src="...">
# Pattern atteso: file con nome tipo chunk-*.js o main-*.js contenente "biUrls"
import re, urllib.request

def find_biurls_bundle(page_url):
    html = fetch_text(page_url)
    scripts = re.findall(r'<script[^>]+src="([^"]+\.js[^"]*)"', html)
    for src in scripts:
        js = fetch_text(src)
        if 'biUrls' in js:
            return js
    return None
```

---

## Stack tecnologico

- **Python 3** — stdlib only (`urllib`, `json`, `re`, `time`, `argparse`, `ssl`)
- **No dipendenze esterne** — compatibile con qualsiasi ambiente
- **Output:** HTML statico self-contained (CSS + JS inline)

---

## Note di sicurezza / rate limiting

- L'API Display Catalog è pubblica ma Microsoft può imporre rate limit
- Usare delay tra i batch (default: 0.3s, aumenta a 2s dopo un 429)
- Il parametro MS-CV è un correlation vector statico, accettato dall'API
- Non è necessario autenticarsi (API pubblica non autenticata)
