"""Export — writes the standardized grading/ folder (GRADING_CONTRACT.md) and a dashboard export from
saved state. No model calls.

    python3 -m pipeline.export --db runs/full/state.db --csv data/spotify_reviews_18months.csv --grading grading
    python3 -m pipeline.export checkpoint --db runs/full/state.db --out grading/checkpoint_before.json
"""

import argparse
import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

from pipeline.config import ROOT
from pipeline.rank import compute_ranking, write_ranking
from pipeline.state import open_state

sys.path.insert(0, str(ROOT))
from check_submission import profile, sha  # noqa: E402


def records(con):
    """Exactly one final record per source ID. Cache reuses point directly at the original."""
    q = con.execute("""
        SELECT r.review_id, r.source_sha256, r.status, r.reason, r.review_timestamp, r.review_rating,
               x.origin_id, x.topic, x.intent, x.sentiment, x.severity, x.entities, x.evidence_quote,
               x.needs_review, x.label_config, m.issue_id
        FROM reviews r
        LEFT JOIN results x ON x.text_sha = r.text_sha AND r.status = 'completed'
        LEFT JOIN membership m ON m.review_id = r.review_id
        ORDER BY r.row_idx""")
    for (rid, src, status, reason, ts, rating, origin, topic, intent, sent, sev, ents, quote, nr, cfg, issue) in q:
        extra = {"review_month": ts[:7], "review_rating": rating, "issue_id": issue}
        if status == "completed":
            rec = {"review_id": rid, "source_sha256": src, "status": "completed", "topic": topic, "intent": intent,
                   "sentiment": sent, "severity": sev, "entities": json.loads(ents), "evidence_quote": quote,
                   "needs_review": bool(nr), "label_config": cfg}
            if origin != rid:
                rec["cache_source_id"] = origin
        elif status == "quarantined":
            rec = {"review_id": rid, "source_sha256": src, "status": "quarantined", "reason": reason}
        else:
            rec = {"review_id": rid, "source_sha256": src, "status": "pending", "reason": "not_processed"}
        yield rec, extra


