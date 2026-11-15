import json
import base64
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import catalog_diff
import fetch_bigids
import fetch_xbox_og
import html_builder


class BigIdParsingTests(unittest.TestCase):
    def test_extract_game_id_arrays_keeps_non_empty_categories(self):
        js = """
        gameIdArrays["xboxOG"] = ["BS7SQNNRB28W","C0J2F5B1B7JD"];
        gameIdArrays["xboxone"] = [];
        gameIdArrays["xbox360"] = ["9NXXNTRZBS0Z"];
        """

        result = fetch_bigids.extract_game_id_arrays(js)

        self.assertEqual(result["xboxOG"], ["BS7SQNNRB28W", "C0J2F5B1B7JD"])
        self.assertEqual(result["xbox360"], ["9NXXNTRZBS0Z"])
        self.assertNotIn("xboxone", result)

    def test_extract_biurls_object_reads_url_map(self):
        js = """
        biUrls = {
          "items": {
            "urls": {
              "BRVM8RNWLXH1": "https://www.xbox.com/games/example"
            }
          }
        };
        """

        result = fetch_bigids.extract_biurls_object(js)

        self.assertEqual(
            result,
            {"BRVM8RNWLXH1": "https://www.xbox.com/games/example"},
        )

    def test_extract_store_product_ids_reads_microsoft_store_cards(self):
        html = """
        {"productId":"9pjpcb188svg","title":"Subnautica 2"}
        {"productId":"BT5P2X999VH2","title":"Fortnite"}
        {"productId":"9pjpcb188svg","title":"Duplicate"}
        """

        result = fetch_bigids.extract_store_product_ids(html)

        self.assertEqual(result, ["9PJPCB188SVG", "BT5P2X999VH2"])

    def test_extract_preloaded_state_reads_browse_channel(self):
        state = {
            "core2": {
                "channels": {
                    "channelData": {
                        "BROWSE_CHANNELID=_FILTERS=": {
                            "data": {
                                "products": [
                                    {"productId": "9pjpcb188svg"},
                                    {"productId": "BT5P2X999VH2"},
                                ],
                                "encodedCT": "token",
                                "totalItems": 16482,
                            }
                        }
                    }
                }
            }
        }
        html = f"""
        <script>
        window.__PRELOADED_STATE__ = {json.dumps(state)};
        window.__OTHER__ = {{"ignored": true}};
        </script>
        """

        parsed = fetch_bigids.extract_preloaded_state(html)
        ids, encoded_ct, total_items = fetch_bigids.extract_browse_channel_data(parsed)

        self.assertEqual(ids, ["9PJPCB188SVG", "BT5P2X999VH2"])
        self.assertEqual(encoded_ct, "token")
        self.assertEqual(total_items, 16482)

    def test_browse_has_more_decodes_continuation_token(self):
        token = base64.b64encode(json.dumps({"HasMore": False}).encode()).decode()

        self.assertFalse(fetch_bigids.browse_has_more(token))

    def test_build_browse_channel_key_matches_frontend_format(self):
        filters = {"orderby": {"id": "orderby", "choices": [{"id": "Title Asc"}]}}

        self.assertEqual(
            fetch_bigids.build_browse_channel_key(filters),
            "BROWSE_CHANNELID=_FILTERS=ORDERBY=TITLE ASC",
        )

    def test_merge_categories_deduplicates_preserving_order(self):
        target = {"xboxOG": ["A", "B"]}
        fetch_bigids.merge_categories(target, {"xboxOG": ["B", "C"], "storeNew": ["D"]})

        self.assertEqual(target["xboxOG"], ["A", "B", "C"])
        self.assertEqual(target["storeNew"], ["D"])

    def test_legacy_biurls_parser_ignores_trailing_js(self):
        content = """
        biUrls = {
          "items": {
            "urls": {
              "BRVM8RNWLXH1": "https://www.xbox.com/games/example<exc>IT"
            }
          }
        }
        gameIdArrays["xboxOG"] = ["BRVM8RNWLXH1"];
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "xcat-bi-urls2.json"
            path.write_text(content, encoding="utf-8")

            ids, urls = fetch_xbox_og._parse_js_biurls(path)

        self.assertEqual(ids, ["BRVM8RNWLXH1"])
        self.assertEqual(urls["BRVM8RNWLXH1"], "https://www.xbox.com/games/example<exc>IT")


class ProductParsingTests(unittest.TestCase):
    def test_filter_by_market_excludes_matching_exc_suffix(self):
        result = fetch_xbox_og.filter_by_market(
            ["A", "B"],
            {
                "A": "https://www.xbox.com/games/a<exc>IT, ja-jp",
                "B": "https://www.xbox.com/games/b<exc>ja-jp",
            },
            "IT",
        )

        self.assertEqual(result, ["B"])

    def test_parse_product_extracts_display_fields(self):
        product = {
            "ProductId": "BS7SQNNRB28W",
            "LocalizedProperties": [
                {
                    "ProductTitle": "Armed and Dangerous",
                    "Images": [
                        {"ImagePurpose": "BoxArt", "Uri": "//images.example/box.jpg"},
                    ],
                }
            ],
            "DisplaySkuAvailabilities": [
                {
                    "Availabilities": [
                        {
                            "OrderManagementData": {
                                "Price": {
                                    "ListPrice": 19.99,
                                    "CurrencyCode": "EUR",
                                }
                            }
                        }
                    ]
                }
            ],
            "Properties": {"Categories": ["Action & adventure"]},
        }

        parsed = fetch_xbox_og.parse_product(product, "BS7SQNNRB28W", "Xbox Original (OG)")

        self.assertEqual(parsed["id"], "BS7SQNNRB28W")
        self.assertEqual(parsed["title"], "Armed and Dangerous")
        self.assertEqual(parsed["img"], "https://images.example/box.jpg")
        self.assertEqual(parsed["price"], "19.99 EUR")
        self.assertEqual(parsed["price_num"], 19.99)
        self.assertEqual(parsed["price_status"], "paid")
        self.assertEqual(parsed["source_category"], "Xbox Original (OG)")
        self.assertEqual(parsed["source_categories"], ["Xbox Original (OG)"])
        self.assertEqual(parsed["genre"], "Action & adventure")

    def test_parse_product_distinguishes_free_from_unknown_price(self):
        free_product = {
            "ProductId": "FREE123456",
            "LocalizedProperties": [{"ProductTitle": "Free Game", "Images": []}],
            "DisplaySkuAvailabilities": [
                {
                    "Availabilities": [
                        {"OrderManagementData": {"Price": {"ListPrice": 0, "CurrencyCode": "EUR"}}}
                    ]
                }
            ],
            "Properties": {},
        }
        unknown_product = {
            "ProductId": "UNKNOWN123",
            "LocalizedProperties": [{"ProductTitle": "Unknown Game", "Images": []}],
            "DisplaySkuAvailabilities": [{"Availabilities": []}],
            "Properties": {},
        }

        free = fetch_xbox_og.parse_product(free_product, "FREE123456", "Test")
        unknown = fetch_xbox_og.parse_product(unknown_product, "UNKNOWN123", "Test")

        self.assertEqual(free["price"], "Gratis")
        self.assertEqual(free["price_status"], "free")
        self.assertIsNone(unknown["price"])
        self.assertEqual(unknown["price_status"], "unknown")

    def test_parse_product_skips_empty_image_uri_and_upgrades_http(self):
        product = {
            "ProductId": "IMG12345678",
            "LocalizedProperties": [{
                "ProductTitle": "Img",
                "Images": [
                    {"ImagePurpose": "BoxArt", "Uri": ""},
                    {"ImagePurpose": "Tile", "Uri": "http://images.example/tile.jpg"},
                ],
            }],
            "DisplaySkuAvailabilities": [{"Availabilities": []}],
            "Properties": {},
        }

        parsed = fetch_xbox_og.parse_product(product, "IMG12345678", "Test")

        self.assertEqual(parsed["img"], "https://images.example/tile.jpg")

    def test_process_batch_result_matches_product_id_case(self):
        product = {
            "ProductId": "bs7sqnnrb28w",
            "LocalizedProperties": [{"ProductTitle": "Armed and Dangerous", "Images": []}],
            "DisplaySkuAvailabilities": [
                {"Availabilities": [
                    {"OrderManagementData": {"Price": {"ListPrice": 1, "CurrencyCode": "EUR"}}}
                ]}
            ],
            "Properties": {"Categories": ["Action & adventure"]},
        }
        seen: set[str] = set()

        games, missing = fetch_xbox_og._process_batch_result(
            ["BS7SQNNRB28W"],
            [product],
            {"BS7SQNNRB28W": ["Xbox Original (OG)"]},
            seen,
        )

        self.assertEqual(missing, [])
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0]["source_category"], "Xbox Original (OG)")
        self.assertEqual(games[0]["id"], "bs7sqnnrb28w")

        again, missing_again = fetch_xbox_og._process_batch_result(
            ["BS7SQNNRB28W"],
            [product],
            {"BS7SQNNRB28W": ["Xbox Original (OG)"]},
            seen,
        )
        self.assertEqual(again, [])
        self.assertEqual(missing_again, [])

        _, truly_missing = fetch_xbox_og._process_batch_result(
            ["ZZMISSING1"],
            [],
            {},
            set(),
        )
        self.assertEqual(truly_missing, ["ZZMISSING1"])

    def test_merge_games_keeps_existing_catalog(self):
        merged = fetch_xbox_og.merge_games(
            [{"id": "AAA", "title": "Old"}, {"id": "BBB", "title": "Keep"}],
            [{"id": "aaa", "title": "New"}, {"id": "CCC", "title": "Add"}],
        )

        self.assertEqual(
            [(game["id"], game["title"]) for game in merged],
            [("aaa", "New"), ("BBB", "Keep"), ("CCC", "Add")],
        )


class JsonFixtureTests(unittest.TestCase):
    def test_bigids_fixture_has_expected_shape(self):
        data = json.loads(Path("bigids.json").read_text(encoding="utf-8"))

        self.assertGreater(data["total"], 3000)
        self.assertIn("ids", data)
        self.assertIn("categories", data)
        self.assertIn("xboxOG", data["categories"])


class HtmlBuilderTests(unittest.TestCase):
    def _game(self, **overrides):
        game = {
            "id": "BS7SQNNRB28W",
            "title": "Armed and Dangerous",
            "img": None,
            "price": "19.99 EUR",
            "price_num": 19.99,
            "price_status": "paid",
            "source_category": "Xbox Original (OG)",
            "source_categories": ["Xbox Original (OG)", "FPS Boost Series X"],
            "genre": "Action & adventure",
            "url": "https://www.xbox.com/games/store/-/BS7SQNNRB28W",
        }
        game.update(overrides)
        return game

    def test_slugify_matches_filter_tokens(self):
        self.assertEqual(html_builder.slugify("FPS Boost Series X"), "fps-boost-series-x")
        self.assertEqual(html_builder.slugify("Action & adventure"), "action-adventure")
        self.assertEqual(html_builder.slugify(""), "")

    def test_build_html_shell_keeps_controls_without_inlining_games(self):
        games = [self._game()]

        html = html_builder.build_html(games, "IT", "Xbox Original (OG)")

        self.assertIn("Xbox — 1 giochi", html)
        self.assertIn("game-card", html)
        self.assertIn('grid-template-columns:repeat(10,minmax(0,1fr))', html)
        self.assertIn('.pill-grid { grid-template-columns:1fr; }', html)
        self.assertIn("FPS Boost Series X", html)
        self.assertIn("Prezzo N/D", html)
        self.assertIn("Action &amp; adventure", html)
        self.assertIn("[^a-z0-9]+", html)
        self.assertNotIn("/xbox-scraper/", html)
        self.assertNotIn("Armed and Dangerous", html)
        self.assertIn("data/games-000.json", html)
        self.assertIn('id="tab-novita"', html)
        self.assertIn(">Novità</span>", html)
        self.assertIn("Prezzo giù", html)
        self.assertIn("Prezzo su", html)
        self.assertIn("function bootNovita()", html)
        self.assertIn("function isNovitaView()", html)
        self.assertIn('url.hash = "novita"', html)
        self.assertIn('.get("view")', html)
        self.assertIn("if (!catalogVisible()) return;", html)
        self.assertNotIn("<script src=", html)
        changes = json.loads(re.search(r'id="changes-manifest">(.*?)</script>', html).group(1))
        novita = json.loads(re.search(r'id="novita-script">(.*?)</script>', html).group(1))
        self.assertEqual(changes, "data/changes.json")
        self.assertEqual(novita, "assets/novita.js")
        self.assertLess(len(html.encode("utf-8")), 200_000)

    def test_write_game_shards_preserves_every_field(self):
        games = [
            self._game(id="ZZZ123456789", title='ZZ "Quoted" Game', img=None),
            self._game(id="AAA123456789", title="Alpha", img="https://images.example/a.jpg", price=None, price_num=0, price_status="unknown"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "data"
            paths = html_builder.write_game_shards(games, directory, shard_size=1)
            self.assertEqual(len(paths), 2)
            self.assertTrue(all(path.startswith("data/games-") and "?v=" in path for path in paths))

            loaded = []
            for rel in paths:
                loaded.extend(json.loads((Path(tmp) / rel.split("?", 1)[0]).read_text(encoding="utf-8")))
            self.assertEqual(loaded, sorted(games, key=lambda game: game["title"].lower()))

            html_builder.write_game_shards(games[:1], directory, shard_size=1)
            self.assertEqual(len(list(directory.glob("games-*.json"))), 1)

    def test_published_shards_match_games_json(self):
        games_path = Path("games.json")
        html_path = Path("index.html")
        if not games_path.exists() or not html_path.exists():
            self.skipTest("catalogo non generato")
        games = json.loads(games_path.read_text(encoding="utf-8"))
        html = html_path.read_text(encoding="utf-8")
        match = re.search(r'id="shard-manifest">(.*?)</script>', html)
        self.assertIsNotNone(match)
        shards = json.loads(match.group(1))
        self.assertGreater(len(shards), 1)
        loaded = []
        for rel in shards:
            file_path = Path(rel.split("?", 1)[0])
            self.assertFalse(file_path.is_absolute())
            self.assertFalse(str(file_path).startswith("/"))
            loaded.extend(json.loads(file_path.read_text(encoding="utf-8")))
        self.assertEqual(len(loaded), len(games))
        by_id = {game["id"]: game for game in games}
        self.assertEqual(set(by_id), {game["id"] for game in loaded})
        for game in loaded:
            self.assertEqual(game, by_id[game["id"]])
        self.assertNotIn("/xbox-scraper/", html)
        self.assertLess(len(html.encode("utf-8")), 200_000)
        self.assertIn('name="description"', html)
        self.assertIn('property="og:title"', html)
        self.assertIn('property="og:description"', html)
        self.assertIn('property="og:image"', html)
        self.assertIn("https://scraper.byluke.org/assets/og-image.png", html)
        self.assertIn("<h1>", html)
        self.assertIn("INITIAL_SHARDS = 2", html)
        self.assertIn("function startRest()", html)
        self.assertIn('id="load-status"', html)
        self.assertIn('id="tab-novita"', html)
        self.assertNotIn("Math.min(4", html)
        changes = json.loads(re.search(r'id="changes-manifest">(.*?)</script>', html).group(1))
        self.assertTrue(changes.startswith("data/changes.json"))
        self.assertNotIn('"added"', changes)

    def test_shell_lazy_loads_two_shards_and_exposes_social_metadata(self):
        html = html_builder.build_html([self._game(), self._game(id="ZZZZZZZZZZZZ", title="Zulu")], "IT", "Xbox Original (OG)")

        self.assertIn('name="description"', html)
        self.assertIn('property="og:title"', html)
        self.assertIn('property="og:description"', html)
        self.assertIn("og-image.png", html)
        self.assertRegex(html, r"<h1[ >]")
        self.assertIn("INITIAL_SHARDS = 2", html)
        self.assertIn("Scorri o cerca per il resto", html)
        self.assertIn("function startRest()", html)
        self.assertNotIn("Math.min(4", html)
        self.assertLess(len(html.encode("utf-8")), 200_000)

    def test_og_image_is_a_share_card(self):
        data = Path("assets/og-image.png").read_bytes()

        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(data[12:16], b"IHDR")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        self.assertEqual((width, height), (1200, 630))

    def test_initial_site_payload_is_under_three_megabytes(self):
        html_size = Path("index.html").stat().st_size
        image_size = Path("assets/og-image.png").stat().st_size
        shards = sorted(Path("data").glob("games-*.json"))
        self.assertGreaterEqual(len(shards), 3)
        initial = html_size + image_size + shards[0].stat().st_size + shards[1].stat().st_size
        self.assertLess(initial, 3_000_000)
        html = Path("index.html").read_text(encoding="utf-8")
        changes = json.loads(re.search(r'id="changes-manifest">(.*?)</script>', html).group(1))
        self.assertTrue(str(changes).startswith("data/changes.json"))
        self.assertLess(len(str(changes)), 80)
        self.assertNotIn('src="assets/novita.js', html)


class PagesWorkflowTests(unittest.TestCase):
    def _top_level_keys(self, text: str) -> list[str]:
        keys = []
        for line in text.splitlines():
            if not line or line[0].isspace() or line.lstrip().startswith("#"):
                continue
            self.assertIn(":", line)
            keys.append(line.split(":", 1)[0].strip().strip("'\""))
        return keys

    def test_workflows_are_valid_yaml_with_explicit_permissions(self):
        workflows = sorted(Path(".github/workflows").glob("*.yml"))
        self.assertGreaterEqual(len(workflows), 4)
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\t", text, path.name)
            keys = self._top_level_keys(text)
            self.assertIn("on", keys, path.name)
            self.assertIn("permissions", keys, path.name)
            self.assertIn("jobs", keys, path.name)
            self.assertIn("permissions:", text, path.name)

        dependabot = Path(".github/dependabot.yml").read_text(encoding="utf-8")
        self.assertIn("version: 2", dependabot)
        ecosystems = re.findall(r"package-ecosystem:\s*(\S+)", dependabot)
        self.assertEqual(ecosystems, ["github-actions"])

    def test_pages_deploys_after_a_successful_scrape_and_only_site_files(self):
        text = Path(".github/workflows/pages.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("workflow_run:", text)
        self.assertIn('workflows: ["Scrape Xbox Catalog"]', text)
        self.assertRegex(text, r"types:\s*\n\s*- completed")
        self.assertIn(
            "github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success'",
            text,
        )
        self.assertIn("path: _site", text)
        self.assertNotIn('path: "."', text)
        self.assertIn("gitleaks/gitleaks-action@v2", Path(".github/workflows/gitleaks.yml").read_text(encoding="utf-8"))
        ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("catalog_diff.py", ci)
        scrape = Path(".github/workflows/scrape.yml").read_text(encoding="utf-8")
        self.assertIn("catalog_diff.py report", scrape)
        self.assertIn("changelog-baseline.json", scrape)
        self.assertIn("--preserve-failed failed_ids.json", scrape)
        self.assertIn("PRICE_DOWN", scrape)
        self.assertNotIn("games_old.json", scrape)

        match = re.search(r"- name: Assemble site\n\s+run: \|\n(?P<body>(?:[ \t]+.+\n)+)", text)
        self.assertIsNotNone(match)
        body = "\n".join(line[10:] if line.startswith("          ") else line for line in match.group("body").splitlines())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(Path("index.html"), root / "index.html")
            shutil.copy(Path("CNAME"), root / "CNAME")
            shutil.copy(Path(".nojekyll"), root / ".nojekyll")
            shutil.copytree(Path("data"), root / "data")
            shutil.copytree(Path("assets"), root / "assets")
            (root / ".claude").mkdir()
            (root / ".claude" / "launch.json").write_text("{}", encoding="utf-8")
            (root / "README.md").write_text("readme", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "games.json").write_text("[]", encoding="utf-8")
            (root / "changelog-baseline.json").write_text("{}", encoding="utf-8")
            (root / ".github").mkdir()
            (root / "fetch_xbox_og.py").write_text("#", encoding="utf-8")

            subprocess.run(["bash", "-c", body], cwd=root, check=True)
            site = root / "_site"
            self.assertTrue((site / "index.html").is_file())
            self.assertTrue((site / "CNAME").is_file())
            self.assertTrue((site / "data" / "games-000.json").is_file())
            self.assertTrue((site / "data" / "changes.json").is_file())
            self.assertTrue(any((site / "data" / "changes").glob("*.json")))
            self.assertTrue((site / "assets" / "novita.js").is_file())
            self.assertTrue((site / "assets" / "og-image.png").is_file())
            self.assertTrue((site / ".nojekyll").is_file())
            self.assertFalse((site / ".claude" / "launch.json").exists())
            self.assertFalse((site / "README.md").exists())
            self.assertFalse((site / "scripts").exists())
            self.assertFalse((site / "games.json").exists())
            self.assertFalse((site / "changelog-baseline.json").exists())
            self.assertFalse((site / ".github").exists())
            self.assertFalse((site / "fetch_xbox_og.py").exists())


class CliValidationTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "ids": None,
            "batch": 50,
            "delay": 0.3,
            "workers": 3,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_validate_args_accepts_default_scrape_values(self):
        fetch_xbox_og.validate_args(self._args())

    def test_validate_args_rejects_batch_above_display_catalog_limit(self):
        with self.assertRaises(SystemExit):
            fetch_xbox_og.validate_args(self._args(batch=51))

    def test_validate_args_rejects_negative_delay(self):
        with self.assertRaises(SystemExit):
            fetch_xbox_og.validate_args(self._args(delay=-0.1))

    def test_validate_args_rejects_missing_ids_file(self):
        with self.assertRaises(SystemExit):
            fetch_xbox_og.validate_args(self._args(ids="/tmp/xbox-scraper-missing-bigids.json"))

    def test_select_category_eof_uses_all(self):
        with patch("builtins.input", side_effect=EOFError), patch("sys.stdout"):
            self.assertEqual(fetch_xbox_og.select_category_interactive("bigids.json"), "all")


class CatalogDiffTests(unittest.TestCase):
    def _paid(self, gid, title, price, **extra):
        game = {
            "id": gid,
            "title": title,
            "price": f"{price:.2f} EUR",
            "price_num": price,
            "price_status": "paid",
            "url": f"https://www.xbox.com/games/store/-/{gid}",
        }
        game.update(extra)
        return game

    def test_diff_added_removed_price_changes_and_unchanged(self):
        old = [
            self._paid("KEEP", "Stesso", 10),
            self._paid("DOWN", "Ribasso piccolo", 10),
            self._paid("BIG", "Ribasso grande", 30),
            self._paid("UP", "Rialzo", 5),
            self._paid("FREE", "Diventa gratis", 12),
            self._paid("GONE", "Sparito", 7.5),
            self._paid("DUP", "Prima", 10),
            {"title": "senza id"},
            None,
        ]
        new = [
            self._paid("KEEP", "Stesso", 10.0),
            self._paid("DOWN", "Ribasso piccolo", 9),
            self._paid("BIG", "Ribasso grande", 4),
            self._paid("UP", "Rialzo", 8),
            {
                "id": "FREE",
                "title": "Diventa gratis",
                "price": "Gratis",
                "price_num": 0,
                "price_status": "free",
                "url": "https://www.xbox.com/games/store/-/FREE",
            },
            self._paid("NEW", "Appena uscito", 19.99),
            self._paid("DUP", "Prima", 10),
            self._paid("DUP", "Dopo", 3),
        ]

        diff = catalog_diff.diff_catalogs(old, new)

        self.assertEqual([item["id"] for item in diff["added"]], ["NEW"])
        self.assertEqual(diff["added"][0]["title"], "Appena uscito")
        self.assertEqual(diff["added"][0]["price"], "19.99 EUR")
        self.assertEqual(diff["added"][0]["url"], "https://www.xbox.com/games/store/-/NEW")
        self.assertEqual(diff["removed"], [{
            "id": "GONE",
            "title": "Sparito",
            "price": "7.50 EUR",
            "url": "https://www.xbox.com/games/store/-/GONE",
        }])
        self.assertEqual([item["id"] for item in diff["price_down"]], ["BIG", "FREE", "DUP", "DOWN"])
        self.assertEqual(diff["price_down"][0]["old"], "30.00 EUR")
        self.assertEqual(diff["price_down"][0]["new"], "4.00 EUR")
        self.assertEqual(diff["price_down"][1]["new"], "Gratis")
        self.assertEqual([item["id"] for item in diff["price_up"]], ["UP"])
        dup = next(item for item in diff["price_down"] if item["id"] == "DUP")
        self.assertEqual(dup["title"], "Dopo")
        self.assertEqual(dup["new"], "3.00 EUR")
        self.assertNotIn("KEEP", [item["id"] for item in diff["added"] + diff["removed"] + diff["price_down"] + diff["price_up"]])
        self.assertEqual(diff["added"][0].get("_delta"), None)

    def test_missing_and_malformed_prices_are_not_price_changes(self):
        old = [
            self._paid("STR", "Stringa", 10),
            self._paid("NONE", "Assente", 10),
            self._paid("NAN", "Nan", 10),
            self._paid("BOOL", "Booleano", 10),
            self._paid("NEG", "Negativo", 10),
            self._paid("UNK", "Ignoto", 10),
            self._paid("NOISE", "Rumore", 19.99),
        ]
        new = [
            {"id": "STR", "title": "Stringa", "price": "8.00 EUR", "price_num": "8.00", "price_status": "paid"},
            {"id": "NONE", "title": "Assente", "price": None, "price_status": "paid"},
            {"id": "NAN", "title": "Nan", "price_num": float("nan"), "price_status": "paid", "price": "1.00 EUR"},
            {"id": "BOOL", "title": "Booleano", "price_num": True, "price_status": "paid", "price": "1.00 EUR"},
            {"id": "NEG", "title": "Negativo", "price_num": -1, "price_status": "paid", "price": "1.00 EUR"},
            {"id": "UNK", "title": "Ignoto", "price_num": 0, "price_status": "unknown", "price": None},
            self._paid("NOISE", "Rumore", 19.9900001),
            {"id": "ADDED", "title": "Senza prezzo"},
        ]

        diff = catalog_diff.diff_catalogs(old, new)

        self.assertEqual(diff["price_down"], [])
        self.assertEqual(diff["price_up"], [])
        self.assertEqual([item["id"] for item in diff["added"]], ["ADDED"])
        self.assertEqual(diff["added"][0]["price"], "N/D")
        self.assertEqual(diff["added"][0]["url"], "https://www.xbox.com/games/store/-/ADDED")
        self.assertEqual(diff["removed"], [])

    def test_missing_baseline_does_not_mark_everything_added(self):
        games = [self._paid("A", "Alpha", 1)]

        self.assertTrue(catalog_diff.is_empty(catalog_diff.diff_catalogs(None, games)))
        self.assertTrue(catalog_diff.is_empty(catalog_diff.diff_catalogs(games, "nope")))
        report = catalog_diff.build_report(None, games)
        self.assertEqual(report["added_count"], 0)
        self.assertEqual(report["total_games"], 1)

    def test_publish_rolls_thirty_snapshots_and_bounds_size(self):
        when = datetime(2026, 11, 1, tzinfo=timezone.utc)
        old = [self._paid(f"G{i}", "Gioco", 20) for i in range(6)]
        new = [self._paid(f"G{i}", "X" * 80, 1) for i in range(6)]
        diff = catalog_diff.diff_catalogs(old, new)
        snapshot = catalog_diff.build_snapshot(diff, when)
        bounded = catalog_diff.bound_snapshot(snapshot, max_bytes=700)
        self.assertEqual(bounded["counts"]["price_down"], 6)
        self.assertLess(len(bounded["price_down"]), 6)
        self.assertLessEqual(catalog_diff._encoded_size(bounded), 700)

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            base = [self._paid("A", "Alpha", 10), self._paid("B", "Beta", 10)]
            first = catalog_diff.publish_snapshot(base, base, data, when=when)
            self.assertTrue(catalog_diff.is_empty(first))
            self.assertFalse((data / "changes.json").exists())

            for index in range(3):
                nxt = list(base)
                nxt.append(self._paid(f"N{index}", f"Nuovo {index}", 4))
                catalog_diff.publish_snapshot(
                    base,
                    nxt,
                    data,
                    when=datetime(2026, 11, index + 1, tzinfo=timezone.utc),
                    max_snapshots=2,
                )
                base = nxt
            index = json.loads((data / "changes.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["snapshots"]), 2)
            self.assertEqual(index["snapshots"][0]["counts"]["added"], 1)
            files = list((data / "changes").glob("*.json"))
            self.assertEqual(len(files), 2)
            newest = json.loads((data / "changes" / catalog_diff._href_name(index["snapshots"][0]["href"])).read_text(encoding="utf-8"))
            self.assertEqual(newest["added"][0]["title"], "Nuovo 2")
            self.assertEqual(newest["added"][0]["price"], "4.00 EUR")

            catalog_diff.publish_snapshot(base, base, data, when=datetime(2026, 11, 9, tzinfo=timezone.utc))
            again = json.loads((data / "changes.json").read_text(encoding="utf-8"))
            self.assertEqual(len(again["snapshots"]), 2)

    def test_report_cli_writes_counts_without_a_previous_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "games.json").write_text(json.dumps([self._paid("A", "Alpha", 2)]), encoding="utf-8")
            env_path = root / "env"
            report_path = root / "scrape-report.json"
            subprocess.run(
                [
                    sys.executable,
                    str(Path("catalog_diff.py").resolve()),
                    "report",
                    "--new",
                    "games.json",
                    "--previous-rev",
                    "HEAD",
                    "--repo",
                    str(root),
                    "--report",
                    str(report_path),
                    "--github-env",
                    str(env_path),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["added_count"], 0)
            self.assertEqual(report["removed_count"], 0)
            self.assertEqual(report["price_down_count"], 0)
            self.assertEqual(report["price_up_count"], 0)
            env = env_path.read_text(encoding="utf-8")
            self.assertIn("ADDED=0\n", env)
            self.assertIn("REMOVED=0\n", env)
            self.assertIn("PRICE_DOWN=0\n", env)
            self.assertIn("PRICE_UP=0\n", env)

    def test_bootstrap_uses_git_history_and_skips_catalog_rebuilds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-b", "master"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            games = root / "games.json"

            def commit(payload, message):
                games.write_text(json.dumps(payload), encoding="utf-8")
                subprocess.run(["git", "add", "games.json"], cwd=root, check=True)
                subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True)

            commit([self._paid("A", "Alpha", 10)], "base")
            commit([self._paid("A", "Alpha", 4), self._paid("B", "Beta", 3)], "week")
            commit(
                [self._paid("A", "Alpha", 4), self._paid("B", "Beta", 3)]
                + [self._paid(f"X{i}", f"Extra {i}", 1) for i in range(5)],
                "rebuild",
            )

            count = catalog_diff.bootstrap_from_git(root, root / "data", max_snapshots=30, max_churn=2)

            self.assertEqual(count, 1)
            index = json.loads((root / "data" / "changes.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["snapshots"]), 1)
            self.assertEqual(index["snapshots"][0]["counts"], {
                "added": 1,
                "removed": 0,
                "price_down": 1,
                "price_up": 0,
            })
            href = index["snapshots"][0]["href"]
            snapshot = json.loads((root / href.split("?", 1)[0]).read_text(encoding="utf-8"))
            self.assertEqual(snapshot["added"][0]["title"], "Beta")
            self.assertEqual(snapshot["price_down"][0]["id"], "A")
            self.assertEqual(snapshot["price_down"][0]["old"], "10.00 EUR")
            self.assertEqual(snapshot["price_down"][0]["new"], "4.00 EUR")
            self.assertTrue(snapshot["removed"] == [])

    def test_failed_fetch_is_not_published_as_removed_or_later_added(self):
        when = datetime(2026, 11, 9, tzinfo=timezone.utc)
        full = [self._paid("A", "Alpha", 10), self._paid("B", "Beta", 8)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            baseline = root / "changelog-baseline.json"
            catalog_diff.record_full_catalog(None, full, data, baseline, when=when)
            partial = [self._paid("A", "Alpha", 10)]
            outcome = catalog_diff.record_full_catalog(
                catalog_diff.load_baseline(baseline),
                partial,
                data,
                baseline,
                failed_ids=["b"],
                when=datetime(2026, 11, 16, tzinfo=timezone.utc),
                trusted=True,
            )
            self.assertEqual(outcome, "unchanged")
            self.assertFalse((data / "changes.json").exists())
            kept = {game["id"]: game for game in catalog_diff.load_baseline(baseline)}
            self.assertEqual(kept["B"]["title"], "Beta")
            self.assertEqual(kept["B"]["price"], "8.00 EUR")

            recovered = [self._paid("A", "Alpha", 10), self._paid("B", "Beta", 8)]
            outcome = catalog_diff.record_full_catalog(
                catalog_diff.load_baseline(baseline),
                recovered,
                data,
                baseline,
                when=datetime(2026, 11, 23, tzinfo=timezone.utc),
                trusted=True,
            )
            self.assertEqual(outcome, "unchanged")
            self.assertFalse((data / "changes.json").exists())

    def test_category_subset_does_not_become_the_next_full_baseline(self):
        full = [self._paid("A", "Alpha", 10), self._paid("B", "Beta", 8), self._paid("C", "Gamma", 4)]
        subset = [self._paid("A", "Alpha", 9)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            baseline = root / "changelog-baseline.json"
            self.assertEqual(
                catalog_diff.record_full_catalog(None, full, data, baseline),
                "no-baseline",
            )
            skipped = catalog_diff.record_full_catalog(
                subset,
                full,
                data,
                baseline,
                trusted=False,
                max_churn=1,
            )
            self.assertEqual(skipped, "untrusted-scope")
            self.assertFalse((data / "changes.json").exists())

            nxt = full + [self._paid("D", "Delta", 6)]
            outcome = catalog_diff.record_full_catalog(
                catalog_diff.load_baseline(baseline),
                nxt,
                data,
                baseline,
                when=datetime(2026, 11, 16, tzinfo=timezone.utc),
                trusted=True,
            )
            self.assertEqual(outcome, "published")
            index = json.loads((data / "changes.json").read_text(encoding="utf-8"))
            self.assertEqual(index["snapshots"][0]["counts"]["added"], 1)
            self.assertEqual(index["snapshots"][0]["counts"]["removed"], 0)
            href = index["snapshots"][0]["href"]
            snapshot = json.loads((root / href.split("?", 1)[0]).read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in snapshot["added"]], ["D"])


if __name__ == "__main__":
    unittest.main()
