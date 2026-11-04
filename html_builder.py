"""Generazione HTML statica per il catalogo Xbox.

`index.html` è solo la shell. I giochi stanno in `data/games-NNN.json` e
vengono caricati dal browser, così la pagina non include più tutto il catalogo.
"""

import hashlib
import html as html_escape
import json
import re
from pathlib import Path

SHARD_SIZE = 500


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def write_game_shards(
    games: list[dict],
    directory: Path,
    shard_size: int = SHARD_SIZE,
) -> list[str]:
    """Scrive i giochi in JSON compatti e ritorna i path relativi alla pagina.

    L'ordine è alfabetico per titolo. Nessun campo viene scartato.
    Il query string `v` cambia quando cambia il contenuto, così la cache
    della pagina non serve shard vecchi dopo uno scrape.
    """
    if shard_size < 1:
        raise ValueError("shard_size deve essere almeno 1")

    directory.mkdir(parents=True, exist_ok=True)
    ordered = sorted(games, key=lambda g: (g.get("title") or "").lower())
    payloads: list[str] = []
    for start in range(0, len(ordered), shard_size):
        payloads.append(
            json.dumps(
                ordered[start:start + shard_size],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    digest = hashlib.sha256()
    for payload in payloads:
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
    revision = digest.hexdigest()[:10] if payloads else "empty"

    names: list[str] = []
    for index, payload in enumerate(payloads):
        name = f"games-{index:03d}.json"
        (directory / name).write_text(payload, encoding="utf-8")
        names.append(name)
    for stale in directory.glob("games-*.json"):
        if stale.name not in names:
            stale.unlink()
    return [f"data/{name}?v={revision}" for name in names]


def _pills(items: list[str], filter_type: str, all_label: str) -> str:
    pills = (
        f'<button type="button" class="pill active" data-filter-{filter_type}="all" '
        f'aria-pressed="true">{all_label}</button>\n'
    )
    for item in items:
        slug = slugify(item)
        esc = html_escape.escape(item)
        pills += (
            f'    <button type="button" class="pill" data-filter-{filter_type}="{slug}" '
            f'aria-pressed="false">{esc}</button>\n'
        )
    return pills


def _stats_html(games: list[dict]) -> str:
    priced = [g for g in games if (g.get("price_num") or 0) > 0]
    avg_price = sum(g["price_num"] for g in priced) / len(priced) if priced else 0
    free_count = sum(1 for g in games if g.get("price_status") == "free")
    unknown_price_count = sum(1 for g in games if g.get("price_status", "unknown") == "unknown")
    cat_counts: dict[str, int] = {}
    for game in games:
        cats = game.get("source_categories") or (
            [game["source_category"]] if game.get("source_category") else []
        )
        for cat in cats or ["Sconosciuto"]:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
    stats_items = "".join(
        '<div class="stat-item"><div class="stat-num">'
        f"{count}</div><div class=\"stat-lbl\">{html_escape.escape(cat)}</div></div>"
        for cat, count in sorted(cat_counts.items(), key=lambda item: -item[1])
    )
    return f"""
    <div class="stats-bar">
      <div class="stat-item"><div class="stat-num">{len(games)}</div><div class="stat-lbl">Totale</div></div>
      <div class="stat-item"><div class="stat-num">{free_count}</div><div class="stat-lbl">Gratis</div></div>
      <div class="stat-item"><div class="stat-num">{unknown_price_count}</div><div class="stat-lbl">Prezzo N/D</div></div>
      <div class="stat-item"><div class="stat-num">{avg_price:.2f}</div><div class="stat-lbl">Prezzo medio</div></div>
      {stats_items}
    </div>"""


_PAGE_JS = r"""
var SHARDS = JSON.parse(document.getElementById("shard-manifest").textContent);
var PAGE_SIZE = 24;
var activeCat = "all";
var activeGenre = "all";
var games = [];
var filtered = [];
var renderLimit = PAGE_SIZE;
var loading = true;
var debounceTimer = 0;
var refreshTimer = 0;
var booted = false;
var lastKey = "";

function slugify(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

function categoryLabels(game) {
  if (game.source_categories && game.source_categories.length) return game.source_categories;
  if (game.source_category) return [game.source_category];
  return [];
}

function categorySlugs(game) {
  if (game._slugs) return game._slugs;
  var labels = categoryLabels(game);
  var slugs = [];
  for (var i = 0; i < labels.length; i++) {
    var slug = slugify(labels[i]);
    if (slug) slugs.push(slug);
  }
  game._slugs = slugs.length ? slugs : ["unknown"];
  return game._slugs;
}

function primarySlug(game) {
  if (!game._primary) {
    game._primary = slugify(game.source_category || "") || categorySlugs(game)[0];
  }
  return game._primary;
}

function genreSlug(game) {
  if (!game._genre) game._genre = slugify(game.genre || "") || "unknown";
  return game._genre;
}

function titleOf(game) {
  return game.title || "";
}

function priceValue(game) {
  var value = Number(game.price_num);
  return isFinite(value) ? value : 0;
}

function setStatus(text) {
  var el = document.getElementById("load-status");
  el.textContent = text || "";
  document.getElementById("grid").setAttribute("aria-busy", text ? "true" : "false");
}

function showError(text) {
  var el = document.getElementById("load-error");
  el.textContent = text || "";
  el.style.display = text ? "block" : "none";
}

function renderCard(game) {
  var title = titleOf(game) || game.id || "Gioco";
  var url = game.url || ("https://www.xbox.com/games/store/-/" + encodeURIComponent(game.id || ""));
  var card = document.createElement("a");
  card.className = "game-card";
  card.setAttribute("role", "listitem");
  card.href = url;
  card.target = "_blank";
  card.rel = "noopener noreferrer";

  var wrap = document.createElement("div");
  wrap.className = "img-wrap";
  if (game.img) {
    var img = document.createElement("img");
    img.src = game.img;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", function () {
      var placeholder = document.createElement("div");
      placeholder.className = "no-img";
      placeholder.setAttribute("aria-hidden", "true");
      placeholder.textContent = "\uD83C\uDFAE";
      img.replaceWith(placeholder);
    });
    wrap.appendChild(img);
  } else {
    var placeholder = document.createElement("div");
    placeholder.className = "no-img";
    placeholder.setAttribute("aria-hidden", "true");
    placeholder.textContent = "\uD83C\uDFAE";
    wrap.appendChild(placeholder);
  }

  var body = document.createElement("div");
  body.className = "card-body";
  var titleEl = document.createElement("div");
  titleEl.className = "card-title";
  titleEl.textContent = title;
  var hint = document.createElement("span");
  hint.className = "sr-only";
  hint.textContent = " (si apre in una nuova scheda)";
  titleEl.appendChild(hint);

  var tags = document.createElement("div");
  tags.className = "card-tags";
  var labels = categoryLabels(game);
  if (!labels.length) labels = ["\u2014"];
  for (var i = 0; i < labels.length; i++) {
    var tag = document.createElement("span");
    tag.className = "tag tag-cat";
    tag.textContent = labels[i];
    tags.appendChild(tag);
  }
  var genre = document.createElement("span");
  genre.className = "tag tag-genre";
  genre.textContent = game.genre || "\u2014";
  tags.appendChild(genre);

  var meta = document.createElement("div");
  meta.className = "card-meta";
  var price = document.createElement("span");
  price.className = "card-price";
  price.textContent = game.price || "\u2014";
  meta.appendChild(price);
  var idEl = document.createElement("div");
  idEl.className = "card-id";
  idEl.textContent = game.id || "";

  body.appendChild(titleEl);
  body.appendChild(tags);
  body.appendChild(meta);
  body.appendChild(idEl);
  card.appendChild(wrap);
  card.appendChild(body);
  return card;
}

function updateMore() {
  var more = document.getElementById("more");
  var shown = Math.min(renderLimit, filtered.length);
  if (shown < filtered.length) {
    more.hidden = false;
    more.textContent = "Mostra altri (" + (filtered.length - shown) + ")";
  } else {
    more.hidden = true;
  }
}

function visibleKey() {
  var count = Math.min(renderLimit, filtered.length);
  var ids = [];
  for (var i = 0; i < count; i++) ids.push(filtered[i].id);
  return ids.join("|");
}

function paint() {
  var grid = document.getElementById("grid");
  var count = Math.min(renderLimit, filtered.length);
  var fragment = document.createDocumentFragment();
  for (var i = 0; i < count; i++) fragment.appendChild(renderCard(filtered[i]));
  grid.textContent = "";
  grid.appendChild(fragment);
  updateMore();
}

function applyFilters(resetWindow) {
  if (resetWindow) renderLimit = PAGE_SIZE;
  var query = document.getElementById("search").value.toLowerCase().trim();
  var sort = document.getElementById("sort").value;
  var next = [];
  for (var i = 0; i < games.length; i++) {
    var game = games[i];
    var title = titleOf(game).toLowerCase();
    var slugs = categorySlugs(game);
    if (query && title.indexOf(query) === -1) continue;
    if (activeCat !== "all" && slugs.indexOf(activeCat) === -1) continue;
    if (activeGenre !== "all" && genreSlug(game) !== activeGenre) continue;
    next.push(game);
  }
  var byTitle = function (a, b) { return titleOf(a).localeCompare(titleOf(b), "it", {sensitivity: "base"}); };
  if (sort === "name-desc") next.sort(function (a, b) { return byTitle(b, a); });
  else if (sort === "price-asc") next.sort(function (a, b) { return priceValue(a) - priceValue(b) || byTitle(a, b); });
  else if (sort === "price-desc") next.sort(function (a, b) { return priceValue(b) - priceValue(a) || byTitle(a, b); });
  else if (sort === "cat") next.sort(function (a, b) { return primarySlug(a).localeCompare(primarySlug(b)) || byTitle(a, b); });
  else next.sort(byTitle);
  filtered = next;
  document.getElementById("vis-count").textContent = String(filtered.length);
  document.getElementById("no-results").style.display = (filtered.length === 0 && !loading) ? "block" : "none";
  var key = visibleKey();
  if (resetWindow || key !== lastKey) {
    lastKey = key;
    paint();
  } else {
    updateMore();
  }
}

function showMore() {
  var more = document.getElementById("more");
  if (more.hidden || renderLimit >= filtered.length) return;
  renderLimit = Math.min(filtered.length, renderLimit + PAGE_SIZE);
  lastKey = "";
  applyFilters(false);
}

function scheduleRefresh() {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(function () { applyFilters(false); }, 60);
}

async function fetchShard(index, buffers) {
  var url = SHARDS[index];
  var res = await fetch(url, {credentials: "same-origin"});
  if (!res.ok) throw new Error("HTTP " + res.status + " per " + String(url).split("?")[0]);
  var data = await res.json();
  if (!Array.isArray(data)) throw new Error("Formato non valido in " + String(url).split("?")[0]);
  buffers[index] = data;
}

function commit(buffers, state) {
  var grew = false;
  while (buffers[state.next]) {
    var chunk = buffers[state.next];
    for (var i = 0; i < chunk.length; i++) games.push(chunk[i]);
    buffers[state.next] = null;
    state.next += 1;
    grew = true;
  }
  if (!grew) return;
  if (!booted) {
    booted = true;
    applyFilters(true);
  } else {
    scheduleRefresh();
  }
}

async function loadCatalog() {
  if (!SHARDS.length) {
    loading = false;
    setStatus("");
    applyFilters(true);
    return;
  }
  var buffers = new Array(SHARDS.length);
  var state = {next: 0};
  var loaded = 0;
  setStatus("Caricamento catalogo 0/" + SHARDS.length);
  async function pull(index) {
    await fetchShard(index, buffers);
    loaded += 1;
    setStatus("Caricamento catalogo " + loaded + "/" + SHARDS.length);
    commit(buffers, state);
  }
  try {
    await pull(0);
    var nextIndex = 1;
    async function worker() {
      while (nextIndex < SHARDS.length) {
        var index = nextIndex++;
        await pull(index);
      }
    }
    var workers = [];
    var pool = Math.min(4, Math.max(0, SHARDS.length - 1));
    for (var w = 0; w < pool; w++) workers.push(worker());
    await Promise.all(workers);
    clearTimeout(refreshTimer);
    loading = false;
    setStatus("");
    applyFilters(false);
  } catch (error) {
    clearTimeout(refreshTimer);
    loading = false;
    setStatus("");
    showError("Una parte del catalogo non si è caricata. Ricarica la pagina. (" + error.message + ")");
    applyFilters(false);
  }
}

document.querySelector(".controls").addEventListener("click", function (event) {
  var pill = event.target.closest(".pill");
  if (!pill) return;
  var type = pill.hasAttribute("data-filter-cat") ? "cat" : "genre";
  var value = pill.getAttribute("data-filter-" + type);
  var group = document.querySelectorAll("[data-filter-" + type + "]");
  for (var i = 0; i < group.length; i++) {
    group[i].classList.remove("active");
    group[i].setAttribute("aria-pressed", "false");
  }
  pill.classList.add("active");
  pill.setAttribute("aria-pressed", "true");
  if (type === "cat") activeCat = value;
  else activeGenre = value;
  applyFilters(true);
});

document.getElementById("search").addEventListener("input", function () {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(function () { applyFilters(true); }, 200);
});
document.getElementById("sort").addEventListener("change", function () { applyFilters(true); });

if (!window.matchMedia("(max-width: 600px)").matches) {
  var filterBlocks = document.querySelectorAll(".filter-details");
  for (var f = 0; f < filterBlocks.length; f++) filterBlocks[f].open = true;
}

var moreButton = document.getElementById("more");
moreButton.addEventListener("click", showMore);
if ("IntersectionObserver" in window) {
  var observer = new IntersectionObserver(function (entries) {
    if (entries.some(function (entry) { return entry.isIntersecting; })) showMore();
  }, {rootMargin: "200px"});
  observer.observe(moreButton);
}

loadCatalog();
"""


def build_html(
    games: list[dict],
    market: str,
    category_label: str,
    shard_paths: list[str] | None = None,
) -> str:
    if shard_paths is None:
        shard_count = (len(games) + SHARD_SIZE - 1) // SHARD_SIZE if games else 0
        shard_paths = [f"data/games-{index:03d}.json" for index in range(shard_count)]

    all_source_cats = sorted({
        cat
        for g in games
        for cat in (
            g.get("source_categories")
            or ([g["source_category"]] if g.get("source_category") else [])
        )
        if cat
    })
    all_genres = sorted({g["genre"] for g in games if g.get("genre")})
    cat_pills = _pills(all_source_cats, "cat", "Tutte le console")
    genre_pills = _pills(all_genres, "genre", "Tutti i generi")
    stats_html = _stats_html(games)
    total = len(games)
    safe_market = html_escape.escape(market)
    safe_label = html_escape.escape(category_label)
    shards_json = (
        json.dumps(shard_paths, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Catalogo navigabile dei giochi Xbox per il mercato {safe_market}.">
<title>Xbox — {total} giochi ({safe_label} · {safe_market})</title>
<link rel="canonical" href="https://scraper.byluke.org/">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Ccircle cx='8' cy='8' r='6' fill='%2300e676'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&amp;family=Share+Tech+Mono&amp;display=swap" rel="stylesheet">
<style>
  :root {{ --green:#00e676; --green-dim:#00b357; --dark:#060a0e; --panel:#0c1318; --border:#1a2a1a; --text:#c8e6c9; --muted:#7a9a7a; --pill-bg:#0f1f0f; --font:'Rajdhani','Segoe UI',sans-serif; --mono:'Share Tech Mono',ui-monospace,monospace; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html {{ color-scheme:dark; }}
  body {{ background:var(--dark); color:var(--text); font-family:var(--font); min-height:100vh; }}
  body::before {{ content:''; position:fixed; inset:0; background-image:linear-gradient(rgba(0,230,118,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,230,118,.03) 1px,transparent 1px); background-size:40px 40px; pointer-events:none; z-index:0; }}
  .skip {{ position:absolute; left:12px; top:-48px; z-index:5; background:var(--green); color:var(--dark); padding:8px 12px; font-family:var(--mono); text-decoration:none; }}
  .skip:focus {{ top:12px; }}
  .sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }}
  header {{ position:relative; z-index:1; padding:28px 40px 18px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; gap:20px; flex-wrap:wrap; }}
  .logo {{ display:flex; align-items:center; gap:16px; min-width:0; }}
  .ring {{ width:48px; height:48px; flex:0 0 auto; border-radius:50%; border:2px solid var(--green); display:flex; align-items:center; justify-content:center; box-shadow:0 0 20px rgba(0,230,118,.3); font-size:1.3rem; animation:pulse 3s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100%{{box-shadow:0 0 20px rgba(0,230,118,.3)}} 50%{{box-shadow:0 0 40px rgba(0,230,118,.55)}} }}
  h1 {{ font-size:1.8rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; overflow-wrap:anywhere; }}
  h1 span {{ color:var(--green); }}
  .sub {{ font-family:var(--mono); font-size:.65rem; color:var(--muted); letter-spacing:.2em; margin-top:3px; }}
  .count {{ font-family:var(--mono); font-size:.85rem; color:var(--green); }}
  .controls {{ position:relative; z-index:1; padding:12px 40px; border-bottom:1px solid var(--border); display:flex; flex-direction:column; gap:10px; }}
  .ctrl-row {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .search-wrap {{ flex:1; min-width:180px; max-width:340px; position:relative; }}
  .search-wrap input {{ width:100%; background:var(--panel); border:1px solid var(--border); color:var(--text); padding:8px 12px 8px 32px; font-family:var(--mono); font-size:.82rem; outline:none; transition:border-color .2s; }}
  .search-wrap input:focus {{ border-color:var(--green); }}
  .search-wrap input::placeholder {{ color:var(--muted); }}
  .si {{ position:absolute; left:9px; top:50%; transform:translateY(-50%); color:var(--muted); font-size:.9rem; pointer-events:none; }}
  select {{ background:var(--panel); border:1px solid var(--border); color:var(--text); padding:8px 12px; font-family:var(--mono); font-size:.78rem; outline:none; cursor:pointer; max-width:100%; }}
  .filter-details:not([open]) > .filter-row {{ display:none; }}
  .filter-details > summary {{ font-family:var(--mono); font-size:16px; min-height:44px; display:flex; align-items:center; cursor:pointer; color:var(--text); }}
  .filter-details > summary:focus-visible {{ outline:2px solid var(--green); outline-offset:2px; }}
  .filter-row {{ display:flex; align-items:start; gap:8px; }}
  .filter-label {{ font-family:var(--mono); font-size:.65rem; color:var(--muted); letter-spacing:.1em; white-space:nowrap; padding-top:6px; }}
  .pill-grid {{ flex:1; min-width:0; display:grid; grid-template-columns:repeat(10,minmax(0,1fr)); gap:6px; }}
  .pill {{ width:100%; min-height:28px; background:var(--pill-bg); border:1px solid var(--border); color:var(--muted); padding:4px 8px; font-family:var(--mono); font-size:.62rem; cursor:pointer; transition:all .15s; overflow-wrap:anywhere; }}
  .pill:hover {{ border-color:var(--green-dim); color:var(--text); }}
  .pill.active {{ background:var(--green-dim); border-color:var(--green); color:var(--dark); font-weight:600; }}
  .pill:focus-visible, .search-wrap input:focus-visible, select:focus-visible {{ outline:2px solid var(--green); outline-offset:2px; }}
  .load-status {{ position:relative; z-index:1; padding:8px 40px 0; font-family:var(--mono); font-size:.75rem; color:var(--green); }}
  .load-status:empty {{ display:none; }}
  .no-results {{ display:none; position:relative; z-index:1; padding:28px 40px; text-align:center; font-family:var(--mono); color:var(--muted); overflow-wrap:anywhere; }}
  .game-grid {{ position:relative; z-index:1; padding:18px 40px 24px; display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; }}
  .game-card {{ position:relative; display:block; background:var(--panel); border:1px solid var(--border); color:var(--text); text-decoration:none; overflow:hidden; transition:border-color .2s,transform .15s; content-visibility:auto; contain-intrinsic-size:200px 280px; }}
  .game-card:hover {{ border-color:var(--green-dim); transform:translateY(-3px); }}
  .game-card:focus-visible {{ outline:2px solid var(--green); outline-offset:2px; }}
  .img-wrap {{ width:100%; aspect-ratio:16/9; background:#0a180a; overflow:hidden; }}
  .img-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .no-img {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:2rem; color:var(--border); }}
  .card-body {{ padding:9px 11px; }}
  .card-title {{ font-size:.88rem; font-weight:600; line-height:1.3; margin-bottom:5px; overflow-wrap:anywhere; }}
  .card-tags {{ display:flex; gap:4px; flex-wrap:wrap; margin-bottom:5px; }}
  .tag {{ font-family:var(--mono); font-size:.52rem; padding:2px 5px; overflow-wrap:anywhere; }}
  .tag-cat {{ background:#001a0d; border:1px solid var(--green-dim); color:var(--green); }}
  .tag-genre {{ background:#0a0a1a; border:1px solid #334; color:#8899bb; }}
  .card-meta {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
  .card-price {{ font-family:var(--mono); font-size:.7rem; color:var(--green); }}
  .card-id {{ font-family:var(--mono); font-size:.55rem; color:var(--muted); margin-top:3px; overflow-wrap:anywhere; }}
  .stats-bar {{ position:relative; z-index:1; display:flex; gap:2px; padding:10px 40px; border-bottom:1px solid var(--border); flex-wrap:wrap; }}
  .stat-item {{ background:var(--panel); border:1px solid var(--border); padding:8px 14px; text-align:center; min-width:80px; max-width:100%; }}
  .stat-num {{ font-family:var(--mono); font-size:1.1rem; color:var(--green); font-weight:700; }}
  .stat-lbl {{ font-family:var(--mono); font-size:.55rem; color:var(--muted); letter-spacing:.05em; margin-top:2px; overflow-wrap:anywhere; }}
  .more-wrap {{ position:relative; z-index:1; display:flex; justify-content:center; padding:0 40px 48px; }}
  #more {{ min-height:44px; padding:10px 18px; background:var(--green-dim); border:1px solid var(--green); color:var(--dark); font-family:var(--mono); font-size:.8rem; font-weight:600; cursor:pointer; max-width:100%; }}
  #more:focus-visible {{ outline:2px solid var(--green); outline-offset:2px; }}
  [hidden] {{ display:none !important; }}
  @media (min-width:601px) {{
    .filter-details > summary {{ display:none; }}
    .filter-details > .filter-row {{ display:flex !important; }}
  }}
  @media (max-width:900px) {{
    .pill-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
  }}
  @media (max-width:600px) {{
    .stats-bar {{ padding-left:max(16px, env(safe-area-inset-left)); padding-right:max(16px, env(safe-area-inset-right)); }}
    header,.controls,.game-grid,.load-status,.no-results,.more-wrap {{ padding-left:max(16px, env(safe-area-inset-left)); padding-right:max(16px, env(safe-area-inset-right)); }}
    h1 {{ font-size:1.4rem; }}
    .game-grid {{ grid-template-columns:repeat(auto-fill,minmax(min(100%,150px),1fr)); }}
    .filter-row {{ flex-direction:column; align-items:stretch; }}
    .filter-label {{ display:none; }}
    .pill-grid {{ grid-template-columns:1fr; }}
    .ctrl-row {{ flex-direction:column; align-items:stretch; }}
    .search-wrap, select {{ max-width:none; width:100%; }}
    .search-wrap input, select, .pill {{ min-height:44px; font-size:16px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .ring {{ animation:none; }}
    .game-card,.pill,.search-wrap input {{ transition:none; }}
  }}
</style>
</head>
<body>
<a class="skip" href="#contenuto">Vai al catalogo</a>
<header>
  <div class="logo">
    <div class="ring" aria-hidden="true">&#x1f3ae;</div>
    <div>
      <h1>Xbox <span>Catalog</span></h1>
      <div class="sub">{safe_label.upper()} &middot; {safe_market}</div>
    </div>
  </div>
  <div class="count"><span id="vis-count">0</span> / <span id="total-count">{total}</span> giochi</div>
</header>
{stats_html}
<div class="controls">
  <div class="ctrl-row">
    <div class="search-wrap" role="search">
      <span class="si" aria-hidden="true">&#x2315;</span>
      <input type="text" id="search" placeholder="Cerca titolo..." aria-label="Cerca gioco per titolo" autocomplete="off">
    </div>
    <select id="sort" aria-label="Ordina per">
      <option value="name-asc">Nome A-Z</option>
      <option value="name-desc">Nome Z-A</option>
      <option value="price-asc">Prezzo crescente</option>
      <option value="price-desc">Prezzo decrescente</option>
      <option value="cat">Console</option>
    </select>
  </div>
  <details class="filter-details">
    <summary>Console</summary>
    <div class="filter-row" role="group" aria-label="Filtra per console">
      <span class="filter-label">CONSOLE:</span>
      <div class="pill-grid">{cat_pills}    </div>
    </div>
  </details>
  <details class="filter-details">
    <summary>Genere</summary>
    <div class="filter-row" role="group" aria-label="Filtra per genere">
      <span class="filter-label">GENERE:</span>
      <div class="pill-grid">{genre_pills}    </div>
    </div>
  </details>
</div>
<main id="contenuto">
<p id="load-status" class="load-status" aria-hidden="true"></p>
<div class="no-results" id="no-results" aria-live="polite">// nessun risultato</div>
<p id="load-error" class="no-results" role="alert"></p>
<div class="game-grid" id="grid" role="list" aria-label="Catalogo giochi" aria-busy="true"></div>
<div class="more-wrap"><button type="button" id="more" hidden>Mostra altri</button></div>
<noscript><p class="no-results" style="display:block">Attiva JavaScript per sfogliare il catalogo. I dati restano nei file data/games-*.json.</p></noscript>
</main>
<script type="application/json" id="shard-manifest">{shards_json}</script>
<script>
{_PAGE_JS}
</script>
</body>
</html>"""
