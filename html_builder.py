"""Generazione HTML statica per il catalogo Xbox."""

import html as html_escape
import re


def slugify(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def build_html(games: list[dict], market: str, category_label: str) -> str:
    # Raccogli valori unici per filtri
    all_source_cats = sorted({
        cat
        for g in games
        for cat in (g.get("source_categories") or ([g["source_category"]] if g.get("source_category") else []))
        if cat
    })
    all_genres = sorted({g["genre"] for g in games if g["genre"]})

    def make_pills(items: list[str], filter_type: str, all_label: str) -> str:
        pills = f'<button class="pill active" data-filter-{filter_type}="all">{all_label}</button>\n'
        for item in items:
            slug = slugify(item)
            esc = html_escape.escape(item)
            pills += f'    <button class="pill" data-filter-{filter_type}="{slug}">{esc}</button>\n'
        return pills

    cat_pills = make_pills(all_source_cats, "cat", "Tutte le console")
    genre_pills = make_pills(all_genres, "genre", "Tutti i generi")

    # Statistiche per dashboard
    priced_games = [g for g in games if g["price_num"] > 0]
    avg_price = sum(g["price_num"] for g in priced_games) / len(priced_games) if priced_games else 0
    free_count = sum(1 for g in games if g.get("price_status") == "free")
    unknown_price_count = sum(1 for g in games if g.get("price_status", "unknown") == "unknown")
    cat_counts = {}
    for g in games:
        cats = g.get("source_categories") or ([g["source_category"]] if g.get("source_category") else [])
        for cat in cats or ["Sconosciuto"]:
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

    stats_items = "".join(
        f'<div class="stat-item"><div class="stat-num">{count}</div><div class="stat-lbl">{html_escape.escape(cat)}</div></div>'
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1])
    )
    stats_html = f"""
    <div class="stats-bar">
      <div class="stat-item"><div class="stat-num">{len(games)}</div><div class="stat-lbl">Totale</div></div>
      <div class="stat-item"><div class="stat-num">{free_count}</div><div class="stat-lbl">Gratis</div></div>
      <div class="stat-item"><div class="stat-num">{unknown_price_count}</div><div class="stat-lbl">Prezzo N/D</div></div>
      <div class="stat-item"><div class="stat-num">{avg_price:.2f}</div><div class="stat-lbl">Prezzo medio</div></div>
      {stats_items}
    </div>"""

    card_items: list[str] = []
    for g in sorted(games, key=lambda x: x["title"].lower()):
        t = html_escape.escape(g["title"])
        store_url = html_escape.escape(g.get("url", ""))
        img_tag = (
            f'<img src="{g["img"]}" alt="{t}" loading="lazy">'
            if g["img"] else '<div class="no-img">&#x1f3ae;</div>'
        )
        price_display = g["price"] or "—"
        source_categories = g.get("source_categories") or ([g["source_category"]] if g.get("source_category") else [])
        cat_slug = slugify(g["source_category"]) if g["source_category"] else "unknown"
        cat_slugs = " ".join(slugify(cat) for cat in source_categories) or "unknown"
        genre_slug = slugify(g["genre"]) if g["genre"] else "unknown"
        cat_tags = "".join(
            f'<span class="tag tag-cat">{html_escape.escape(cat)}</span>'
            for cat in source_categories
        ) or '<span class="tag tag-cat">—</span>'
        genre_label_esc = html_escape.escape(g["genre"]) if g["genre"] else "—"

        card_tag = "a" if store_url else "div"
        link_attrs = f' href="{store_url}" target="_blank" rel="noopener"' if store_url else ""

        card_items.append(f"""
        <{card_tag} class="game-card" role="listitem"{link_attrs}
             data-title="{t.lower()}"
             data-cat="{cat_slug}"
             data-cats="{cat_slugs}"
             data-genre="{genre_slug}"
             data-price-num="{g['price_num']:.2f}">
          <div class="img-wrap">{img_tag}</div>
          <div class="card-body">
            <div class="card-title">{t}</div>
            <div class="card-tags">
              {cat_tags}
              <span class="tag tag-genre">{genre_label_esc}</span>
            </div>
            <div class="card-meta">
              <span class="card-price">{price_display}</span>
            </div>
            <div class="card-id">{g["id"]}</div>
          </div>
        </{card_tag}>""")

    total = len(games)
    cards = "".join(card_items)

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Xbox — {total} giochi ({category_label} · {market})</title>
<link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600;700&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
  :root {{ --green:#00e676; --green-dim:#00b357; --dark:#060a0e; --panel:#0c1318; --border:#1a2a1a; --text:#c8e6c9; --muted:#7a9a7a; --pill-bg:#0f1f0f; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--dark); color:var(--text); font-family:'Rajdhani',sans-serif; min-height:100vh; }}
  body::before {{ content:''; position:fixed; inset:0; background-image:linear-gradient(rgba(0,230,118,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,230,118,.03) 1px,transparent 1px); background-size:40px 40px; pointer-events:none; z-index:0; }}
  header {{ position:relative; z-index:1; padding:28px 40px 18px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; gap:20px; flex-wrap:wrap; }}
  .logo {{ display:flex; align-items:center; gap:16px; }}
  .ring {{ width:48px; height:48px; border-radius:50%; border:2px solid var(--green); display:flex; align-items:center; justify-content:center; box-shadow:0 0 20px rgba(0,230,118,.3); font-size:1.3rem; animation:pulse 3s ease-in-out infinite; }}
  @keyframes pulse {{ 0%,100%{{box-shadow:0 0 20px rgba(0,230,118,.3)}} 50%{{box-shadow:0 0 40px rgba(0,230,118,.55)}} }}
  h1 {{ font-size:1.8rem; font-weight:700; letter-spacing:.1em; text-transform:uppercase; }}
  h1 span {{ color:var(--green); }}
  .sub {{ font-family:'Share Tech Mono',monospace; font-size:.65rem; color:var(--muted); letter-spacing:.2em; margin-top:3px; }}
  .count {{ font-family:'Share Tech Mono',monospace; font-size:.85rem; color:var(--green); white-space:nowrap; }}
  .controls {{ position:relative; z-index:1; padding:12px 40px; border-bottom:1px solid var(--border); display:flex; flex-direction:column; gap:10px; }}
  .ctrl-row {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
  .search-wrap {{ flex:1; min-width:180px; max-width:340px; position:relative; }}
  .search-wrap input {{ width:100%; background:var(--panel); border:1px solid var(--border); color:var(--text); padding:8px 12px 8px 32px; font-family:'Share Tech Mono',monospace; font-size:.82rem; outline:none; transition:border-color .2s; }}
  .search-wrap input:focus {{ border-color:var(--green); }}
  .search-wrap input::placeholder {{ color:var(--muted); }}
  .si {{ position:absolute; left:9px; top:50%; transform:translateY(-50%); color:var(--muted); font-size:.9rem; }}
  select {{ background:var(--panel); border:1px solid var(--border); color:var(--text); padding:8px 12px; font-family:'Share Tech Mono',monospace; font-size:.78rem; outline:none; cursor:pointer; }}
  .filter-row {{ display:flex; align-items:start; gap:8px; }}
  .filter-label {{ font-family:'Share Tech Mono',monospace; font-size:.65rem; color:var(--muted); letter-spacing:.1em; white-space:nowrap; }}
  .pill-grid {{ flex:1; display:grid; grid-template-columns:repeat(10,minmax(0,1fr)); gap:6px; }}
  .pill {{ width:100%; min-height:28px; background:var(--pill-bg); border:1px solid var(--border); color:var(--muted); padding:4px 8px; font-family:'Share Tech Mono',monospace; font-size:.62rem; cursor:pointer; transition:all .15s; overflow-wrap:anywhere; }}
  .pill:hover {{ border-color:var(--green-dim); color:var(--text); }}
  .pill.active {{ background:var(--green-dim); border-color:var(--green); color:var(--dark); font-weight:600; }}
  .pill:focus-visible {{ outline:2px solid var(--green); outline-offset:2px; }}
  .no-results {{ display:none; position:relative; z-index:1; padding:60px 40px; text-align:center; font-family:'Share Tech Mono',monospace; color:var(--muted); }}
  .game-grid {{ position:relative; z-index:1; padding:18px 40px 60px; display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:10px; }}
  .game-card {{ display:block; background:var(--panel); border:1px solid var(--border); color:var(--text); text-decoration:none; overflow:hidden; transition:border-color .2s,transform .15s; content-visibility:auto; contain-intrinsic-size:200px 280px; }}
  .game-card:hover {{ border-color:var(--green-dim); transform:translateY(-3px); }}
  .game-card:focus-visible {{ outline:2px solid var(--green); outline-offset:2px; }}
  .img-wrap {{ width:100%; aspect-ratio:16/9; background:#0a180a; overflow:hidden; }}
  .img-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .no-img {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:2rem; color:var(--border); }}
  .card-body {{ padding:9px 11px; }}
  .card-title {{ font-size:.88rem; font-weight:600; line-height:1.3; margin-bottom:5px; }}
  .card-tags {{ display:flex; gap:4px; flex-wrap:wrap; margin-bottom:5px; }}
  .tag {{ font-family:'Share Tech Mono',monospace; font-size:.52rem; padding:2px 5px; }}
  .tag-cat {{ background:#001a0d; border:1px solid var(--green-dim); color:var(--green); }}
  .tag-genre {{ background:#0a0a1a; border:1px solid #334; color:#8899bb; }}
  .card-meta {{ display:flex; align-items:center; justify-content:space-between; }}
  .card-price {{ font-family:'Share Tech Mono',monospace; font-size:.7rem; color:var(--green); }}
  .card-id {{ font-family:'Share Tech Mono',monospace; font-size:.55rem; color:var(--muted); margin-top:3px; }}
  .stats-bar {{ position:relative; z-index:1; display:flex; gap:2px; padding:10px 40px; border-bottom:1px solid var(--border); flex-wrap:wrap; }}
  .stat-item {{ background:var(--panel); border:1px solid var(--border); padding:8px 14px; text-align:center; min-width:80px; }}
  .stat-num {{ font-family:'Share Tech Mono',monospace; font-size:1.1rem; color:var(--green); font-weight:700; }}
  .stat-lbl {{ font-family:'Share Tech Mono',monospace; font-size:.55rem; color:var(--muted); letter-spacing:.05em; margin-top:2px; }}
  @media(max-width:600px) {{
    .stats-bar {{ padding-left:16px; padding-right:16px; }}
    header,.controls,.game-grid {{ padding-left:16px; padding-right:16px; }}
    h1 {{ font-size:1.4rem; }}
    .game-grid {{ grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); }}
    .filter-row {{ flex-direction:column; align-items:stretch; }}
    .filter-label {{ white-space:normal; }}
    .pill-grid {{ grid-template-columns:1fr; }}
  }}
</style>
</head>
<body>
<header>
  <div class="logo">
    <div class="ring">&#x1f3ae;</div>
    <div>
      <h1>Xbox <span>Catalog</span></h1>
      <div class="sub">{html_escape.escape(category_label).upper()} &middot; {market}</div>
    </div>
  </div>
  <div class="count"><span id="vis-count">{total}</span> / {total} giochi</div>
</header>

{stats_html}

<div class="controls" role="search">
  <div class="ctrl-row">
    <div class="search-wrap">
      <span class="si">&#x2315;</span>
      <input type="text" id="search" placeholder="Cerca titolo..." aria-label="Cerca gioco per titolo">
    </div>
    <select id="sort" aria-label="Ordina per">
      <option value="name-asc">Nome A-Z</option>
      <option value="name-desc">Nome Z-A</option>
      <option value="price-asc">Prezzo crescente</option>
      <option value="price-desc">Prezzo decrescente</option>
      <option value="cat">Console</option>
    </select>
  </div>
  <div class="filter-row" role="group" aria-label="Filtra per console">
    <span class="filter-label">CONSOLE:</span>
    <div class="pill-grid">{cat_pills}    </div>
  </div>
  <div class="filter-row" role="group" aria-label="Filtra per genere">
    <span class="filter-label">GENERE:</span>
    <div class="pill-grid">{genre_pills}    </div>
  </div>
</div>

<div class="no-results" id="no-results" aria-live="polite">// nessun risultato</div>
<div class="game-grid" id="grid" role="list" aria-label="Catalogo giochi">{cards}</div>

<script>
var activeCat = 'all';
var activeGenre = 'all';
var _debounceTimer;

// Event delegation — niente inline onclick
document.querySelector('.controls').addEventListener('click', function(e) {{
  var pill = e.target.closest('.pill');
  if (!pill) return;
  var type = pill.hasAttribute('data-filter-cat') ? 'cat' : 'genre';
  var value = pill.getAttribute('data-filter-' + type);
  if (type === 'cat') {{
    activeCat = value;
    document.querySelectorAll('[data-filter-cat]').forEach(function(b) {{ b.classList.remove('active'); }});
  }} else {{
    activeGenre = value;
    document.querySelectorAll('[data-filter-genre]').forEach(function(b) {{ b.classList.remove('active'); }});
  }}
  pill.classList.add('active');
  applyFilters();
}});

document.getElementById('search').addEventListener('input', function() {{
  clearTimeout(_debounceTimer);
  _debounceTimer = setTimeout(applyFilters, 200);
}});

document.getElementById('sort').addEventListener('change', applyFilters);

function applyFilters() {{
  var q = document.getElementById('search').value.toLowerCase().trim();
  var sort = document.getElementById('sort').value;
  var cards = [].slice.call(document.querySelectorAll('.game-card'));

  cards.forEach(function(c) {{
    var ok = (!q || c.dataset.title.indexOf(q) !== -1)
          && (activeCat === 'all' || (c.dataset.cats || c.dataset.cat || '').split(' ').indexOf(activeCat) !== -1)
          && (activeGenre === 'all' || c.dataset.genre === activeGenre);
    c.style.display = ok ? '' : 'none';
  }});

  var vis = cards.filter(function(c) {{ return c.style.display !== 'none'; }});
  var grid = document.getElementById('grid');

  if (sort === 'name-asc')        vis.sort(function(a,b) {{ return a.dataset.title.localeCompare(b.dataset.title); }});
  else if (sort === 'name-desc')  vis.sort(function(a,b) {{ return b.dataset.title.localeCompare(a.dataset.title); }});
  else if (sort === 'price-asc')  vis.sort(function(a,b) {{ return parseFloat(a.dataset.priceNum||0) - parseFloat(b.dataset.priceNum||0); }});
  else if (sort === 'price-desc') vis.sort(function(a,b) {{ return parseFloat(b.dataset.priceNum||0) - parseFloat(a.dataset.priceNum||0); }});
  else if (sort === 'cat')        vis.sort(function(a,b) {{ return a.dataset.cat.localeCompare(b.dataset.cat) || a.dataset.title.localeCompare(b.dataset.title); }});

  vis.forEach(function(c) {{ grid.appendChild(c); }});
  document.getElementById('vis-count').textContent = vis.length;
  document.getElementById('no-results').style.display = vis.length === 0 ? 'block' : 'none';
}}
</script>
</body>
</html>"""
