"""Report observed differences and assign descriptive finding codes."""

import re
from urllib.parse import urlsplit

from .html import has_noindex


def header_noindex(values, bot):
    """Respect bot-specific X-Robots-Tag values."""
    rules = []
    for value in values:
        scope = None
        for rule in value.lower().split(","):
            qualifier = re.match(r"\s*([\w-]+)\s*:\s*(.*)", rule)
            if qualifier:
                name, rule = qualifier.groups()
                if name in {"max-snippet", "max-image-preview", "max-video-preview", "unavailable_after"}:
                    continue
                scope = name
            if scope in {None, bot}:
                rules.append(rule)
    return has_noindex(rules)


def compare(raw, rendered, headers):
    findings = []

    def add(code, **evidence):
        findings.append({"code": code, **evidence})

    for bot in ("googlebot", "bingbot"):
        raw_noindex = has_noindex(raw["robots"] + raw[bot])
        rendered_noindex = has_noindex(rendered["robots"] + rendered[bot])
        blocked_by_header = header_noindex(headers, bot)
        if raw_noindex or rendered_noindex or blocked_by_header:
            add("noindex", bot=bot, raw=raw_noindex, rendered=rendered_noindex, header=blocked_by_header)

    for key in ("title", "description", "canonical", "robots", "googlebot", "bingbot", "h1", "schema_types"):
        if raw[key] != rendered[key]:
            add("html_changed", field=key, raw=raw[key], rendered=rendered[key])

    before, after = raw["main_text_chars"], rendered["main_text_chars"]
    if after >= 100 and after - before >= 100 and before <= after * 0.3:
        add("main_text_added_after_js", raw_chars=before, rendered_chars=after)
    elif before >= 100 and before - after >= 100 and after <= before * 0.3:
        add("main_text_lost_after_js", raw_chars=before, rendered_chars=after)
    elif after == 0:
        add("empty_rendered_main_text")

    raw_links = set(raw["links"])
    rendered_links = set(rendered["links"])
    for code, difference in (
        ("links_added_after_js", rendered_links - raw_links),
        ("links_lost_after_js", raw_links - rendered_links),
    ):
        if difference:
            add(code, count=len(difference), examples=sorted(difference)[:5])
    hash_routes = [
        url for url in rendered["links"]
        if urlsplit(url).fragment.startswith(("/", "!/"))
    ]
    if hash_routes:
        add("hash_routes", count=len(hash_routes), examples=hash_routes[:5])
    return findings
