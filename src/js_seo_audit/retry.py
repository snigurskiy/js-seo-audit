"""Identify suspicious renders and retain evidence from each attempt."""

GOOGLE_CODES = {
    "main_text_added_after_js", "main_text_lost_after_js",
    "block_added_after_js", "block_lost_after_js",
    "block_empty_after_js", "block_text_added_after_js",
}
RETRY_CODES = GOOGLE_CODES | {"empty_rendered_main_text", "block_missing"}


def should_retry(page):
    return page.get("http_status") == 200 and any(
        finding["code"] in RETRY_CODES for finding in page["findings"]
    )


def attempts(page):
    yield page
    if "retry" in page:
        yield page["retry"]
