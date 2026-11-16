from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.etree import ElementTree
from zipfile import ZipFile

from js_seo_audit.cli import main
from js_seo_audit.compare import compare
from js_seo_audit.html import extract
from js_seo_audit.reports import report_paths, write_reports


def test_page(url, *args):
    raw = extract('<meta name="description" content="Music tools">', url)
    rendered = extract(
        '<title>=1+1</title><meta name="description" content="Music tools">'
        '<meta name="description" content="Music tools"><main><h1>Free Music Tools</h1>'
        + "<p>" + "Practice with a free online metronome. " * 8 + "</p>"
        '<a href="/instrument-tuner">Tuner</a></main>',
        url,
    )
    if "static.example" in url:
        raw = rendered
    if "unavailable.example" in url:
        return {"url": url, "http_status": 503, "findings": [{"code": "http_status", "status": 503}]}
    return {
        "url": url, "http_status": 200, "raw": raw, "rendered": rendered,
        "findings": compare(raw, rendered, []),
    }


class ReportTests(unittest.TestCase):
    def test_cli_writes_three_reports_from_url_list(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            input_file = base / "urls.txt"
            input_file.write_text(
                "https://js.example/\nhttps://static.example/\nhttps://js.example/page\n"
                "https://unavailable.example/\n", encoding="utf-8",
            )
            output = base / "audit.json"
            argv = ["js-seo-audit", "--input", str(input_file), "--output", str(output)]
            with (
                patch.object(sys, "argv", argv),
                patch("js_seo_audit.cli.sync_playwright"),
                patch("js_seo_audit.cli.inspect", side_effect=lambda browser, url, *args: test_page(url)),
            ):
                self.assertEqual(main(), 0)

            self.assertEqual(len(json.loads(output.read_text(encoding="utf-8"))["pages"]), 4)
            self.assertEqual((base / "invisible-domains.txt").read_text(encoding="utf-8"), "js.example\n")
            with ZipFile(base / "audit.xlsx") as workbook:
                self.assertIsNone(workbook.testzip())
                self.assertIn("Домени", workbook.read("xl/workbook.xml").decode())
                strings = workbook.read("xl/sharedStrings.xml").decode()
                self.assertIn("Уривок основного тексту", strings)
                self.assertIn("https://js.example/instrument-tuner", strings)
                self.assertIn("Не виміряно", strings)
                self.assertIn("=1+1", strings)
                ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                details = ElementTree.fromstring(workbook.read("xl/worksheets/sheet2.xml"))
                self.assertEqual(details.findall(".//x:f", ns), [])
                self.assertIsNotNone(details.find("x:autoFilter", ns))

    def test_distinct_runs_share_candidate_history(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            first = report_paths(base / "audit.json")
            second = report_paths(base / "check.json")
            first[2].write_bytes(b"JS.EXAMPLE")
            write_reports({"pages": [
                test_page("https://js.example/"), test_page("https://other.example/"),
            ]}, first)
            write_reports({"pages": [test_page("https://static.example/")]}, second)
            write_reports({"pages": [test_page("https://other.example/again")]}, second)
            self.assertEqual(second[1].name, "check.xlsx")
            self.assertEqual(second[2], first[2])
            self.assertEqual(first[2].read_text(encoding="utf-8"), "JS.EXAMPLE\nother.example\n")

    def test_overlapping_batches_keep_both_new_domains(self):
        with TemporaryDirectory() as directory, ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(
                    write_reports, {"pages": [test_page(f"https://{name}.example/")]},
                    report_paths(Path(directory) / f"{name}.json"),
                )
                for name in ("first", "second")
            ]
            for future in futures:
                future.result()
            lines = (Path(directory) / "invisible-domains.txt").read_text(encoding="utf-8").splitlines()
            self.assertCountEqual(lines, ["first.example", "second.example"])


if __name__ == "__main__":
    unittest.main()
