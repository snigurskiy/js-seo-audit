"""Audit explicitly supplied URLs and write JSON, Excel and domain-list reports."""

import argparse
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Error, sync_playwright
from xlsxwriter.exceptions import XlsxWriterException

from .blocks import parse_blocks
from .browser import inspect
from .checkpoint import checkpoint, saved_results, save_result
from .reports import report_lock, report_paths, write_reports
from .retry import should_retry


def normalize(value):
    url = value if "://" in value else "https://" + value
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        raise ValueError(f"Expected an HTTP(S) URL without credentials: {value}")
    return url


def audit_pending(db, settings, records):
    pending = [url for url in settings["urls"] if url not in records]
    total = len(settings["urls"])
    done = len(records)
    if done:
        print(f"Resuming: {done}/{total} URL(s) already saved.", flush=True)
    if not pending:
        return

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=settings["chromium_path"])
        for index, url in enumerate(pending, start=done + 1):
            print(f"[{index}/{total}] {url}", flush=True)
            page = inspect(browser, url, settings["wait_ms"], settings["timeout_ms"], settings["blocks"])
            retry_wait = settings["retry_wait_ms"]
            if retry_wait is not None and should_retry(page):
                print(f"  Retry with {retry_wait} ms wait", flush=True)
                page["retry"] = inspect(browser, url, retry_wait, settings["timeout_ms"], settings["blocks"])
            save_result(db, page)
            records[url] = page
            status = page.get("http_status", "no HTTP status")
            print(f"  Saved ({status})", flush=True)
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="*")
    parser.add_argument("--input", type=Path, help="UTF-8 file with one URL per line")
    parser.add_argument("--output", type=Path, default=Path("audit.json"))
    parser.add_argument("--wait-ms", type=int, default=2000, help="Wait after DOMContentLoaded")
    parser.add_argument("--timeout-ms", type=int, default=20000, help="Navigation timeout")
    parser.add_argument("--chromium-path", help="Optional path to a local Chromium executable")
    parser.add_argument("--resume", action="store_true", help="Reuse results saved for this output")
    parser.add_argument("--block", action="append", default=[], metavar="LABEL=CSS",
                        help="Check one content block on each URL. May be repeated.")
    retry = parser.add_mutually_exclusive_group()
    retry.add_argument("--retry-wait-ms", type=int, help="Longer wait for suspicious renders")
    retry.add_argument("--no-retry", action="store_true", help="Skip a second navigation")
    args = parser.parse_args()

    try:
        inputs = args.urls[:]
        if args.input:
            inputs.extend(args.input.read_text(encoding="utf-8-sig").splitlines())
        urls = list(dict.fromkeys(normalize(line.strip()) for line in inputs if line.strip()))
        blocks = parse_blocks(args.block)
        outputs = report_paths(args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not urls:
        parser.error("Provide at least one URL or --input.")
    if args.wait_ms < 0 or args.timeout_ms <= 0:
        parser.error("--wait-ms must be nonnegative and --timeout-ms must be positive.")
    if args.retry_wait_ms is not None and args.retry_wait_ms <= args.wait_ms:
        parser.error("--retry-wait-ms must be greater than --wait-ms.")

    checkpoint_path = args.output.with_name(args.output.name + ".checkpoint.sqlite3")
    if args.input and any(args.input.resolve() == path.resolve() for path in (*outputs, checkpoint_path)):
        parser.error("--input must be different from the report files and checkpoint.")

    retry_wait = args.retry_wait_ms or max(6000, args.wait_ms + 1000)
    settings = {
        "urls": urls, "wait_ms": args.wait_ms, "timeout_ms": args.timeout_ms,
        "chromium_path": args.chromium_path, "blocks": blocks,
        "retry_wait_ms": None if args.no_retry else retry_wait,
    }
    try:
        with report_lock(checkpoint_path), checkpoint(checkpoint_path, settings, args.resume) as db:
            records = saved_results(db)
            audit_pending(db, settings, records)
            report = {key: settings[key] for key in ("wait_ms", "timeout_ms", "retry_wait_ms", "blocks")}
            report["pages"] = [records[url] for url in urls]
            write_reports(report, outputs)
    except KeyboardInterrupt:
        print("Stopped. Add --resume to the same command to continue.", file=sys.stderr)
        return 130
    except (Error, OSError, ValueError, sqlite3.Error, XlsxWriterException) as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Checked {len(urls)} URL(s).")
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
