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
    render_memo(con, grading_dir.parent / "memo.md")
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
    # Area aggregates the memo cites as [A1]..[A4] (code-computed; not issue-level claims)
    with open(out / "aggregates.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["aggregate_id", "area", "topics", "complaints", "severity_sum"])
        for area, v in pack.get("area_totals", {}).items():
            w.writerow([v.get("aggregate_id"), area, ";".join(v["topics"]), v["complaints"], v["severity_sum"]])
    status = Counter(r["status"] for r, _ in rows)
    golden_path = ROOT / "evals/golden_summary.json"
    golden = json.loads(golden_path.read_text()) if golden_path.exists() else {}
    vnotes = con.execute("SELECT notes FROM stages WHERE stage='verify' ORDER BY started DESC LIMIT 1").fetchone()
    vnotes = json.loads(vnotes[0]) if vnotes and vnotes[0] else {}
    run_id = con.execute("SELECT run_id FROM runs ORDER BY started DESC LIMIT 1").fetchone()
    (out / "run_summary.json").write_text(json.dumps({
        "run_id": run_id[0] if run_id else None, "synthetic": False, "source_rows": len(rows),
        "completed": status.get("completed", 0), "quarantined": status.get("quarantined", 0),
        "pending": status.get("pending", 0),
        "quarantine_reasons": dict(Counter(r.get("reason") for r, _ in rows if r["status"] == "quarantined")),
        "distinct_texts": con.execute("SELECT COUNT(DISTINCT text_sha) FROM reviews WHERE status<>'quarantined' OR reason<>'empty_review_text'").fetchone()[0],
        "cache_reuse_rows": sum(1 for r, _ in rows if r.get("cache_source_id")),
        "golden": {k: golden.get(k) for k in ("n", "topic_agreement", "intent_agreement", "severity_exact", "severity_mae",
                                               "label_config")},
        "verifier": {k: vnotes.get(k) for k in ("sample_size", "topic_agreement", "intent_agreement", "severity_exact",
                                                 "severity_mae")},
        "cost_usd": round(con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0], 6),
        "cost_by_role_usd": {r: round(c or 0, 6) for r, c in con.execute("SELECT role, SUM(cost_usd) FROM calls GROUP BY role")},
        "wall_seconds": round(con.execute("SELECT COALESCE(SUM(ended-started),0) FROM stages").fetchone()[0], 1),
        "model": "gpt-6-luna",
        "label_config": next((r["label_config"] for r, _ in rows if r.get("label_config")), None),
    }, indent=2) + "\n", encoding="utf-8")
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


def render_memo(con, path):
    """memo.md: the model's validated prose plus code-generated tables, so every number is traceable."""
    row = con.execute("SELECT memo_json, claims_json, pack_json, validation FROM memo ORDER BY created DESC LIMIT 1").fetchone()
    if not row or not row[0] or row[0] == "null":
        return False
    memo, claims, pack, validation = (json.loads(x) for x in row)
    names = {i["issue_id"]: i["name"] for i in pack["issues"]}
    quotes = {e["review_id"]: e for e in pack["evidence"]}
    L = ["# Decision memo: where should next quarter's product effort go?", "",
         f"*Source: {pack['source_rows']:,} Google Play reviews (May 2022 – Nov 2023); {pack['completed']:,} classified, "
         f"{pack['quarantined']:,} quarantined, {pack['pending']:,} unprocessed. Written by the memo-writer role "
         f"(gpt-6-luna) from saved aggregates only; every number and ID below was checked by code "
         f"(validation: {'passed' if validation['passed'] else 'FAILED: ' + '; '.join(validation['problems'])}).*", "",
         f"**Recommendation: {memo['priority_area'].replace('_', '/')} — lead issue `{memo['priority_issue_id']}` "
         f"({names.get(memo['priority_issue_id'], '')})**", "", memo["recommendation"], "", "## Alternatives considered", ""]
    L += [f"- **`{a['issue_id']}`** ({names.get(a['issue_id'], '')}): {a['why_not']}" for a in memo["alternatives"]]
    L += ["", "## Limitations", ""] + [f"- {x}" for x in memo["limitations"]]
    L += ["", "## Area totals cited as [A#] (code-computed, `aggregates.csv`)", "",
          "| ID | Area | Topics | Complaint + cancellation reviews | Severity sum |", "|---|---|---|---|---|"]
    L += [f"| A{k} | {a} | {', '.join(v['topics'])} | {v['complaints']:,} | {v['severity_sum']:,} |"
          for k, (a, v) in enumerate(pack["area_totals"].items(), 1)]
    L += ["", "## Issue claims cited as [C#] (`grading/claims.csv`, recomputed by the checker)", "",
          "| Claim | Issue | Metric | Value |", "|---|---|---|---|"]
    L += [f"| {c['claim_id']} | `{c['issue_id']}` {names.get(c['issue_id'], '')} | {c['metric']} | {c['value']} |" for c in claims]
    L += ["", "## Evidence quotes cited", ""]
    for rid in memo["cited_review_ids"]:
        e = quotes.get(rid)
        if e:
            L.append(f"- `{rid}` ({e['issue_id']}, severity {e['severity']}): \"{e['quote']}\"")
    L += ["", "*Reviews are self-selected public feedback. Cancellation language is stated intent, not observed churn. "
          "No revenue, plan tier or retention data exists in this dataset, so no revenue or churn impact is claimed.*", ""]
    Path(path).write_text("\n".join(L), encoding="utf-8")
    return True


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
