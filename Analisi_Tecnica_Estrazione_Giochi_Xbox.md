# Xbox Scraper — Analisi Tecnica

## Obiettivo

Costruire un catalogo statico dei giochi Xbox esposti dalla pagina pubblica `https://www.xbox.com/it-IT/games/browse`, avvicinandosi il piu possibile al totale dichiarato dal sito, e arricchirli tramite Microsoft Display Catalog API.

Il flusso attuale:

1. legge i primi ProductId e il `totalItems` da `window.__PRELOADED_STATE__`;
2. pagina il canale Browse tramite endpoint Emerald e `encodedCT`;
3. integra ProductId dai bundle legacy Xbox e dalle pagine Microsoft Store paginate;
4. se il merge resta sotto `totalItems`, usa ordinamenti Browse ufficiali come recovery controllato;
5. salva l'elenco normalizzato in `bigids.json`;
6. interroga Display Catalog API per recuperare metadati, immagini e prezzi;
7. salva `games.json`;
8. genera `index.html`, pubblicabile su GitHub Pages.

Non vengono scaricati giochi, binari o asset protetti oltre alle immagini pubbliche referenziate dal catalogo.

## Stato attuale

| Area | Stato |
| --- | --- |
| Discovery Xbox Browse | Implementata in `fetch_bigids.py` via `encodedCT` |
| Recovery Browse | Implementata con ordinamenti ufficiali fino a `totalItems` |
| Discovery BigId legacy | Implementata in `fetch_bigids.py` |
| Discovery Microsoft Store | Implementata via listing paginati `microsoft.com/store` |
| Categorie BigId | Implementate in `bigids.json` |
| Scraping catalogo | Implementato in `fetch_xbox_og.py` |
| Generazione HTML | Isolata in `html_builder.py` |
| Retry/backoff | Implementato in `scraper_utils.py` |
| Resume errori | Implementato tramite `failed_ids.json` |
| Filtro mercato `<exc>` | Implementato via mappa URL legacy quando disponibile |
| Validazione CLI | Implementata per file `--ids`, batch, delay e workers |
| Pulizia artefatti locali | Implementata in `scripts/clean_artifacts.sh` |
| Report differenze scrape | Implementato come artifact `scrape-report.json` nel workflow settimanale |
| Output JSON | Implementato con `--json-out` |
| Output HTML | Implementato con ricerca, filtri, sort e card cliccabili |
| Deploy GitHub Pages | Implementato in `.github/workflows/pages.yml` |
| Aggiornamento schedulato | Implementato in `.github/workflows/scrape.yml` |
| Health check giornaliero | Implementato in `.github/workflows/verify.yml` |

Snapshot locale del 2026-05-27:

| Output | Conteggio |
| --- | ---: |
| BigId/ProductId unici | 16483 |
| Giochi in `games.json` | 16482 |
| Card in `index.html` | 16482 |
| Missing Display Catalog | 1 (`BTQSPR43SR63`) |

## Architettura

```text
[Pagina Xbox Browse]
      |
      v
[HTML pubblico con window.__PRELOADED_STATE__]
      |
      v
[encodedCT + endpoint Emerald Browse]
      |
      v
[xboxBrowse]
      |
      v
[bundle JS legacy + Microsoft Store listing]
      |
      v
[merge + recovery ordinamenti Browse]
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

1. file locale passato con `--input`;
2. bundle diretto passato con `--bundle`;
3. pagina Browse passata con `--page` o default `https://www.xbox.com/it-IT/games/browse`;
4. bundle legacy Xbox candidate (`it-IT` ed `en-US`);
5. listing Microsoft Store.

In modalita automatica la sorgente predefinita e `combined`: unisce Browse, bundle legacy e listing Microsoft Store paginata `most-popular`. Il Browse endpoint dichiara un `totalItems`; se la paginazione base termina prima, il recovery usa ordinamenti ufficiali (`Title Asc`, `Title Desc`, `ReleaseDate desc`, `MostPopular desc`) e aggiunge solo gli ID necessari a raggiungere quel totale.

Per ridurre scope o tempi:

```bash
python3 fetch_bigids.py --source browse
python3 fetch_bigids.py --source xbox
python3 fetch_bigids.py --source store --store-pages 10
python3 fetch_bigids.py --source combined --browse-delay 0
```

