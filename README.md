# Xbox Scraper

Scraper statico per costruire e pubblicare un catalogo navigabile dei giochi Xbox esposti dal catalogo pubblico Microsoft/Xbox.

Il progetto recupera i BigId dai bundle JavaScript della pagina Xbox, interroga la Microsoft Display Catalog API, genera `games.json` e produce un `index.html` statico con ricerca, filtri e ordinamento. Non scarica giochi o contenuti protetti: usa solo metadati pubblici di catalogo.

## Stato

Ultimo snapshot locale verificato:

| File | Stato |
| --- | --- |
| `bigids.json` | 4277 BigId unici |
| `games.json` | 4276 giochi estratti |
| `index.html` | catalogo statico generato per mercato IT |

Categorie rilevate in `bigids.json`:

| Categoria | BigId |
| --- | ---: |
| Xbox Original (OG) | 61 |
| Xbox 360 | 663 |
| Xbox One | 4184 |
| FPS Boost Series X | 131 |
| FPS Boost Series S | 120 |
| Auto HDR | 5 |
| Starting at... | 333 |

## File principali

| File | Scopo |
| --- | --- |
| `fetch_bigids.py` | Scopre il bundle Xbox e genera `bigids.json` |
| `fetch_xbox_og.py` | Interroga Display Catalog API e genera `games.json` + `index.html` |
| `scraper_utils.py` | Utility condivise per HTTP, SSL, retry/backoff e MS-CV |
| `bigids.json` | Elenco BigId per categoria |
| `games.json` | Dati normalizzati dei giochi |
| `index.html` | Interfaccia statica pubblicabile su GitHub Pages |
| `Analisi_Tecnica_Estrazione_Giochi_Xbox.md` | Note tecniche e roadmap |

## Requisiti

- Python 3.11 o superiore consigliato
- Nessuna dipendenza Python esterna

Il codice usa solo la standard library (`urllib`, `json`, `ssl`, `argparse`, `concurrent.futures`, ecc.).

## Uso rapido

Scoprire o aggiornare i BigId:

```bash
python3 fetch_bigids.py --out bigids.json
```

Generare catalogo completo per il mercato italiano:

```bash
python3 fetch_xbox_og.py \
  --category all \
  --batch 50 \
  --delay 0.3 \
  --workers 3 \
  --out index.html \
  --json-out games.json
```

Generare solo una categoria:

```bash
python3 fetch_xbox_og.py --category xboxOG --out index.html --json-out games.json
python3 fetch_xbox_og.py --category xbox360 --out index.html --json-out games.json
python3 fetch_xbox_og.py --category fullXboxOne --out index.html --json-out games.json
```

Se `--category` viene omesso in un terminale interattivo, lo script mostra un menu di selezione. In ambienti non interattivi usa `all`.

Filtro mercato da suffissi `<exc>` della mappa URL legacy:

```bash
python3 fetch_xbox_og.py --category all --filter-market --market IT
```

Il filtro viene applicato solo agli ID coperti da una mappa URL con suffissi `<exc>`, tipicamente `xcat-bi-urls2.json`.

## Categorie supportate

Le chiavi dipendono dal bundle Xbox e sono salvate in `bigids.json`.

| Chiave | Descrizione |
| --- | --- |
| `all` | Tutti i BigId unici |
| `xboxOG` | Xbox Original |
| `xbox360` | Xbox 360 |
| `fullXboxOne` | Catalogo Xbox One |
| `fpsBoostSeriesX` | FPS Boost Series X |
| `fpsBoostSeriesS` | FPS Boost Series S |
| `autoHDR` | Auto HDR |
| `startingat` | Raccolta promozionale "Starting at..." |

## Output HTML

`index.html` e autosufficiente e include:

- ricerca per titolo;
- filtro per console/categoria sorgente;
- filtro per genere;
- ordinamento per nome, prezzo e console;
- card con immagine, prezzo, tag, ID prodotto e link allo store Xbox.

## Automazioni GitHub

| Workflow | Frequenza | Scopo |
| --- | --- | --- |
| `.github/workflows/ci.yml` | push/PR | Compilazione sintattica Python |
| `.github/workflows/verify.yml` | giornaliera + manuale | Health check su codice, discovery bundle e API Display Catalog |
| `.github/workflows/scrape.yml` | settimanale + manuale | Aggiorna `bigids.json`, `games.json` e `index.html`, poi committa se cambiano |
| `.github/workflows/pages.yml` | push su `master` | Pubblica GitHub Pages quando cambiano `index.html` o `games.json` |

## Verifiche locali

Controllo sintassi:

```bash
python3 -m py_compile fetch_bigids.py fetch_xbox_og.py scraper_utils.py
```

Test unitari offline:

```bash
python3 -m unittest discover -s tests
```

Verifica discovery bundle senza modificare i dati versionati:

```bash
python3 fetch_bigids.py --out /tmp/bigids_verify.json
```

Verifica API su una categoria piccola:

```bash
python3 fetch_xbox_og.py \
  --ids bigids.json \
  --category xboxOG \
  --batch 50 \
  --delay 0.3 \
  --workers 2 \
  --out /tmp/xbox_verify.html \
  --json-out /tmp/xbox_verify.json
```

## Note operative

- `failed_ids.json` viene creato solo se alcuni batch falliscono ed e ignorato da Git.
- `--resume` ritenta gli ID salvati in `failed_ids.json`.
- `--no-verify-ssl` serve solo come fallback diagnostico.
- `--filter-market` richiede una mappa URL legacy con suffissi `<exc>`; se la copertura e parziale, lo script lo segnala nei log.
- `--batch` accetta valori da 1 a 50; `--workers` accetta valori da 1 a 10.
- Il batch predefinito e `50`; per ridurre pressione sull'API aumentare `--delay` o ridurre `--workers`.

## Licenza

MIT
