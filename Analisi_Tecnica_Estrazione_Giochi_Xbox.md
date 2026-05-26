# Xbox Scraper — Analisi Tecnica

## Obiettivo

Costruire un catalogo statico dei giochi Xbox esposti dalla pagina pubblica di retrocompatibilita/catalogo Xbox e dalla Microsoft Display Catalog API.

Il flusso attuale:

1. scopre i BigId dal bundle JavaScript della pagina Xbox;
2. salva l'elenco normalizzato in `bigids.json`;
3. interroga Display Catalog API per recuperare metadati, immagini e prezzi;
4. salva `games.json`;
5. genera `index.html`, pubblicabile su GitHub Pages.

Non vengono scaricati giochi, binari o asset protetti oltre alle immagini pubbliche referenziate dal catalogo.

## Stato attuale

| Area | Stato |
| --- | --- |
| Discovery BigId | Implementata in `fetch_bigids.py` |
| Categorie BigId | Implementate in `bigids.json` |
| Scraping catalogo | Implementato in `fetch_xbox_og.py` |
| Retry/backoff | Implementato in `scraper_utils.py` |
| Resume errori | Implementato tramite `failed_ids.json` |
| Filtro mercato `<exc>` | Implementato via mappa URL legacy quando disponibile |
| Output JSON | Implementato con `--json-out` |
| Output HTML | Implementato con ricerca, filtri e sort |
| Deploy GitHub Pages | Implementato in `.github/workflows/pages.yml` |
| Aggiornamento schedulato | Implementato in `.github/workflows/scrape.yml` |
| Health check giornaliero | Implementato in `.github/workflows/verify.yml` |

Snapshot locale:

| Output | Conteggio |
| --- | ---: |
| BigId unici | 4277 |
| Giochi in `games.json` | 4276 |
| Card in `index.html` | 4276 |

## Architettura

```text
[Pagina Xbox]
      |
      v
[HTML pubblico]
      |
      v
[Script bundle JS]
      |
      v
[gameIdArrays / biUrls]
      |
      v
[bigids.json]
      |
      v
[Display Catalog API]
      |
      v
[games.json]
      |
      v
[index.html statico]
      |
      v
[GitHub Pages]
```

## Discovery BigId

`fetch_bigids.py` prova in ordine:

1. pagina passata con `--page`;
2. bundle diretto passato con `--bundle`;
3. file locale passato con `--input`;
4. lista di pagine Xbox candidate (`it-IT` ed `en-US`).

La discovery scarica l'HTML, estrae gli URL `<script src="...">`, ordina i bundle per priorita e cerca marker noti:

- `gameIdArrays`
- `biUrls`

Il formato preferito e `gameIdArrays`:

```javascript
gameIdArrays["xboxOG"] = ["BS7SQNNRB28W", "..."];
gameIdArrays["xbox360"] = ["C0J2F5B1B7JD", "..."];
```

Il fallback legacy e `biUrls`:

```javascript
biUrls = {
  "items": {
    "urls": {
      "BRVM8RNWLXH1": "https://www.xbox.com/games/..."
    }
  }
}
```

Output `bigids.json`:

```json
{
  "source": "https://www.xbox.com/...",
  "total": 4277,
  "categories": {
    "xboxOG": {
      "label": "Xbox Original (OG)",
      "count": 61,
      "ids": []
    }
  },
  "ids": []
}
```

## Display Catalog API

Endpoint usato:

```text
GET https://displaycatalog.mp.microsoft.com/v7.0/products
```

Parametri:

| Parametro | Valore |
| --- | --- |
| `bigIds` | lista BigId separati da virgola |
| `market` | default `IT` |
| `languages` | default `it-it` |
| `MS-CV` | correlation vector generato localmente |

Esempio:

```text
https://displaycatalog.mp.microsoft.com/v7.0/products?bigIds=BS7SQNNRB28W&market=IT&languages=it-it&MS-CV=<cv>
```

Campi usati dalla risposta:

| Campo API | Uso |
| --- | --- |
| `ProductId` | ID prodotto |
| `LocalizedProperties[0].ProductTitle` | titolo |
| `LocalizedProperties[0].Images[]` | immagine card |
| `DisplaySkuAvailabilities[].Availabilities[]...ListPrice` | prezzo |
| `Properties.Categories` | genere |

