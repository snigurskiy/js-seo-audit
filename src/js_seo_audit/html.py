"""Extract comparable SEO signals from two versions of an HTML document."""

import json
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from lxml import html


def clean(text):
    return " ".join(text.split())


def unique(items):
    return list(dict.fromkeys(items))


def schema_types(item):
    if isinstance(item, list):
        for value in item:
            yield from schema_types(value)
    elif isinstance(item, dict):
        types = item.get("@type", [])
        for name in [types] if isinstance(types, str) else types or []:
            if isinstance(name, str):
                yield name
        for value in item.values():
            if isinstance(value, (list, dict)):
                yield from schema_types(value)


def extract(markup, url):
    doc = html.document_fromstring(markup or "<html></html>")
    base = urljoin(url, doc.xpath("string((//head/base[@href])[1]/@href)"))
    metadata = {}
    for element in doc.xpath("//head/meta[@name][@content]"):
        name = element.get("name").strip().lower()
        metadata.setdefault(name, []).append(element.get("content").strip())

    canonical = [
        urljoin(base, link.get("href").strip())
        for link in doc.xpath("//head/link[@rel][@href]")
        if "canonical" in link.get("rel").lower().split() and link.get("href").strip()
    ]
    links = []
    for href in doc.xpath("//a[@href]/@href"):
        href = href.strip()
        if href.startswith("#/") or href.startswith("#!/"):
            links.append(urljoin(base, href))
        elif href and not href.startswith("#"):
            try:
                target = urldefrag(urljoin(base, href)).url
                if urlsplit(target).scheme in {"http", "https"}:
                    links.append(target)
            except ValueError:
                continue

    schemas = []
    for script in doc.xpath("//script[@type='application/ld+json']/text()"):
        try:
            schemas.extend(schema_types(json.loads(script)))
        except (ValueError, TypeError, RecursionError):
            continue

    title = clean(doc.xpath("string((//head/title)[1])"))
    for node in doc.xpath("//script | //style | //template | //noscript | //svg"):
        node.drop_tree()
    main = doc.xpath("(//main | //*[@role='main'])[1]")
    if not main:
        main = doc.xpath("(//article)[1]") or doc.xpath("//body")
    root = main[0] if main else doc
    for node in root.xpath(".//nav | .//header[not(descendant::h1)] | .//footer | .//aside"):
        node.drop_tree()
    content = clean(" ".join(root.xpath(".//text()")))
    return {
        "title": title,
        "description": metadata.get("description", []),
        "canonical": unique(canonical),
        "robots": metadata.get("robots", []),
        "googlebot": metadata.get("googlebot", []),
        "bingbot": metadata.get("bingbot", []),
        "h1": [clean(node.text_content()) for node in root.xpath(".//h1")],
        "main_text_chars": len(content),
        "main_text_excerpt": content[:350],
        "links": unique(links),
        "schema_types": unique(schemas),
    }


def has_noindex(values):
    tokens = {token for value in values for token in re.split(r"[\s,]+", value.lower())}
    return bool(tokens & {"noindex", "none"})
