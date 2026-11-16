"""Inspect explicitly configured content blocks with browser CSS selectors."""

BLOCK_SNAPSHOT = r"""
({source, blocks}) => {
    const original = new DOMParser().parseFromString(source, "text/html");
    function snapshot(doc, selector) {
        const nodes = [...doc.querySelectorAll(selector)];
        const pieces = nodes.map(node => {
            const copy = node.cloneNode(true);
            copy.querySelectorAll("script, style, template, noscript").forEach(node => node.remove());
            return copy.textContent.replace(/\s+/g, " ").trim();
        });
        const text = pieces.filter(Boolean).join(" ");
        return {matches: nodes.length, text_chars: text.length, text};
    }
    return blocks.map(({label, selector}) => {
        try {
            const raw = snapshot(original, selector);
            const rendered = snapshot(document, selector);
            const textChanged = raw.text !== rendered.text;
            for (const state of [raw, rendered]) {
                state.excerpt = state.text.slice(0, 350);
                delete state.text;
            }
            return {label, selector, raw, rendered, text_changed: textChanged};
        } catch (error) {
            return {label, selector, error: error.message};
        }
    });
}
"""


def parse_blocks(items):
    blocks = []
    labels = set()
    for item in items:
        label, _, selector = item.partition("=")
        label, selector = label.strip(), selector.strip()
        if not label or not selector or label.casefold() in labels:
            raise ValueError("Use --block 'unique label=CSS selector' for each block.")
        blocks.append({"label": label, "selector": selector})
        labels.add(label.casefold())
    return blocks


def block_findings(blocks):
    for block in blocks:
        if "error" in block:
            code = "invalid_block_selector"
        else:
            raw, rendered = block["raw"], block["rendered"]
            if not raw["matches"] and not rendered["matches"]:
                code = "block_missing"
            elif raw["matches"] and not rendered["matches"]:
                code = "block_lost_after_js"
            elif not raw["matches"] and rendered["matches"]:
                code = "block_added_after_js"
            elif raw["text_chars"] and not rendered["text_chars"]:
                code = "block_empty_after_js"
            elif not raw["text_chars"] and rendered["text_chars"]:
                code = "block_text_added_after_js"
            elif raw != rendered or block.get("text_changed"):
                code = "block_changed"
            else:
                continue
        yield {"code": code, "block": block["label"]}