## Scraping

`fetch_xbox_og.py` carica gli ID da `bigids.json` o da un file passato con `--ids`.

Comando completo consigliato:

```bash
python3 fetch_xbox_og.py \
  --category all \
  --batch 50 \
  --delay 0.3 \
  --workers 3 \
  --out index.html \
  --json-out games.json
```

La modalita concorrente usa `ThreadPoolExecutor`. I primi worker sono sfalsati per evitare una raffica iniziale verso l'API. Gli errori di rete o HTTP transitori passano da `fetch_with_retry()` con backoff esponenziale.

Il flag `--filter-market` usa la mappa BigId -> URL del file legacy `xcat-bi-urls2.json` per interpretare i suffissi `<exc>MARKET`. Se la mappa URL non copre tutti gli ID dello scrape, il filtro viene applicato solo agli ID coperti e lo script emette un warning.

Output della funzione `scrape()`:

| Valore | Significato |
| --- | --- |
| `games` | prodotti normalizzati e deduplicati |
| `failed_ids` | ID falliti per errore di rete/API |
| `missing_ids` | ID richiesti ma non restituiti dall'API |

## HTML generato

`build_html()` produce una pagina statica con:

- CSS inline;
- JavaScript inline senza dipendenze;
- ricerca debounced;
- filtri dinamici generati dai dati reali;
- ordinamento numerico del prezzo con `data-price-num`;
- link allo store Xbox tramite `https://www.xbox.com/games/store/-/<ProductId>`.

Attributi principali delle card:

```html
<div class="game-card"
     data-title="..."
     data-cat="xbox-original-og"
     data-genre="action-adventure"
     data-price-num="19.99">
```

## Automazioni

### CI

`.github/workflows/ci.yml`

- gira su push e PR;
- installa Python;
- compila tutti i file `.py`.

### Verify giornaliero

`.github/workflows/verify.yml`

- gira ogni giorno;
- compila gli script;
- esegue discovery BigId in un file temporaneo;
- verifica che il numero di BigId sia sopra una soglia minima;
- esegue uno scrape piccolo su `xboxOG`;
- verifica che il JSON generato contenga risultati.

Questo workflow non committa modifiche.

### Scrape settimanale

`.github/workflows/scrape.yml`

- gira ogni lunedi;
- prova discovery dei BigId;
- usa fallback sul `bigids.json` versionato se la discovery fallisce;
- rigenera `games.json` e `index.html`;
- committa e pusha solo se ci sono modifiche.

### Deploy Pages

`.github/workflows/pages.yml`

- si attiva quando cambiano `index.html` o `games.json` su `master`;
- pubblica l'intero contenuto del repository come sito statico.

## Rischi tecnici

| Rischio | Mitigazione |
| --- | --- |
| Xbox cambia struttura HTML o nomi bundle | `verify.yml` fallisce sulla discovery giornaliera |
| Xbox cambia marker JS (`gameIdArrays`/`biUrls`) | `fetch_bigids.py` ha fallback locale/manuale |
| Display Catalog API cambia endpoint o schema | `verify.yml` fallisce sullo scrape campione |
| Rate limit Microsoft | batch configurabile, delay, workers limitati, retry/backoff |
| Prodotti delisted o non restituiti | tracciati come `missing_ids`, non considerati errore fatale |

## Roadmap residua

| Priorita | Attivita |
| --- | --- |
| Media | Aggiungere test unitari per parsing `gameIdArrays`, `biUrls` e normalizzazione prodotto |
| Media | Separare template HTML da logica Python se la UI cresce |
| Bassa | Aggiungere report storico delle differenze fra run |
| Bassa | Rendere configurabili soglie health check via workflow inputs |

## Comandi di verifica locale

```bash
python3 -m py_compile fetch_bigids.py fetch_xbox_og.py scraper_utils.py
python3 fetch_bigids.py --out /tmp/bigids_verify.json
python3 fetch_xbox_og.py --ids /tmp/bigids_verify.json --category xboxOG --out /tmp/xbox_verify.html --json-out /tmp/xbox_verify.json
```
