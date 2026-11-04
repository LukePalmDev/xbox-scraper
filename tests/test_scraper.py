import json
import base64
import re
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
