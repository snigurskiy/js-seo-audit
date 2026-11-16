"""Turn recorded render differences into evidence for manual Google checks."""

from .retry import GOOGLE_CODES, RETRY_CODES, attempts


REASONS = {
    "main_text_added_after_js": "Основний текст з’явився або суттєво доповнився після JS",
    "main_text_lost_after_js": "Значна частина основного тексту зникла після JS",
    "empty_rendered_main_text": "Основний текст після рендерингу порожній",
    "block_added_after_js": "Блок з’явився після JS",
    "block_text_added_after_js": "Текст блоку з’явився після JS",
    "block_lost_after_js": "Блок зник після JS",
    "block_empty_after_js": "Текст блоку зник після JS",
    "block_missing": "Селектор не знайшов блок у жодній версії HTML",
}


def new_phrase(target, other, text_key, headings=False):
    other_text = other.get(text_key, "")
    if headings:
        new_headings = [heading for heading in target.get("h1", []) if heading not in other.get("h1", [])]
        if new_headings:
            return new_headings[0]
    target_text = target.get(text_key, "")
    if len(target_text) == 350:
        target_text = target_text.rpartition(" ")[0]
    words = target_text.split()
    for size in range(min(7, len(words)), 0, -1):
        for start in range(len(words) - size + 1):
            phrase = " ".join(words[start:start + size])
            if phrase.casefold() not in other_text.casefold():
                return phrase
    return ""


def review_rows(groups):
    for domain, pages in groups.items():
        for page in pages:
            reasons = {}
            for attempt in attempts(page):
                if attempt.get("http_status") != 200:
                    continue
                blocks = {block["label"]: block for block in attempt.get("blocks", [])}
                for finding in attempt["findings"]:
                    code = finding["code"]
                    if code not in RETRY_CODES:
                        continue
                    label = finding.get("block", "")
                    block = blocks.get(label, {})
                    raw, rendered = (block or attempt).get("raw", {}), (block or attempt).get("rendered", {})
                    target, other = (rendered, raw) if "added" in code else (raw, rendered)
                    phrase = ""
                    if code in GOOGLE_CODES or code == "empty_rendered_main_text":
                        phrase = new_phrase(
                            target, other, "excerpt" if block else "main_text_excerpt",
                            headings=not block,
                        )
                    key = (code, label)
                    previous = reasons.get(key)
                    if previous is None or (phrase and not previous[0]):
                        reasons[key] = (phrase, block.get("selector", ""), attempt.get("wait_ms"))
            for (code, label), (phrase, selector, wait) in reasons.items():
                reason = REASONS[code] + (f" · {label}" if label else "")
                yield domain, page["url"], reason, phrase, selector, wait, ""