def write_jsonl_gz(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def export(db, csv_path, grading_dir, dash_dir=None, allow_multi_issue=False):
    con = open_state(db)
    grading_dir, n = Path(grading_dir), 0
    grading_dir.mkdir(parents=True, exist_ok=True)
    (grading_dir / "run.json").write_text(json.dumps({
        "version": "a5-audit-v1", "analysis_count": con.execute("SELECT COUNT(*) FROM reviews").fetchone()[0],
        "analysis_sha256": sha(csv_path), "classification_input_fields": ["review_text"],
        "allow_multi_issue": allow_multi_issue}) + "\n", encoding="utf-8")
    (grading_dir / "ingestion.json").write_text(
        json.dumps(profile(csv_path), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    rows = list(records(con))
    write_jsonl_gz(grading_dir / "records.jsonl.gz", (r for r, _ in rows))
    status = Counter(r["status"] for r, _ in rows)

    membership = con.execute("SELECT issue_id, review_id FROM membership ORDER BY issue_id, review_id").fetchall()
    with open(grading_dir / "membership.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["issue_id", "review_id"])
        w.writerows(membership)
    sev = {r["review_id"]: r["severity"] for r, _ in rows
           if r["status"] == "completed" and r["intent"] in ("complaint", "cancellation")}
    ranking = compute_ranking(sev, membership)
    write_ranking(ranking, grading_dir / "ranking.csv")

    memo_row = con.execute("SELECT memo_json, claims_json, pack_json, validation FROM memo ORDER BY created DESC LIMIT 1").fetchone()
    claims = json.loads(memo_row[1]) if memo_row else []
    with open(grading_dir / "claims.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["claim_id", "issue_id", "metric", "value"])
        w.writeheader()
        w.writerows(claims)

    with gzip.open(grading_dir / "calls.jsonl.gz", "wt", encoding="utf-8") as f:
        for (aid, req, role, phase, model, cfg, ids, outcome, itok, otok, tier, run_id) in con.execute(
                "SELECT attempt_id, request_id, role, phase, model, label_config, review_ids, outcome, input_tokens,"
                " output_tokens, service_tier, run_id FROM calls ORDER BY started"):
            f.write(json.dumps({"request_id": req or f"failed-attempt-{aid}", "role": role,
                                "review_ids": json.loads(ids or "[]"), "model": model, "phase": phase,
                                "outcome": outcome, "label_config": cfg,
                                # checker requires integers; unknown usage (e.g. failed/timed-out attempt) is
                                # exported as 0 and explicitly marked unavailable, never silently invented
                                "input_tokens": itok or 0, "output_tokens": otok or 0,
                                "usage_status": "reported" if itok is not None else "unavailable",
                                "service_tier": tier, "run_id": run_id}) + "\n")

    if dash_dir:
        export_dashboard(con, rows, ranking, membership, memo_row, Path(dash_dir))
    return {"records": len(rows), "status_counts": dict(status), "issues": len(ranking),
            "memberships": len(membership), "claims": len(claims)}


def export_dashboard(con, rows, ranking, membership, memo_row, out):
    """Dashboard input contract (see dashboard/README.md)."""
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl_gz(out / "records.jsonl.gz", ({**r, **x} for r, x in rows))
    with open(out / "membership.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["issue_id", "review_id"])
        w.writerows(membership)
    write_ranking(ranking, out / "ranking.csv")
    issues = [{"issue_id": i, "name": n, "description": d, "topic": t, "example_review_ids": json.loads(ex)}
              for i, t, n, d, ex in con.execute("SELECT issue_id, topic, name, description, example_review_ids FROM issues")]
    (out / "issues.json").write_text(json.dumps(issues, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    memo, claims, pack = (json.loads(memo_row[0]), json.loads(memo_row[1]), json.loads(memo_row[2])) if memo_row else ({}, [], {})
    with open(out / "claims.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["claim_id", "issue_id", "metric", "value"])
        w.writeheader()
        w.writerows(claims)
    if memo:
        memo = {**memo, "priority_issue_id": memo.get("priority_issue_id"), "model": "gpt-6-luna",
                "generated_at": con.execute("SELECT MAX(created) FROM memo").fetchone()[0]}
    (out / "memo.json").write_text(json.dumps(memo, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    cited = set((memo or {}).get("cited_review_ids", []))
    examples = {rid for i in issues for rid in i["example_review_ids"]} | cited | {e["review_id"] for e in pack.get("evidence", [])}
    with open(out / "evidence.jsonl", "w", encoding="utf-8") as f:
        for rid in sorted(examples):
            row = con.execute("""SELECT r.review_text, r.review_rating, r.review_timestamp, x.evidence_quote, x.topic,
                                        x.intent, x.severity, m.issue_id FROM reviews r
                                 JOIN results x ON x.text_sha = r.text_sha LEFT JOIN membership m ON m.review_id = r.review_id
                                 WHERE r.review_id = ?""", (rid,)).fetchone()
            if row:
                f.write(json.dumps({"review_id": rid, "review_text": row[0], "review_rating": row[1],
                                    "review_month": row[2][:7], "evidence_quote": row[3], "topic": row[4],
                                    "intent": row[5], "severity": row[6], "issue_id": row[7]}, ensure_ascii=False) + "\n")


def checkpoint(db, out):
    con = open_state(db)
    ids = [r[0] for r in con.execute("SELECT review_id FROM reviews WHERE status='completed' ORDER BY review_id")]
    Path(out).write_text(json.dumps({"completed_ids": ids, "count": len(ids)}) + "\n", encoding="utf-8")
    return len(ids)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "checkpoint":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--db", required=True)
        ap.add_argument("--out", required=True)
        a = ap.parse_args()
        print(f"saved {checkpoint(a.db, a.out)} completed_ids -> {a.out}")
        return
    ap = argparse.ArgumentParser(description="Write grading/ and dashboard exports from saved state")
    ap.add_argument("--db", required=True)
    ap.add_argument("--csv", required=True, help="the source CSV the run ingested")
    ap.add_argument("--grading", required=True)
    ap.add_argument("--dashboard", help="optional dashboard export dir")
    a = ap.parse_args()
    print(json.dumps(export(a.db, a.csv, a.grading, a.dashboard), indent=2))


if __name__ == "__main__":
    main()
