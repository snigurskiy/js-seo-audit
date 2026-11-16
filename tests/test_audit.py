import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from playwright.sync_api import sync_playwright

from js_seo_audit.browser import inspect
from js_seo_audit.compare import compare, header_noindex
from js_seo_audit.html import extract


class HTMLTests(unittest.TestCase):
    def test_main_and_relative_links(self):
        result = extract(
            '<head><base href="/catalog/"><title>Recipes</title></head>'
            '<body><nav><a href="soup">Menu</a></nav><main><h1>Soup</h1>'
            '<script>ignored()</script><a href="#/recipe">Read</a></main></body>',
            "https://example.com/",
        )
        self.assertEqual(result["h1"], ["Soup"])
        self.assertEqual(result["main_text_excerpt"], "Soup Read")
        self.assertEqual(result["links"], [
            "https://example.com/catalog/soup",
            "https://example.com/catalog/#/recipe",
        ])

    def test_content_and_metadata_difference(self):
        url = "https://example.com/"
        raw = extract("<title>Loading</title><main></main>", url)
        rendered = extract(
            "<title>Soup</title><main><h1>Soup</h1><p>"
            + "Soup ingredients and instructions. " * 10
            + '</p><a href="/related">Related recipe</a></main>',
            url,
        )
        findings = compare(raw, rendered, [])
        self.assertTrue({
            "html_changed", "main_text_added_after_js", "links_added_after_js",
        } <= {finding["code"] for finding in findings})
        self.assertIn(
            "main_text_lost_after_js",
            {finding["code"] for finding in compare(rendered, raw, [])},
        )

    def test_scoped_robot_directives(self):
        headers = ["googlebot: nofollow, none", "bingbot: max-snippet: 0"]
        self.assertTrue(header_noindex(headers, "googlebot"))
        self.assertFalse(header_noindex(headers, "bingbot"))
        self.assertTrue(header_noindex(["max-snippet: 15, noindex"], "bingbot"))


class TestSite(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return
        if self.path == "/failed.js":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/missing":
            self.send_response(404)
            markup = "<body>Missing page</body>"
        elif self.path == "/noindex":
            self.send_response(200)
            self.send_header("X-Robots-Tag", "googlebot: noindex")
            markup = "<main><h1>Known page</h1></main>"
        elif self.path == "/vanishes":
            self.send_response(200)
            markup = (
                "<main><p>" + "Main text present on the server. " * 20
                + "</p></main><script>document.querySelector('main').replaceChildren()</script>"
            )
        else:
            self.send_response(200)
            markup = """<head><title>Loading</title></head><body>
            <main></main><script src="/failed.js"></script>
            <script>
            setTimeout(() => {
                document.title = "Recipe";
                document.querySelector("main").innerHTML =
                    '<h1>Borsch</h1><p>' + 'Recipe ingredients and instructions. '.repeat(10) +
                    '</p><a href="/related">Related</a>';
            }, 50);
            </script></body>"""
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(markup.encode())

    def log_message(self, *args):
        pass


class BrowserTests(unittest.TestCase):
    def test_raw_response_and_real_browser_render(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), TestSite) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_port}"
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        executable_path=os.environ.get("JS_SEO_TEST_CHROMIUM")
                    )
                    audit = inspect(browser, url + "/redirect", 250, 5000, [
                        {"label": "Heading", "selector": "main > h1"},
                        {"label": "Invalid", "selector": "["},
                    ])
                    missing = inspect(browser, url + "/missing", 0, 5000)
                    noindex = inspect(browser, url + "/noindex", 0, 5000)
                    vanishes = inspect(browser, url + "/vanishes", 0, 5000)
                    browser.close()
            finally:
                server.shutdown()
                thread.join()

        self.assertEqual(audit["http_redirect_from"], [url + "/redirect"])
        self.assertEqual(audit["raw"]["h1"], [])
        self.assertEqual(audit["rendered"]["h1"], ["Borsch"])
        self.assertEqual(audit["rendered"]["title"], "Recipe")
        heading = audit["blocks"][0]
        self.assertEqual(heading["raw"]["matches"], 0)
        self.assertEqual(heading["rendered"]["excerpt"], "Borsch")
        self.assertIn("error", audit["blocks"][1])
        codes = {finding["code"] for finding in audit["findings"]}
        self.assertTrue({
            "main_text_added_after_js", "links_added_after_js",
            "browser_resource_or_script_errors",
        } <= codes, audit)
        self.assertEqual(missing["http_status"], 404)
        self.assertEqual(
            [finding["bot"] for finding in noindex["findings"] if finding["code"] == "noindex"],
            ["googlebot"],
        )
        self.assertIn(
            "main_text_lost_after_js",
            {finding["code"] for finding in vanishes["findings"]},
        )


if __name__ == "__main__":
    unittest.main()
