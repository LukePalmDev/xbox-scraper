import json
import tempfile
import unittest
from pathlib import Path

import fetch_bigids
import fetch_xbox_og


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
        self.assertEqual(parsed["source_category"], "Xbox Original (OG)")
        self.assertEqual(parsed["genre"], "Action & adventure")


class JsonFixtureTests(unittest.TestCase):
    def test_bigids_fixture_has_expected_shape(self):
        data = json.loads(Path("bigids.json").read_text(encoding="utf-8"))

        self.assertGreater(data["total"], 3000)
        self.assertIn("ids", data)
        self.assertIn("categories", data)
        self.assertIn("xboxOG", data["categories"])


if __name__ == "__main__":
    unittest.main()
