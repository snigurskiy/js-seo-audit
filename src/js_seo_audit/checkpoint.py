"""Keep committed URL results in a checkpoint beside the final report."""

from contextlib import closing, contextmanager
import json
import sqlite3


@contextmanager
def checkpoint(path, settings, resume):
    if resume and not path.is_file():
        raise ValueError(f"Checkpoint not found: {path}")
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS settings (value TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS results (url TEXT PRIMARY KEY, record TEXT NOT NULL)")
        if resume:
            saved = db.execute("SELECT value FROM settings").fetchone()
            if saved is None or json.loads(saved[0]) != settings:
                raise ValueError("Checkpoint settings differ. Resume with the same URLs and options.")
        else:
            with db:
                db.execute("DELETE FROM settings")
                db.execute("DELETE FROM results")
                db.execute("INSERT INTO settings VALUES (?)", (json.dumps(settings),))
        yield db


def saved_results(db):
    return {url: json.loads(record) for url, record in db.execute("SELECT url, record FROM results")}


def save_result(db, result):
    with db:
        db.execute("INSERT INTO results VALUES (?, ?)", (result["url"], json.dumps(result)))
