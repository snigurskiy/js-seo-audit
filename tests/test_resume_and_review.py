import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from js_seo_audit.blocks import block_findings, parse_blocks
from js_seo_audit.cli import main
from js_seo_audit.google_review import review_rows
from test_reports import test_page


class ResumeTests(unittest.TestCase):
    def test_interrupted_run_resumes_and_keeps_both_attempts(self):
        with TemporaryDirectory() as folder:
            output = Path(folder) / "audit.json"
            first = "https://static.example/"
            candidate = "https://js.example/"
            argv = ["js-seo-audit", first, candidate, "--output", str(output)]

            def measure(browser, url, wait, timeout, blocks):
                page = test_page(url)
                page["wait_ms"] = wait
                return page

            with patch("js_seo_audit.cli.sync_playwright") as playwright:
                with patch.object(sys, "argv", argv), patch(
                    "js_seo_audit.cli.inspect", side_effect=[measure(None, first, 2000, 0, []), KeyboardInterrupt],
                ):
                    self.assertEqual(main(), 130)
                self.assertFalse(output.exists())
                with patch.object(sys, "argv", argv + ["--resume", "--no-retry"]):
                    self.assertEqual(main(), 1)
                with patch.object(sys, "argv", argv + ["--resume"]), patch(
                    "js_seo_audit.cli.inspect", side_effect=measure,
                ) as inspect:
                    self.assertEqual(main(), 0)
                self.assertEqual([(c.args[1], c.args[2]) for c in inspect.call_args_list],
                                 [(candidate, 2000), (candidate, 6000)])

                with patch.object(sys, "argv", argv + ["--resume"]):
                    launch_count = playwright.call_count
                    self.assertEqual(main(), 0)
                    self.assertEqual(playwright.call_count, launch_count)

            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual([p["url"] for p in report["pages"]], [first, candidate])
            self.assertEqual(report["pages"][1]["retry"]["wait_ms"], 6000)
            with ZipFile(output.with_suffix(".xlsx")) as workbook:
                self.assertIn("Перевірити в Google", workbook.read("xl/workbook.xml").decode())
                self.assertIn("xl/worksheets/sheet3.xml", workbook.namelist())
                self.assertIn("Free Music Tools", workbook.read("xl/sharedStrings.xml").decode())

    def test_block_text_loss_produces_original_phrase_for_google(self):
        selectors = parse_blocks(["Ingredients=[data-recipe='ingredients']"])
        url = "https://recipe.example/one"
        page = test_page(url)
        block = {
            **selectors[0],
            "raw": {"matches": 1, "text_chars": 19, "excerpt": "Salt and olive oil"},
            "rendered": {"matches": 1, "text_chars": 0, "excerpt": ""},
        }
        page["blocks"] = [block]
        page["findings"] = list(block_findings([block]))
        row, = review_rows({"recipe.example": [page]})
        self.assertEqual(page["findings"][0]["code"], "block_empty_after_js")
        self.assertEqual(row[1], url)
        self.assertEqual(row[3:5], ("Salt and olive oil", selectors[0]["selector"]))
