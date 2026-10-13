"""Additional trend analysis (code only, no model calls): complaint share by month with denominators,
and issue shares in comparable periods a year apart. 2022-05 and 2023-11 are partial months
(the window is 2022-05-17 to 2023-11-15), so they are flagged and excluded from period comparisons.

    python3 -m pipeline.trends --db runs/full/state.db --out analysis
"""

import argparse
import csv
import json
from pathlib import Path

from pipeline.state import open_state

PARTIAL_MONTHS = {"2022-05", "2023-11"}
PERIODS = {"2022-06..2022-10": ("2022-06", "2022-10"), "2023-06..2023-10": ("2023-06", "2023-10")}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default="analysis")
    args = ap.parse_args()
    con, out = open_state(args.db), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    monthly = con.execute("""
        SELECT substr(r.review_timestamp,1,7) m, COUNT(*) completed,
               SUM(x.intent IN ('complaint','cancellation')) complaints, SUM(x.intent='cancellation') cancellations
        FROM reviews r JOIN results x ON x.text_sha = r.text_sha WHERE r.status='completed' GROUP BY m ORDER BY m""").fetchall()
    with open(out / "monthly_complaint_share.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["month", "completed_reviews", "complaint_or_cancellation", "share", "cancellation_intent", "partial_month"])
        for m, n, c, k in monthly:
            w.writerow([m, n, c, f"{c / n:.4f}", k, m in PARTIAL_MONTHS])

    def period_counts(lo, hi):
        total = con.execute("""SELECT COUNT(*) FROM reviews r JOIN results x ON x.text_sha=r.text_sha
            WHERE r.status='completed' AND x.intent IN ('complaint','cancellation')
              AND substr(r.review_timestamp,1,7) BETWEEN ? AND ?""", (lo, hi)).fetchone()[0]
        by_issue = dict(con.execute("""SELECT m.issue_id, COUNT(*) FROM membership m JOIN reviews r USING(review_id)
            WHERE substr(r.review_timestamp,1,7) BETWEEN ? AND ? GROUP BY 1""", (lo, hi)).fetchall())
        return total, by_issue

    (n1, a), (n2, b) = (period_counts(*PERIODS[p]) for p in PERIODS)
    rows = []
    for issue in sorted(set(a) | set(b), key=lambda i: -(b.get(i, 0) / n2 - a.get(i, 0) / n1)):
        rows.append({"issue_id": issue, "complaints_2022": a.get(issue, 0), "share_2022": round(a.get(issue, 0) / n1, 4),
                     "complaints_2023": b.get(issue, 0), "share_2023": round(b.get(issue, 0) / n2, 4),
                     "share_change_pts": round(100 * (b.get(issue, 0) / n2 - a.get(issue, 0) / n1), 2)})
    with open(out / "issue_share_comparable_periods.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summary = {"periods": list(PERIODS), "complaint_denominators": {"2022-06..2022-10": n1, "2023-06..2023-10": n2},
               "partial_months_excluded": sorted(PARTIAL_MONTHS), "largest_share_increases": rows[:5],
               "largest_share_decreases": rows[-5:][::-1],
               "note": "Shares of complaint/cancellation reviews in each period. Review volume itself spikes in "
                       "2023-07 and 2023-10, so compare shares, not counts. Self-selected reviews; descriptive only."}
    (out / "trends_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2)[:2500])


if __name__ == "__main__":
    main()
