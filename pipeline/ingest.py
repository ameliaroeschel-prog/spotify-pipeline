"""Stage 1 — Prepare. Code only, no model calls.

Reads any review CSV with the six source fields, preserves every value exactly,
hashes each row with the grading contract's row_sha, finds exact duplicate texts,
quarantines empty texts, and queues one canonical review per distinct text.

    python3 -m pipeline.ingest data/spotify_reviews_18months.csv --db runs/full/state.db
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_submission import FIELDS, csv_rows, profile, row_sha, sha  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    row_idx INTEGER NOT NULL,
    review_text TEXT NOT NULL,
    review_rating TEXT NOT NULL,
    review_likes TEXT NOT NULL,
    app_version TEXT NOT NULL,
    review_timestamp TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    text_sha TEXT NOT NULL,
    canonical_id TEXT NOT NULL,          -- first review with this exact text (itself if first)
    status TEXT NOT NULL,                -- pending | completed | quarantined
    reason TEXT                          -- quarantine reason
);
CREATE INDEX IF NOT EXISTS reviews_text ON reviews(text_sha);
CREATE INDEX IF NOT EXISTS reviews_status ON reviews(status);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads while a writer commits
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def ingest(csv_path, db_path):
    started = time.time()
    con = connect(db_path)
    if con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]:
        raise SystemExit(f"{db_path} already holds an ingested file; use a fresh --db per input.")

    first_by_text, dup_ids, ratings = {}, 0, Counter()
    batch = []
    for idx, row in enumerate(csv_rows(csv_path)):
        text = row["review_text"]
        tsha = text_sha(text)
        empty = not text.strip()
        canonical = first_by_text.setdefault(tsha, row["review_id"]) if not empty else row["review_id"]
        batch.append((row["review_id"], idx, *(row[f] for f in FIELDS[1:]), row_sha(row), tsha, canonical,
                      "quarantined" if empty else "pending", "empty_review_text" if empty else None))
        if len(batch) >= 20000:
            dup_ids += _insert(con, batch)
            batch = []
    dup_ids += _insert(con, batch)
    con.commit()

    q = lambda sql: con.execute(sql).fetchone()[0]
    nonempty = q("SELECT COUNT(*) FROM reviews WHERE status='pending'")
    distinct = q("SELECT COUNT(DISTINCT text_sha) FROM reviews WHERE status='pending'")
    top_dups = con.execute(
        "SELECT review_text, COUNT(*) n FROM reviews WHERE status='pending' "
        "GROUP BY text_sha HAVING n > 1 ORDER BY n DESC LIMIT 15").fetchall()
    report = {
        "input_path": str(csv_path),
        "input_sha256": sha(csv_path),
        "official_profile": profile(csv_path),       # same helper the grader uses
        "records": q("SELECT COUNT(*) FROM reviews"),
        "duplicate_review_ids_rejected": dup_ids,
        "empty_review_text_quarantined": q("SELECT COUNT(*) FROM reviews WHERE reason='empty_review_text'"),
        "nonempty_to_classify": nonempty,
        "distinct_nonempty_texts": distinct,
        "exact_duplicate_rows_reusable": nonempty - distinct,
        "missing_app_version": q("SELECT COUNT(*) FROM reviews WHERE TRIM(app_version)=''"),
        "most_repeated_texts": [{"text": t[:80], "count": n} for t, n in top_dups],
        "runtime_seconds": round(time.time() - started, 2),
        "model_calls": 0,
    }
    con.execute("INSERT OR REPLACE INTO meta VALUES ('input_sha256', ?)", (report["input_sha256"],))
    con.commit()
    return report


def _insert(con, batch):
    before = con.total_changes
    con.executemany("INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", batch)
    return len(batch) - (con.total_changes - before)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--db", required=True)
    ap.add_argument("--report", help="defaults to ingestion_report.json beside the db")
    args = ap.parse_args()
    report = ingest(args.csv, args.db)
    out = Path(args.report or Path(args.db).with_name("ingestion_report.json"))
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in ("official_profile", "most_repeated_texts")}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
