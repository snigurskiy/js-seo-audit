"""Collect the server response and rendered DOM from one browser navigation."""

from urllib.parse import urldefrag

from lxml.etree import ParserError
from playwright.sync_api import Error

from .blocks import BLOCK_SNAPSHOT, block_findings
from .compare import compare
from .html import extract, unique

RESOURCE_TYPES = {"script", "stylesheet", "xhr", "fetch"}


def inspect(browser, url, wait_ms, timeout_ms, blocks=()):
    context = browser.new_context(accept_downloads=False)
    page = context.new_page()
    issues = []

    def response_issue(response):
        if response.request.resource_type in RESOURCE_TYPES and response.status >= 400:
            issues.append(f"{response.status} {response.url}")

    def request_failed(request):
        if request.resource_type in RESOURCE_TYPES:
            issues.append(f"{request.failure} {request.url}")

    page.on("response", response_issue)
    page.on("requestfailed", request_failed)
    page.on("pageerror", lambda error: issues.append(f"JS: {error}"))
    record = {"url": url, "wait_ms": wait_ms, "findings": []}
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if response is None:
            record["findings"].append({"code": "no_http_response"})
            return record
        headers = response.header_values("x-robots-tag")
        record.update(http_status=response.status, response_url=response.url, x_robots_tag=headers)
        previous = response.request.redirected_from
        redirects = []
        while previous is not None:
            redirects.append(previous.url)
            previous = previous.redirected_from
        if redirects:
            record["http_redirect_from"] = redirects[::-1]
        if response.status != 200:
            record["findings"].append({"code": "http_status", "status": response.status})
        content_type = response.header_value("content-type") or ""
        if "html" not in content_type.lower():
            record["findings"].append({"code": "non_html_response", "content_type": content_type})
            return record

        source = response.text()
        raw = extract(source, response.url)
        record["raw"] = raw
        page.wait_for_timeout(wait_ms)
        record["final_url"] = page.url
        if urldefrag(page.url).url != urldefrag(response.url).url:
            record["findings"].append({
                "code": "url_changed_after_navigation",
                "response_url": response.url,
                "final_url": page.url,
            })
            return record
        rendered = extract(page.content(), page.url)
        record["rendered"] = rendered
        record["findings"].extend(compare(raw, rendered, headers))
        if blocks:
            record["blocks"] = page.evaluate(BLOCK_SNAPSHOT, {"source": source, "blocks": blocks})
            record["findings"].extend(block_findings(record["blocks"]))
    except (Error, ParserError, ValueError) as error:
        record["findings"].append({"code": "inspection_failed", "detail": str(error)})
    finally:
        if issues:
            record["findings"].append({
                "code": "browser_resource_or_script_errors",
                "examples": unique(issues)[:10],
            })
        context.close()
    return record
