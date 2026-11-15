/* Vista Novità. Caricata solo quando si apre la sezione, non col catalogo. */
(function () {
  var PAGE = 40;
  var LABELS = {
    all: "Tutti",
    added: "Nuovi",
    removed: "Rimossi",
    price_down: "Prezzo giù",
    price_up: "Prezzo su"
  };
  var KIND = {
    added: "Nuovo",
    removed: "Rimosso",
    price_down: "Prezzo giù",
    price_up: "Prezzo su"
  };
  var state = {
    index: null,
    files: {},
    current: 0,
    filter: "all",
    limit: PAGE
  };

  function $(id) {
    return document.getElementById(id);
  }

  function status(text) {
    var el = $("novita-status");
    if (el) el.textContent = text || "";
  }

  function allowedHref(href) {
    if (typeof href !== "string") return "";
    if (!/^data\/changes\/[A-Za-z0-9._-]+\.json(\?v=[A-Za-z0-9._-]+)?$/.test(href)) return "";
    return href;
  }

  function fetchJson(url) {
    return fetch(url, {credentials: "same-origin"}).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    });
  }

  function countOf(file, entry, kind) {
    if (entry && entry.counts && typeof entry.counts[kind] === "number") return entry.counts[kind];
    var list = file && file[kind];
    return Array.isArray(list) ? list.length : 0;
  }

  function formatWhen(iso) {
    var date = new Date(iso);
    if (isNaN(date.getTime())) return iso || "Snapshot";
    return date.toLocaleString("it-IT", {dateStyle: "medium", timeStyle: "short"});
  }

  function safeUrl(url) {
    if (typeof url !== "string") return "";
    if (url.indexOf("https://") === 0 || url.indexOf("http://") === 0) return url;
    return "";
  }

  function rowsFor(file, filter) {
    var rows = [];
    function push(kind) {
      var items = file[kind];
      if (!Array.isArray(items)) return;
      for (var i = 0; i < items.length; i++) rows.push({kind: kind, item: items[i] || {}});
    }
    if (filter === "all" || filter === "added") push("added");
    if (filter === "all" || filter === "removed") push("removed");
    if (filter === "all" || filter === "price_down") push("price_down");
    if (filter === "all" || filter === "price_up") push("price_up");
    return rows;
  }

  function renderRow(row) {
    var item = row.item || {};
    var url = safeUrl(item.url);
    var el = document.createElement(url ? "a" : "div");
    el.className = "change-row";
    el.setAttribute("role", "listitem");
    if (url) {
      el.href = url;
      el.target = "_blank";
      el.rel = "noopener noreferrer";
    }
    var kind = document.createElement("span");
    kind.className = "change-kind" + (row.kind === "removed" ? " removed" : row.kind === "price_up" ? " up" : "");
    kind.textContent = KIND[row.kind] || row.kind;
    var title = document.createElement("span");
    title.className = "change-title";
    title.textContent = item.title || item.id || "Gioco";
    if (url) {
      var hint = document.createElement("span");
      hint.className = "sr-only";
      hint.textContent = " (si apre in una nuova scheda)";
      title.appendChild(hint);
    }
    var price = document.createElement("span");
    price.className = "change-price";
    if (row.kind === "price_down" || row.kind === "price_up") {
      var was = document.createElement("span");
      was.className = "was";
      was.textContent = item.old || "N/D";
      price.appendChild(was);
      price.appendChild(document.createTextNode(" \u2192 " + (item["new"] || "N/D")));
    } else {
      price.textContent = item.price || "N/D";
    }
    el.appendChild(kind);
    el.appendChild(title);
    el.appendChild(price);
    return el;
  }

  function truncationNote(file, entry) {
    var names = {added: "nuovi", removed: "rimossi", price_down: "ribassi", price_up: "rialzi"};
    var parts = [];
    Object.keys(names).forEach(function (key) {
      var total = entry && entry.counts ? entry.counts[key] : null;
      var have = file && Array.isArray(file[key]) ? file[key].length : 0;
      if (typeof total === "number" && total > have) parts.push(have + " di " + total + " " + names[key]);
    });
    return parts.length ? "Elenco parziale: " + parts.join(", ") + "." : "";
  }

  function render() {
    var entry = state.index.snapshots[state.current];
    var href = allowedHref(entry && entry.href);
    var file = state.files[href] || {};
    var when = $("novita-when");
    if (when) when.textContent = formatWhen(entry && entry.at);
    var counts = $("novita-counts");
    if (counts) {
      counts.textContent = countOf(file, entry, "added") + " nuovi · " +
        countOf(file, entry, "removed") + " rimossi · " +
        countOf(file, entry, "price_down") + " prezzo giù · " +
        countOf(file, entry, "price_up") + " prezzo su";
    }
    var pills = document.querySelectorAll("#novita [data-change]");
    for (var p = 0; p < pills.length; p++) {
      var key = pills[p].getAttribute("data-change");
      var label = LABELS[key] || key;
      if (key !== "all") label += " (" + countOf(file, entry, key) + ")";
      pills[p].textContent = label;
      var on = key === state.filter;
      pills[p].classList.toggle("active", on);
      pills[p].setAttribute("aria-pressed", on ? "true" : "false");
    }
    var rows = rowsFor(file, state.filter);
    var list = $("novita-list");
    list.textContent = "";
    var shown = Math.min(state.limit, rows.length);
    var fragment = document.createDocumentFragment();
    for (var i = 0; i < shown; i++) fragment.appendChild(renderRow(rows[i]));
    list.appendChild(fragment);
    if (!rows.length) {
      var empty = document.createElement("p");
      empty.className = "no-results";
      empty.style.display = "block";
      empty.textContent = "Nessuna novità in questo filtro.";
      list.appendChild(empty);
    }
    var note = truncationNote(file, entry);
    if (note) {
      var partial = document.createElement("p");
      partial.className = "novita-note";
      partial.textContent = note;
      list.appendChild(partial);
    }
    var more = $("novita-more");
    if (shown < rows.length) {
      more.hidden = false;
      more.textContent = "Mostra altri (" + (rows.length - shown) + ")";
    } else {
      more.hidden = true;
    }
  }

  function selectSnapshot(index) {
    var entry = state.index.snapshots[index];
    var href = allowedHref(entry && entry.href);
    if (!href) {
      status("Snapshot non disponibile.");
      return Promise.resolve();
    }
    state.current = index;
    state.limit = PAGE;
    var select = $("novita-snapshot");
    if (select) select.value = String(index);
    status("Caricamento snapshot...");
    var pending = state.files[href]
      ? Promise.resolve(state.files[href])
      : fetchJson(href).then(function (data) {
          state.files[href] = data;
          return data;
        });
    return pending.then(function () {
      status("");
      render();
    }).catch(function () {
      status("Impossibile caricare questo snapshot.");
    });
  }

  function fillSelect() {
    var select = $("novita-snapshot");
    select.textContent = "";
    var snaps = state.index.snapshots;
    for (var i = 0; i < snaps.length; i++) {
      var option = document.createElement("option");
      option.value = String(i);
      option.textContent = formatWhen(snaps[i].at);
      select.appendChild(option);
    }
  }

  function start(manifestUrl) {
    status("Caricamento novità...");
    return fetchJson(manifestUrl).then(function (index) {
      if (!index || !Array.isArray(index.snapshots) || !index.snapshots.length) {
        status("Nessuno snapshot disponibile.");
        return;
      }
      state.index = index;
      state.current = 0;
      state.filter = "all";
      state.limit = PAGE;
      fillSelect();
      return selectSnapshot(0);
    }).catch(function () {
      status("Impossibile caricare le novità. Ricarica la pagina.");
    });
  }

  var snapshot = $("novita-snapshot");
  if (snapshot) {
    snapshot.addEventListener("change", function (event) {
      if (!state.index) return;
      selectSnapshot(Number(event.target.value));
    });
  }
  var pills = document.querySelector("#novita .novita-pills");
  if (pills) {
    pills.addEventListener("click", function (event) {
      var pill = event.target.closest("[data-change]");
      if (!pill || !state.index) return;
      state.filter = pill.getAttribute("data-change");
      state.limit = PAGE;
      render();
    });
  }
  var more = $("novita-more");
  if (more) {
    more.addEventListener("click", function () {
      state.limit += PAGE;
      render();
    });
  }

  window.XboxNovita = {start: start};
})();
