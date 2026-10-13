"""Build a SYNTHETIC state.db for testing the calculator. Nothing here is a measured pilot.

The review rows are the real 100 rows of data/cost_100.csv (so ID and checksum checks can pass),
but every label, call, token count and timestamp is invented with round numbers so the
arithmetic can be checked by hand:

  cold run "SYNTHETIC-cold"
    enrich req-1 attempt 1  failed (timeout)     usage unknown (NULL)             -> unresolved
    enrich req-1 attempt 2  standard  in 3,000 (1,000 cached)  out 2,000           -> $0.001210
    enrich req-2            flex      in 2,500 (0 cached)      out 1,500           -> $0.000500
    enrich req-3 x2         failed    usage unknown, 2 texts never completed      -> unresolved
    verify req-4            decisions in   800                 out 0               -> $0.000080
    group  req-5            standard  in 5,000                 out 400             -> $0.000700
    memo   req-6            standard  in 2,000                 out 800             -> $0.000600
                                                               cold priced total     $0.003090
  warm run "SYNTHETIC-warm": no API calls (result cache hit), stages only.

Usage: python3 cost/tests/make_fixture.py [out_path]   (default cost/tests/fixture/state.db)
"""

import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from check_submission import row_sha  # noqa: E402  (stdlib-only helper from the assignment)

LABEL = "SYNTHETIC-gpt-6-luna+effort-none+enrich-test+schema-test"
T0 = 1_800_000_000.0     # synthetic clock


def build(out_path):
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(out_path)
    con.executescript("""
    CREATE TABLE reviews (review_id TEXT PRIMARY KEY, row_idx INTEGER, review_text TEXT, review_rating TEXT,
        review_likes TEXT, app_version TEXT, review_timestamp TEXT, source_sha256 TEXT, text_sha TEXT,
        canonical_id TEXT, status TEXT, reason TEXT);
    CREATE TABLE results (text_sha TEXT, label_config TEXT, origin_id TEXT, topic TEXT, intent TEXT,
        sentiment REAL, severity INTEGER, entities TEXT, evidence_quote TEXT, needs_review INTEGER,
        quote_source TEXT, request_id TEXT, created REAL, PRIMARY KEY (text_sha, label_config));
    CREATE TABLE calls (attempt_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT, role TEXT, phase TEXT,
        model TEXT, label_config TEXT, review_ids TEXT, outcome TEXT, error TEXT, attempt INTEGER,
        input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
        cost_usd REAL, started REAL, ended REAL, service_tier TEXT);
    CREATE TABLE failures (review_id TEXT, label_config TEXT, attempts INTEGER, reason TEXT);
    CREATE TABLE runs (run_id TEXT PRIMARY KEY, phase TEXT, started REAL, ended REAL, settings TEXT, summary TEXT);
    CREATE TABLE stages (run_id TEXT, stage TEXT, started REAL, ended REAL, items_in INTEGER, items_out INTEGER,
        notes TEXT);
    """)

    with open(ROOT / "data" / "cost_100.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    canonical, order = {}, []
    for i, r in enumerate(rows):
        tsha = hashlib.sha256(r["review_text"].encode("utf-8")).hexdigest()
        if tsha not in canonical:
            canonical[tsha] = r["review_id"]
            order.append(tsha)
        r["_tsha"], r["_idx"] = tsha, i
    texts = order                                   # one canonical review per distinct text
    batch1, batch2, failed_texts = texts[:50], texts[50:-2], texts[-2:]
    id_of = canonical

    for r in rows:
        status = "pending" if r["_tsha"] in failed_texts else "completed"
        con.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            r["review_id"], r["_idx"], r["review_text"], r["review_rating"], r["review_likes"], r["app_version"],
            r["review_timestamp"], row_sha(r), r["_tsha"], canonical[r["_tsha"]], status, None))
        if status == "pending":
            con.execute("INSERT INTO failures VALUES (?,?,?,?)", (r["review_id"], LABEL, 2, "synthetic_timeout"))
    for tsha in batch1 + batch2:
        text = next(r["review_text"] for r in rows if r["_tsha"] == tsha)
        con.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            tsha, LABEL, id_of[tsha], "other", "unclear", 0.0, 1, "[]", text[:40], 1, "synthetic", "req-1", T0))

    def call(aid, req, run, role, ids, outcome, attempt, inp, cached, out, tier, start, end, model="gpt-6-luna"):
        con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            aid, req, run, role, "initial", model, LABEL if role == "enrich" else f"SYNTHETIC-{role}-v1",
            json.dumps([id_of[t] for t in ids]), outcome, None if outcome == "succeeded" else "synthetic timeout",
            attempt, inp, cached, out, None if inp is None else 0, None, T0 + start, T0 + end, tier))

    c = "SYNTHETIC-cold"
    call("a1", None, c, "enrich", batch1, "failed", 1, None, None, None, "standard", 1, 11)
    call("a2", "req-1", c, "enrich", batch1, "succeeded", 2, 3000, 1000, 2000, "standard", 13, 23)
    call("a3", "req-2", c, "enrich", batch2, "succeeded", 1, 2500, 0, 1500, "flex", 24, 34)
    call("a4", None, c, "enrich", failed_texts, "failed", 1, None, None, None, "standard", 35, 36)
    call("a5", None, c, "enrich", failed_texts, "failed", 2, None, None, None, "standard", 38, 39)
    call("a6", "req-4", c, "verify", batch1[:10], "succeeded", 1, 800, 0, 0, "decisions", 40, 45)
    call("a7", "req-5", c, "group", [], "succeeded", 1, 5000, 0, 400, "standard", 46, 56)
    call("a8", "req-6", c, "memo", [], "succeeded", 1, 2000, 0, 800, "standard", 57, 67)

    settings = {"provider": "openai", "model": "gpt-6-luna", "effort": "none", "batch_size": 50, "workers": 1,
                "budget": 0.25, "verify_rate": 0.1, "synthetic": True}
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (c, "initial", T0, T0 + 70, json.dumps(settings), "{}"))
    con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", ("SYNTHETIC-warm", "initial", T0 + 100, T0 + 105,
                                                          json.dumps(settings), "{}"))
    for run, base, spans in ((c, 0, [("ingest", 0, 1), ("enrich", 1, 39), ("verify", 40, 45), ("group", 46, 56),
                                      ("rank", 56, 57), ("memo", 57, 67), ("export", 67, 70)]),
                             ("SYNTHETIC-warm", 100, [("ingest", 0, 1), ("enrich", 1, 1.5), ("verify", 1.5, 2),
                                                      ("group", 2, 2.5), ("rank", 2.5, 3), ("memo", 3, 3.5),
                                                      ("export", 3.5, 5)])):
        for stage, a, b in spans:
            con.execute("INSERT INTO stages VALUES (?,?,?,?,?,?,?)",
                        (run, stage, T0 + base + a, T0 + base + b, None, None, "synthetic"))
    con.commit()
    con.close()
    return {"distinct_texts": len(texts), "enriched": len(batch1) + len(batch2), "failed_texts": len(failed_texts)}


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "fixture" / "state.db"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(build(out), "->", out)
