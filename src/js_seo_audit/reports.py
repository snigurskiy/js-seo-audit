"""Write the JSON, Excel and domain-list reports for one audit run."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import wrap
from urllib.parse import urlsplit

import xlsxwriter

from .google_review import REASONS, review_rows
from .retry import GOOGLE_CODES, attempts

FIELDS = (
    ("Основний текст, символів", "main_text_chars"),
    ("Уривок основного тексту", "main_text_excerpt"),
    ("Посилання, кількість", "links"),
    ("Title", "title"),
    ("H1", "h1"),
    ("Canonical", "canonical"),
    ("Meta description", "description"),
    ("Типи структурованих даних", "schema_types"),
)

FINDING_LABELS = {
    "links_added_after_js": "Посилання додані після JavaScript",
    "links_lost_after_js": "Посилання зникли після JavaScript",
    "browser_resource_or_script_errors": "Помилки ресурсів або JavaScript",
    "url_changed_after_navigation": "URL змінився після навігації",
    "non_html_response": "Відповідь без HTML",
    "no_http_response": "Немає HTTP-відповіді",
    "inspection_failed": "Перевірка не завершилася",
    "hash_routes": "Маршрути через фрагмент URL",
    "invalid_block_selector": "Некоректний CSS-селектор",
    "block_changed": "Змінився вміст блоку",
}


def is_candidate(page):
    return any(
        attempt.get("http_status") == 200 and any(
            finding["code"] in GOOGLE_CODES for finding in attempt["findings"]
        )
        for attempt in attempts(page)
    )


def findings_text(page):
    notes = []
    for finding in page["findings"]:
        code = finding["code"]
        if code == "html_changed":
            continue
        if code == "noindex":
            notes.append(f'Знайдено noindex для {finding["bot"]}')
        elif code == "http_status":
            notes.append(f'HTTP {finding["status"]}')
        else:
            note = REASONS.get(code) or FINDING_LABELS.get(code, code)
            if finding.get("block"):
                note += " · " + finding["block"]
            notes.append(note)

    descriptions = page.get("rendered", {}).get("description", [])
    if len(descriptions) > 1:
        notes.append(f"Meta description після JS — {len(descriptions)} теги")
    return "\n".join(notes) or "Окремих попереджень немає"


def value_for(snapshot, key):
    if snapshot is None:
        return "Не виміряно"
    value = snapshot.get(key)
    if key == "links":
        return len(value or [])
    if isinstance(value, list):
        return "\n".join(value) or "Відсутнє"
    return value if value or value == 0 else "Відсутнє"


def details_rows(groups):
    for domain, pages in groups.items():
        for page in pages:
            url = page["url"]
            candidate = "Так" if is_candidate(page) else "—"
            for index, attempt in enumerate(attempts(page)):
                note = findings_text(attempt)
                wait = attempt.get("wait_ms")
                if wait is not None:
                    note = f"Очікування: {wait} мс\n{note}"
                yield domain, url, "Повторний замір" if index else "Висновок", "", "", candidate, note
                destination = attempt.get("final_url") or attempt.get("response_url")
                if destination and destination != url:
                    yield domain, url, "Кінцева адреса", "", "", candidate, destination
                status = attempt.get("http_status")
                yield domain, url, "HTTP-статус", status or "Не отримано", "", candidate, ""
                headers = attempt.get("x_robots_tag")
                if headers:
                    yield domain, url, "X-Robots-Tag", "\n".join(headers), "", candidate, ""
                raw, rendered = attempt.get("raw"), attempt.get("rendered")
                for label, key in FIELDS:
                    yield (
                        domain, url, label, value_for(raw, key),
                        value_for(rendered, key), candidate, "",
                    )
                examples = [
                    link for finding in attempt["findings"]
                    if finding["code"] == "links_added_after_js"
                    for link in finding.get("examples", [])
                ]
                if examples:
                    yield domain, url, "Приклади доданих посилань", "", "\n".join(examples), candidate, ""
                for block in attempt.get("blocks", []):
                    evidence = block["selector"]
                    if "error" in block:
                        yield domain, url, block["label"], "", "", candidate, f'{evidence}\n{block["error"]}'
                        continue
                    snapshots = []
                    for key in ("raw", "rendered"):
                        state = block[key]
                        snapshots.append(
                            f'{state["matches"]} збігів · {state["text_chars"]} символів\n{state["excerpt"]}'
                        )
                    yield domain, url, block["label"], *snapshots, candidate, evidence


def write_table(book, name, headers, widths, rows, frozen_columns=0):
    sheet = book.add_worksheet(name)
    sheet.freeze_panes(1, frozen_columns)
    sheet.hide_gridlines(2)
    sheet.set_zoom(90)
    heading = book.add_format({
        "bg_color": "#17344D", "font_color": "white",
        "bold": True, "valign": "vcenter",
    })
    normal = book.add_format({"text_wrap": True, "valign": "top", "font_color": "#17344D"})
    shaded = book.add_format({
        "text_wrap": True, "valign": "top", "font_color": "#17344D", "bg_color": "#EDF4F8",
    })
    sheet.write_row(0, 0, headers, heading)
    sheet.set_row(0, 26)
    for index, width in enumerate(widths):
        sheet.set_column(index, index, width)
    last_row = 0
    for last_row, row in enumerate(rows, start=1):
        style = shaded if last_row % 2 == 0 else normal
        sheet.write_row(last_row, 0, row, style)
        line_count = max(
            sum(max(1, len(wrap(line, width=max(8, int(width * 0.9)))))
                for line in str(value).split("\n"))
            for value, width in zip(row, widths)
        )
        sheet.set_row(last_row, min(180, 6 + 15 * line_count))
    sheet.autofilter(0, 0, last_row, len(headers) - 1)


def write_workbook(path, groups):
    options = {"strings_to_formulas": False, "strings_to_urls": False}
    with xlsxwriter.Workbook(path, options) as book:
        summaries = []
        for domain, pages in groups.items():
            candidates = sum(is_candidate(page) for page in pages)
            incomplete = sum(not any(attempt.get("rendered") is not None
                                     for attempt in attempts(page)) for page in pages)
            summaries.append((domain, len(pages), candidates, incomplete, "Так" if candidates else "—"))
        write_table(
            book, "Домени",
            ["Домен", "URL в аудиті", "URL з ознаками ризику", "URL без знімка DOM", "Ознака в цьому прогоні"],
            [39, 18, 27, 26, 27], summaries,
        )
        write_table(
            book, "Деталі URL",
            ["Домен", "URL", "Показник", "Початковий HTML", "Після JavaScript", "Ознака на цій URL", "Примітка"],
            [28, 43, 34, 47, 47, 25, 48], details_rows(groups), frozen_columns=2,
        )
        write_table(
            book, "Перевірити в Google",
            ["Домен", "Точна URL", "Причина відбору", "Фраза для пошуку в HTML",
             "CSS-селектор за наявності", "Очікування, мс", "Результат ручної перевірки"],
            [30, 48, 47, 42, 30, 19, 32],
            review_rows(groups), frozen_columns=2,
        )


def report_paths(json_path):
    workbook_path = json_path.with_suffix(".xlsx")
    if workbook_path == json_path:
        workbook_path = json_path.with_name(json_path.name + ".xlsx")
    txt_path = json_path.with_name("invisible-domains.txt")
    if json_path in {txt_path, txt_path.with_name(f".{txt_path.stem}.lock")}:
        raise ValueError("--output conflicts with the shared domain list or its lock file.")
    return json_path, workbook_path, txt_path


@contextmanager
def report_lock(txt_path):
    # Lock a stable file because the TXT is published by atomic replacement.
    lock_path = txt_path.with_name(f".{txt_path.stem}.lock")
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def merge_candidates(txt_path, candidates):
    try:
        existing = txt_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        existing = None
    seen = {
        line.strip().rstrip(".").casefold()
        for line in (existing or "").splitlines() if line.strip()
    }
    additions = []
    for domain in candidates:
        key = domain.rstrip(".").casefold()
        if key not in seen:
            additions.append(domain)
            seen.add(key)

    if existing is not None and not additions:
        return None

    separator = "\n" if existing and not existing.endswith(("\n", "\r")) else ""
    return (existing or "") + separator + "".join(f"{domain}\n" for domain in additions)


def write_reports(report, paths):
    groups = {}
    for page in report["pages"]:
        domain = urlsplit(page["url"]).hostname
        groups.setdefault(domain, []).append(page)
    candidates = [domain for domain, pages in groups.items() if any(map(is_candidate, pages))]

    with TemporaryDirectory(dir=paths[0].parent) as directory:
        json_file, workbook_file, txt_file = [Path(directory) / path.name for path in paths]
        json_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_workbook(workbook_file, groups)
        with report_lock(paths[2]):
            updated_text = merge_candidates(paths[2], candidates)
            if updated_text is not None:
                txt_file.write_text(updated_text, encoding="utf-8")
            workbook_file.replace(paths[1])
            json_file.replace(paths[0])
            if updated_text is not None:
                txt_file.replace(paths[2])
