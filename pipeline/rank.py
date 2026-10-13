"""Stage 5 — Rank. Code only, no model: recomputes the baseline ranking from saved records and
saved issue membership, so it is reproducible with no API key.

priority(issue) = complaint_count x mean_severity = severity_sum. Sort by descending score, then
ascending issue_id. Means use six decimals, half-up (same as the grading checker).

    python3 -m pipeline.rank --records grading/records.jsonl.gz --membership grading/membership.csv --out grading/ranking.csv
"""

import argparse
import csv
import gzip
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path


def mean_string(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def compute_ranking(severity_by_review, membership):
    """severity_by_review: {review_id: int} for completed complaint/cancellation records.
    membership: iterable of (issue_id, review_id). Each (issue, review) pair counts once."""
    members, seen = defaultdict(list), set()
    for issue_id, review_id in membership:
        if (issue_id, review_id) in seen or review_id not in severity_by_review:
            continue
        seen.add((issue_id, review_id))
        members[issue_id].append(severity_by_review[review_id])
    rows = [{"issue_id": iid, "complaint_count": len(v), "severity_sum": sum(v),
             "mean_severity": mean_string(sum(v), len(v)), "priority_score": sum(v)} for iid, v in members.items()]
    rows.sort(key=lambda r: (-r["priority_score"], r["issue_id"]))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def write_ranking(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity",
                                          "priority_score"])
        w.writeheader()
        w.writerows({k: r[k] for k in w.fieldnames} for r in rows)


def read_records(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def main():
    ap = argparse.ArgumentParser(description="Regenerate the baseline ranking from saved outputs (no model calls)")
    ap.add_argument("--records", required=True)
    ap.add_argument("--membership", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    sev = {r["review_id"]: r["severity"] for r in read_records(args.records)
           if r.get("status") == "completed" and r.get("intent") in ("complaint", "cancellation")}
    with open(args.membership, encoding="utf-8", newline="") as f:
        membership = [(r["issue_id"], r["review_id"]) for r in csv.DictReader(f)]
    rows = compute_ranking(sev, membership)
    write_ranking(rows, Path(args.out))
    print(f"ranked {len(rows)} issues from {len(membership)} memberships -> {args.out}")
    for r in rows[:10]:
        print(f"  {r['rank']:>2}. {r['issue_id']:<34} count={r['complaint_count']:<7} mean={r['mean_severity']} "
              f"score={r['priority_score']}")


if __name__ == "__main__":
    main()
