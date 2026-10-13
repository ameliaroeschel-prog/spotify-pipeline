"""Golden-set evaluation: compare saved enricher predictions with Amelia's hand labels.

The model never sees the human labels: the golden texts are enriched through the normal pipeline
(golden_50_to_label.csv has blank label columns), then code compares field by field.

    python3 -m pipeline.ingest data/golden_50_to_label.csv --db runs/golden/state.db
    python3 -m pipeline.enrich --db runs/golden/state.db --provider openai --budget 0.05
    python3 -m pipeline.evaluate --db runs/golden/state.db          # offline, no API calls
"""

import argparse
import csv
import json
import sqlite3
from collections import Counter

from pipeline.config import INTENTS, ROOT, TOPICS

SENTIMENT_TOLERANCE = 0.5   # predeclared: |pred - human| <= 0.5 counts as sentiment agreement


def load(db):
    con = sqlite3.connect(db)
    preds = {rid: dict(zip(("topic", "intent", "severity", "sentiment", "evidence_quote", "needs_review",
                            "entities", "quote_source", "label_config"), row))
             for rid, *row in con.execute("""
        SELECT r.review_id, x.topic, x.intent, x.severity, x.sentiment, x.evidence_quote, x.needs_review,
               x.entities, x.quote_source, x.label_config
        FROM reviews r JOIN results x ON x.text_sha = r.text_sha WHERE r.status='completed'""")}
    status = dict(con.execute("SELECT review_id, status FROM reviews"))
    return preds, status


def evaluate(db, out_dir):
    gold = list(csv.DictReader(open(ROOT / "evals/golden_labels.csv", encoding="utf-8")))
    preds, status = load(db)
    rows, n = [], len(gold)
    for g in gold:
        p = preds.get(g["review_id"])
        row = {"review_id": g["review_id"], "review_text": g["review_text"], "status": status.get(g["review_id"]),
               "human_topic": g["topic"], "model_topic": p and p["topic"],
               "human_intent": g["intent"], "model_intent": p and p["intent"],
               "human_severity": int(g["severity"]), "model_severity": p and p["severity"],
               "human_sentiment": float(g["sentiment"]), "model_sentiment": p and p["sentiment"],
               "human_needs_review": g["needs_review"] == "True", "model_needs_review": bool(p and p["needs_review"]),
               "model_quote": p and p["evidence_quote"], "quote_source": p and p["quote_source"],
               "quote_is_exact_substring": bool(p and p["evidence_quote"] in g["review_text"]),
               "model_entities": p and p["entities"]}
        # Missing predictions count as incorrect (they stay in every denominator).
        row["topic_match"] = row["human_topic"] == row["model_topic"]
        row["intent_match"] = row["human_intent"] == row["model_intent"]
        row["severity_exact"] = row["human_severity"] == row["model_severity"]
        row["severity_abs_err"] = abs(row["human_severity"] - row["model_severity"]) if p else 4
        row["sentiment_abs_err"] = abs(row["human_sentiment"] - row["model_sentiment"]) if p else 2.0
        row["sentiment_within_tol"] = row["sentiment_abs_err"] <= SENTIMENT_TOLERANCE
        row["all_core_match"] = row["topic_match"] and row["intent_match"] and row["severity_exact"]
        rows.append(row)

    def rate(k):
        return round(sum(r[k] for r in rows) / n, 3)

    def f1_macro(field, labels):
        f1s = []
        for lab in labels:
            tp = sum(r[f"human_{field}"] == lab and r[f"model_{field}"] == lab for r in rows)
            fp = sum(r[f"human_{field}"] != lab and r[f"model_{field}"] == lab for r in rows)
            fn = sum(r[f"human_{field}"] == lab and r[f"model_{field}"] != lab for r in rows)
            if tp + fp + fn:
                f1s.append(2 * tp / (2 * tp + fp + fn))
        return round(sum(f1s) / len(f1s), 3) if f1s else None

    hr = [r for r in rows if r["human_needs_review"]]
    mr = [r for r in rows if r["model_needs_review"]]
    summary = {
        "n": n, "predictions_present": sum(r["model_topic"] is not None for r in rows),
        "label_config": next((p["label_config"] for p in preds.values()), None),
        "topic_agreement": rate("topic_match"), "topic_macro_f1": f1_macro("topic", TOPICS),
        "intent_agreement": rate("intent_match"), "intent_macro_f1": f1_macro("intent", INTENTS),
        "severity_exact": rate("severity_exact"),
        "severity_mae": round(sum(r["severity_abs_err"] for r in rows) / n, 3),
        "sentiment_mae": round(sum(r["sentiment_abs_err"] for r in rows) / n, 3),
        "sentiment_within_0.5": rate("sentiment_within_tol"),
        "all_three_core_fields_match": rate("all_core_match"),
        "quote_exact_substring_rate": rate("quote_is_exact_substring"),
        "ambiguous_cases_human_flagged": len(hr),
        "needs_review_model_flagged": len(mr),
        "needs_review_overlap": sum(r["human_needs_review"] and r["model_needs_review"] for r in rows),
        "per_topic_counts_human": dict(Counter(r["human_topic"] for r in rows)),
        "per_topic_counts_model": dict(Counter(r["model_topic"] for r in rows)),
        "topic_confusion": {f"{h}->{m}": c for (h, m), c in Counter(
            (r["human_topic"], r["model_topic"]) for r in rows if not r["topic_match"]).most_common()},
        "intent_confusion": {f"{h}->{m}": c for (h, m), c in Counter(
            (r["human_intent"], r["model_intent"]) for r in rows if not r["intent_match"]).most_common()},
        "severity_confusion": {f"{h}->{m}": c for (h, m), c in Counter(
            (r["human_severity"], r["model_severity"]) for r in rows if not r["severity_exact"]).most_common()},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "golden_per_case.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out_dir / "golden_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary, rows


def main():
    ap = argparse.ArgumentParser(description="Offline golden-set comparison (no API calls)")
    ap.add_argument("--db", default="runs/golden/state.db")
    ap.add_argument("--out", default="evals")
    args = ap.parse_args()
    summary, rows = evaluate(args.db, ROOT / args.out)
    print(json.dumps({k: v for k, v in summary.items() if "confusion" not in k and "counts" not in k}, indent=2))
    print("topic confusion:", summary["topic_confusion"])
    print("intent confusion:", summary["intent_confusion"])
    print("severity confusion:", summary["severity_confusion"])


if __name__ == "__main__":
    main()
