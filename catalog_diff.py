"""Diff tra due snapshot del catalogo Xbox e changelog Novità.

Lo scrape settimanale riusa questo modulo per il messaggio di commit e per
`data/changes.json`. L'indice elenca gli ultimi snapshot; i giochi stanno in
`data/changes/<data>.json`, così la pagina non scarica tutta la storia insieme.

Ogni snapshot ha added, removed, price_down e price_up. I rimossi tengono
titolo e prezzo dello snapshot precedente. Un prezzo assente o non numerico
non conta come ribasso né come rialzo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("added", "removed", "price_down", "price_up")
PRICE_KINDS = ("price_down", "price_up")
MAX_SNAPSHOTS = 30
# Uno snapshot settimanale vero è circa 300 KB. Oltre questa soglia si tengono
# i movimenti di prezzo più ampi e i conteggi restano quelli completi.
MAX_SNAPSHOT_BYTES = 550_000
# Saltare i salti di catalogo (per esempio i 4k → 16k giochi): non sono una
# "novità" settimanale e gonfierebbero il file.
MAX_BOOTSTRAP_CHURN = 2500
STORE_URL = "https://www.xbox.com/games/store/-/{gid}"


def empty_diff() -> dict[str, list]:
    return {kind: [] for kind in KINDS}


def is_empty(diff: dict) -> bool:
    return all(not diff.get(kind) for kind in KINDS)


def cents(value: float) -> int:
    return int(round(float(value) * 100))


def comparable_price(game: dict) -> float | None:
    """Prezzo confrontabile, oppure None se manca o è malformato.

    `price_num` 0 con stato `free` è un prezzo vero. `unknown`, assente,
    booleano, NaN, negativo o non numerico non si confrontano: un gioco che
    passa da prezzo ignoto a prezzo noto non è un ribasso né un rialzo.
    """
    if not isinstance(game, dict):
        return None
    status = game.get("price_status")
    raw = game.get("price_num")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw != raw or raw in (float("inf"), float("-inf")) or raw < 0:
        return None
    if status == "unknown":
        return None
    if status == "free":
        return 0.0
    if status == "paid":
        return float(raw) if raw > 0 else None
    if status is None:
        # Cataloghi precedenti a price_status: 0 e prezzo vuoto significano N/D.
        price = game.get("price")
        if not isinstance(price, str) or not price.strip():
            return None
        return float(raw)
    return None


def display_price(game: dict, numeric: float | None) -> str:
    price = game.get("price") if isinstance(game, dict) else None
    if isinstance(price, str) and price.strip():
        return price.strip()
    if numeric is None:
        return "N/D"
    if cents(numeric) == 0:
        return "Gratis"
    return f"{numeric:.2f}"


def store_url(game: dict, gid: str) -> str:
    url = game.get("url") if isinstance(game, dict) else None
    if isinstance(url, str) and (url.startswith("https://") or url.startswith("http://")):
        return url
    return STORE_URL.format(gid=gid)


def title_of(game: dict, gid: str) -> str:
    title = game.get("title") if isinstance(game, dict) else None
    if isinstance(title, str) and title.strip():
        return title.strip()
    return gid


def index_games(games: list) -> dict[str, dict]:
    """Ultimo record con lo stesso id, come il confronto storico dello scrape."""
    indexed: dict[str, dict] = {}
    for game in games:
        if not isinstance(game, dict):
            continue
        gid = game.get("id")
        if not isinstance(gid, str) or not gid:
            continue
        indexed[gid] = game
    return indexed


def _identity(game: dict, gid: str, numeric: float | None) -> dict:
    return {
        "id": gid,
        "title": title_of(game, gid),
        "price": display_price(game, numeric),
        "url": store_url(game, gid),
    }


def _by_title(item: dict) -> tuple:
    return (item["title"].casefold(), item["id"])


def diff_catalogs(old_games, new_games) -> dict[str, list]:
    """Confronta due cataloghi. Se uno dei due non è una lista, il diff è vuoto.

    Non tratta un file mancante come "tutto nuovo": lo scrape fa lo stesso
    quando `games.json` precedente non c'è.
    """
    if not isinstance(old_games, list) or not isinstance(new_games, list):
        return empty_diff()

    old_index = index_games(old_games)
    new_index = index_games(new_games)
    old_ids = set(old_index)
    new_ids = set(new_index)

    added = [
        _identity(new_index[gid], gid, comparable_price(new_index[gid]))
        for gid in new_ids - old_ids
    ]
    removed = [
        _identity(old_index[gid], gid, comparable_price(old_index[gid]))
        for gid in old_ids - new_ids
    ]
    price_down: list[dict] = []
    price_up: list[dict] = []
    for gid in old_ids & new_ids:
        old_game = old_index[gid]
        new_game = new_index[gid]
        old_price = comparable_price(old_game)
        new_price = comparable_price(new_game)
        if old_price is None or new_price is None:
            continue
        old_cents = cents(old_price)
        new_cents = cents(new_price)
        if new_cents == old_cents:
            continue
        item = {
            "id": gid,
            "title": title_of(new_game, gid),
            "old": display_price(old_game, old_price),
            "new": display_price(new_game, new_price),
            "url": store_url(new_game, gid),
            "_delta": abs(new_cents - old_cents),
        }
        if new_cents < old_cents:
            price_down.append(item)
        else:
            price_up.append(item)

    added.sort(key=_by_title)
    removed.sort(key=_by_title)
    price_key = lambda item: (-item["_delta"], item["title"].casefold(), item["id"])
    price_down.sort(key=price_key)
    price_up.sort(key=price_key)
    return {
        "added": added,
        "removed": removed,
        "price_down": [_public_item(item) for item in price_down],
        "price_up": [_public_item(item) for item in price_up],
    }


def _public_item(item: dict) -> dict:
    kept = ("id", "title", "price", "old", "new", "url")
    return {key: item[key] for key in kept if key in item}


def _dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _encoded_size(payload: dict) -> int:
    return len(_dumps(payload).encode("utf-8"))


def build_snapshot(diff: dict, when: datetime) -> dict:
    moment = _as_utc(when)
    return {
        "at": moment.isoformat(),
        "counts": {kind: len(diff.get(kind) or []) for kind in KINDS},
        "added": list(diff.get("added") or []),
        "removed": list(diff.get("removed") or []),
        "price_down": list(diff.get("price_down") or []),
        "price_up": list(diff.get("price_up") or []),
    }


def bound_snapshot(snapshot: dict, max_bytes: int = MAX_SNAPSHOT_BYTES) -> dict:
    """Accorcia le liste se il JSON supera la soglia. I conteggi restano interi."""
    current = {
        "at": snapshot.get("at"),
        "counts": dict(snapshot.get("counts") or {}),
        "added": list(snapshot.get("added") or []),
        "removed": list(snapshot.get("removed") or []),
        "price_down": list(snapshot.get("price_down") or []),
        "price_up": list(snapshot.get("price_up") or []),
    }
    if _encoded_size(current) <= max_bytes:
        return current

    def shrink(kinds: tuple[str, ...]) -> bool:
        kind = max(kinds, key=lambda name: len(current[name]))
        if not current[kind]:
            return False
        drop = max(1, len(current[kind]) // 10)
        del current[kind][-drop:]
        return True

    while _encoded_size(current) > max_bytes and any(current[kind] for kind in PRICE_KINDS):
        if not shrink(PRICE_KINDS):
            break
    while _encoded_size(current) > max_bytes and any(current[kind] for kind in ("added", "removed")):
        if not shrink(("added", "removed")):
            break
    return current


def _as_utc(when: datetime | str) -> datetime:
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _stamp(when: datetime) -> str:
    return _as_utc(when).strftime("%Y-%m-%dT%H%M%SZ")


def _href_name(href: str) -> str | None:
    if not isinstance(href, str) or not href or href.startswith(("/", "\\")):
        return None
    name = Path(href.split("?", 1)[0]).name
    if not name.endswith(".json") or name != Path(name).name:
        return None
    if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in name):
        return None
    return name


def load_index(data_dir: Path) -> dict:
    path = data_dir / "changes.json"
    if not path.is_file():
        return {"snapshots": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"snapshots": []}
    if not isinstance(data, dict):
        return {"snapshots": []}
    snapshots = data.get("snapshots")
    if not isinstance(snapshots, list):
        snapshots = []
    return {
        "generated_at": data.get("generated_at"),
        "snapshots": [item for item in snapshots if isinstance(item, dict)],
    }


def _write_index(data_dir: Path, snapshots: list[dict], generated_at: datetime | None = None) -> None:
    payload = {
        "generated_at": _as_utc(generated_at or datetime.now(timezone.utc)).isoformat(),
        "snapshots": snapshots,
    }
    (data_dir / "changes.json").write_text(_dumps(payload), encoding="utf-8")


def _cleanup(folder: Path, keep: set[str]) -> None:
    if not folder.is_dir():
        return
    for child in folder.glob("*.json"):
        if child.name not in keep:
            child.unlink()


def _allocate_name(when: datetime, reserved: set[str]) -> str:
    base = _stamp(when)
    name = base + ".json"
    if name not in reserved:
        return name
    suffix = 2
    while f"{base}-{suffix}.json" in reserved:
        suffix += 1
    return f"{base}-{suffix}.json"


def _store_snapshot(data_dir: Path, entry: dict, name: str) -> dict:
    folder = data_dir / "changes"
    folder.mkdir(parents=True, exist_ok=True)
    text = _dumps(entry)
    (folder / name).write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return {
        "at": entry["at"],
        "counts": entry["counts"],
        "href": f"data/changes/{name}?v={digest}",
    }


def publish_snapshot(
    old_games,
    new_games,
    data_dir: Path,
    when: datetime | None = None,
    max_snapshots: int = MAX_SNAPSHOTS,
    max_bytes: int = MAX_SNAPSHOT_BYTES,
) -> dict | None:
    """Accoda uno snapshot se il catalogo è cambiato. Ritorna il diff, o None senza baseline."""
    if not isinstance(old_games, list) or not isinstance(new_games, list):
        return None
    diff = diff_catalogs(old_games, new_games)
    if is_empty(diff):
        return diff

    moment = _as_utc(when or datetime.now(timezone.utc))
    entry = bound_snapshot(build_snapshot(diff, moment), max_bytes)
    data_dir.mkdir(parents=True, exist_ok=True)
    index = load_index(data_dir)
    previous = [item for item in index["snapshots"] if item.get("at") != entry["at"]]
    reserved = {_href_name(item.get("href")) for item in previous}
    reserved.discard(None)
    replaced = [item for item in index["snapshots"] if item.get("at") == entry["at"]]
    name = _href_name(replaced[0].get("href")) if replaced else None
    if not name:
        name = _allocate_name(moment, reserved)
    meta = _store_snapshot(data_dir, entry, name)
    snapshots = [meta, *previous]
    snapshots.sort(key=lambda item: item.get("at") or "", reverse=True)
    kept = snapshots[:max_snapshots]
    _write_index(data_dir, kept)
    keep_names = {name for name in (_href_name(item.get("href")) for item in kept) if name}
    _cleanup(data_dir / "changes", keep_names)
    return diff


def write_history(
    data_dir: Path,
    snapshots: list[tuple[datetime, dict]],
    max_bytes: int = MAX_SNAPSHOT_BYTES,
    generated_at: datetime | None = None,
) -> None:
    """Riscrive indice e file snapshot. `snapshots` è dal più recente al più vecchio."""
    data_dir.mkdir(parents=True, exist_ok=True)
    folder = data_dir / "changes"
    folder.mkdir(parents=True, exist_ok=True)
    for child in folder.glob("*.json"):
        child.unlink()
    reserved: set[str] = set()
    index_entries: list[dict] = []
    for when, diff in snapshots:
        moment = _as_utc(when)
        entry = bound_snapshot(build_snapshot(diff, moment), max_bytes)
        name = _allocate_name(moment, reserved)
        reserved.add(name)
        index_entries.append(_store_snapshot(data_dir, entry, name))
    _write_index(data_dir, index_entries, generated_at)


def versioned_href(path: Path, public_path: str) -> str:
    if not path.is_file():
        return public_path
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
    return f"{public_path}?v={digest}"


def load_games_file(path: Path) -> list | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) else None


def load_git_games(repo: Path, rev: str, path: str = "games.json") -> list | None:
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{rev}:{path}"],
            cwd=repo,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def list_game_commits(repo: Path, path: str = "games.json") -> list[tuple[str, str]]:
    try:
        text = subprocess.check_output(
            ["git", "log", "--format=%H%x09%cI", "--", path],
            cwd=repo,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    commits: list[tuple[str, str]] = []
    for line in text.splitlines():
        sha, separator, when = line.partition("\t")
        if separator and sha and when:
            commits.append((sha, when))
    return commits


def bootstrap_from_git(
    repo: Path,
    data_dir: Path,
    max_snapshots: int = MAX_SNAPSHOTS,
    games_path: str = "games.json",
    max_churn: int = MAX_BOOTSTRAP_CHURN,
) -> int:
    """Ricostruisce gli ultimi snapshot confrontando `games.json` nei commit.

    Le coppie con migliaia di aggiunte o rimozioni sono rebuild del catalogo,
    non una settimana di novità, e si saltano. Ritorna quanti snapshot scrive.
    """
    commits = list_game_commits(repo, games_path)
    kept: list[tuple[datetime, dict]] = []
    newer_games: list | None = None
    newer_when: str | None = None
    for sha, when in commits:
        games = load_git_games(repo, sha, games_path)
        if not isinstance(games, list):
            newer_games = None
            newer_when = None
            continue
        if isinstance(newer_games, list) and newer_when:
            diff = diff_catalogs(games, newer_games)
            churn_ok = len(diff["added"]) <= max_churn and len(diff["removed"]) <= max_churn
            if churn_ok and not is_empty(diff):
                kept.append((_as_utc(newer_when), diff))
                if len(kept) >= max_snapshots:
                    break
        newer_games = games
        newer_when = when
    write_history(data_dir, kept[:max_snapshots])
    return len(kept)


def build_report(old_games, new_games, generated_at: datetime | None = None) -> dict:
    if isinstance(old_games, list) and isinstance(new_games, list):
        diff = diff_catalogs(old_games, new_games)
    else:
        diff = empty_diff()

    def brief(items: list[dict]) -> list[dict]:
        return [
            {"id": item["id"], "title": item["title"]}
            for item in sorted(items, key=lambda item: item["id"])
        ]

    return {
        "generated_at": _as_utc(generated_at or datetime.now(timezone.utc)).isoformat(),
        "total_games": len(new_games) if isinstance(new_games, list) else 0,
        "added_count": len(diff["added"]),
        "removed_count": len(diff["removed"]),
        "price_down_count": len(diff["price_down"]),
        "price_up_count": len(diff["price_up"]),
        "added": brief(diff["added"]),
        "removed": brief(diff["removed"]),
    }


def write_github_env(path: str, counts: dict[str, int]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        for key in ("ADDED", "REMOVED", "PRICE_DOWN", "PRICE_UP"):
            handle.write(f"{key}={int(counts[key])}\n")


def _cmd_report(args: argparse.Namespace) -> int:
    new_games = load_games_file(Path(args.new))
    if new_games is None:
        print(f"Catalogo nuovo illeggibile: {args.new}", file=sys.stderr)
        return 1
    if args.previous:
        old_games = load_games_file(Path(args.previous))
    elif args.previous_rev:
        old_games = load_git_games(Path(args.repo), args.previous_rev, args.games_path)
    else:
        old_games = None
    report = build_report(old_games, new_games)
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    counts = {
        "ADDED": report["added_count"],
        "REMOVED": report["removed_count"],
        "PRICE_DOWN": report["price_down_count"],
        "PRICE_UP": report["price_up_count"],
    }
    if args.github_env:
        write_github_env(args.github_env, counts)
    print(
        f"Nuovi: {counts['ADDED']}, rimossi: {counts['REMOVED']}, "
        f"prezzo giù: {counts['PRICE_DOWN']}, prezzo su: {counts['PRICE_UP']}"
    )
    for item in report["added"][:20]:
        print(f"  + {item['title']}")
    if len(report["added"]) > 20:
        print(f"  ... e altri {len(report['added']) - 20}")
    return 0


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    count = bootstrap_from_git(
        Path(args.repo),
        Path(args.data_dir),
        max_snapshots=args.max_snapshots,
    )
    print(f"Snapshot novità: {count}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff del catalogo Xbox e changelog Novità")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Confronta due cataloghi e scrive scrape-report.json")
    report.add_argument("--new", required=True)
    report.add_argument("--previous", help="File JSON del catalogo precedente")
    report.add_argument("--previous-rev", help="Revisione git del catalogo precedente, es. HEAD")
    report.add_argument("--games-path", default="games.json")
    report.add_argument("--repo", default=".")
    report.add_argument("--report", required=True)
    report.add_argument("--github-env")
    report.set_defaults(func=_cmd_report)

    bootstrap = sub.add_parser("bootstrap", help="Ricostruisce data/changes.json dalla storia git")
    bootstrap.add_argument("--repo", default=".")
    bootstrap.add_argument("--data-dir", default="data")
    bootstrap.add_argument("--max-snapshots", type=int, default=MAX_SNAPSHOTS)
    bootstrap.set_defaults(func=_cmd_bootstrap)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