### Xbox Browse endpoint

Pagina sorgente:

```text
https://www.xbox.com/it-IT/games/browse
```

Endpoint rilevato via DevTools/Playwright:

```text
POST https://emerald.xboxservices.com/xboxcomfd/browse?locale=it-IT
```

Payload base per le pagine successive:

```json
{
  "Filters": "e30=",
  "ReturnFilters": false,
  "ChannelKeyToBeUsedInResponse": "BROWSE_CHANNELID=_FILTERS=",
  "EncodedCT": "<continuation token>",
  "ChannelId": ""
}
```

Il primo `EncodedCT` arriva da `window.__PRELOADED_STATE__`. Ogni risposta contiene:

| Campo | Uso |
| --- | --- |
| `channels["BROWSE_CHANNELID=_FILTERS="].products[].productId` | ProductId pagina corrente |
| `totalItems` | Target dichiarato dal sito |
| `encodedCT` | Continuation token successivo |

Osservazione tecnica: il canale base puo chiudersi con `HasMore=false` prima del `totalItems`. Per questo il merge usa anche fonti legacy e recovery da ordinamenti Browse.

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
  "total": 16483,
  "categories": {
    "xboxBrowse": {
      "label": "Xbox Browse - All games",
      "count": 14986,
      "ids": []
    },
    "xboxBrowseRecovery": {
      "label": "Xbox Browse - Sort recovery",
      "count": 221,
      "ids": []
    },
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

`html_builder.build_html()` produce una pagina statica con:

- CSS inline;
- JavaScript inline senza dipendenze;
- ricerca debounced;
- filtri dinamici generati dai dati reali;
- ordinamento numerico del prezzo con `data-price-num`;
- stato prezzo esplicito (`paid`, `free`, `unknown`) per distinguere gratis da prezzo non disponibile;
- categorie sorgente multiple tramite `source_categories`, mantenendo `source_category` come valore primario compatibile;
- card interamente cliccabile tramite `https://www.xbox.com/games/store/-/<ProductId>`;
- filtri/badge disposti in 10 colonne su desktop e in una colonna su mobile.

Attributi principali delle card:

```html
<a class="game-card"
     href="https://www.xbox.com/games/store/-/<ProductId>"
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
- verifica che il numero di BigId sia sopra la soglia minima (default 16000);
- verifica che il Browse endpoint restituisca almeno 15000 ID;
- esegue uno scrape piccolo su `xboxOG`;
- verifica che il JSON generato contenga risultati.

Questo workflow non committa modifiche.
Quando lanciato manualmente, permette di configurare le soglie minime `min_bigids` e `min_xboxog_games`; la schedule usa i default.

### Scrape settimanale

`.github/workflows/scrape.yml`

- gira ogni lunedi;
- prova discovery dei BigId;
- usa fallback sul `bigids.json` versionato se la discovery fallisce;
- rigenera `games.json` e `index.html`;
- genera e carica come artifact `scrape-report.json` con conteggi e liste di giochi aggiunti/rimossi;
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
| Xbox cambia endpoint Browse o payload `encodedCT` | `verify.yml` fallisce sulla soglia Browse |
| Display Catalog API cambia endpoint o schema | `verify.yml` fallisce sullo scrape campione |
| Rate limit Microsoft | batch configurabile, delay, workers limitati, retry/backoff |
| Prodotti delisted o non restituiti | tracciati come `missing_ids`, non considerati errore fatale |

## Roadmap residua

| Priorita | Attivita |
| --- | --- |
| Media | Monitorare se il recovery Browse resta necessario o se Xbox stabilizza la paginazione base |
| Media | Separare template HTML da logica Python se la UI cresce |

## Comandi di verifica locale

```bash
python3 -m py_compile fetch_bigids.py fetch_xbox_og.py html_builder.py scraper_utils.py tests/test_scraper.py
python3 -m unittest discover -s tests
python3 fetch_bigids.py --browse-delay 0 --out /tmp/bigids_verify.json
python3 fetch_xbox_og.py --ids /tmp/bigids_verify.json --category xboxOG --out /tmp/xbox_verify.html --json-out /tmp/xbox_verify.json
```
