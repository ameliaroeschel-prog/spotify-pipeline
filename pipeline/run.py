"""Orchestrator: runs all six stages on any review CSV, no manual chat pasting.

    ingest (code) -> enrich (Luna, batches of 50) -> verify (Decisions API, declared sample)
    -> group (code membership + Luna names) -> rank (code) -> memo (Luna, code-validated) -> export (code)

Code decides what runs next, owns the queue, budget, validation, counts and arithmetic; models only
read language. Re-running on the same --run-dir resumes: completed work is reused, not re-bought.

    python3 -m pipeline.run data/cost_100.csv --run-dir runs/pilot --provider openai --workers 1 --budget 0.25 --verify-rate 0.2
"""

import argparse
import json
import time
from pathlib import Path

from pipeline import config as C
from pipeline.enrich import Enricher, label_config_for
from pipeline.export import export
from pipeline.group import group
from pipeline.ingest import ingest
from pipeline.memo import write_memo
from pipeline.state import new_run_id, open_state, stage
from pipeline.verify import verify


def run(args):
    started = time.time()
    run_dir = Path(args.run_dir)
    db = run_dir / "state.db"
    run_id = new_run_id()
    model = C.MODEL if args.provider == "openai" else "mock-keyword-v1"
    summary = {"run_id": run_id, "input": args.csv, "provider": args.provider, "model": model,
               "label_config": label_config_for(args.provider), "service_tier": args.service_tier,
               "workers": args.workers, "budget_usd": args.budget, "verify_rate": args.verify_rate}

    if not db.exists():
        t = time.time()
        report = ingest(args.csv, db)
        (run_dir / "ingestion_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        con = open_state(db)
        with con:
            con.execute("INSERT INTO stages VALUES (?,?,?,?,?,?,?)", (run_id, "ingest", t, time.time(),
                        report["records"], report["nonempty_to_classify"], json.dumps({"model_calls": 0})))
        summary["ingest"] = "fresh"
    else:
        summary["ingest"] = "reused saved ingestion"
    con = open_state(db)

    enricher = Enricher(db, args.provider, args.workers, args.budget, label_config_for(args.provider), model,
                        args.service_tier, args.stop_after_batches, run_id=run_id)
    with stage(con, run_id, "enrich") as info:
        enrich = enricher.run(args.limit_texts)
        info["items_out"], info["notes"] = enrich["completed_texts"], enrich
    summary["enrich"] = enrich
    if enrich["stopped_reason"]:
        summary["stopped_after"] = "enrich: " + enrich["stopped_reason"]
        return finish(summary, run_dir, started, con)

    verify_summary, disagreements = verify(db, args.provider, args.verify_rate, args.verify_min, args.budget,
                                           max(args.workers, 2), run_id=run_id)
    (run_dir / "verify_disagreements.json").write_text(json.dumps(disagreements, indent=2, ensure_ascii=False) + "\n")
    summary["verify"] = verify_summary
    summary["group"] = group(db, args.provider, args.budget, run_id=run_id)
    memo, pack, claims, validation = write_memo(db, args.provider, args.budget, run_id=run_id)
    summary["memo_validation"] = validation
    with stage(con, run_id, "export") as info:
        summary["export"] = export(db, args.csv, run_dir / "grading", run_dir / "export")
        info["items_out"] = summary["export"]["records"]
    return finish(summary, run_dir, started, con)


def finish(summary, run_dir, started, con):
    summary["wall_seconds"] = round(time.time() - started, 2)
    summary["spent_usd_all_runs_on_this_dir"] = round(
        con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0], 6)
    summary["new_calls_this_run"] = dict(con.execute(
        "SELECT role, COUNT(*) FROM calls WHERE run_id=? GROUP BY role", (summary["run_id"],)).fetchall())
    summary["status_counts"] = dict(con.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status").fetchall())
    with open(run_dir / "run_log.jsonl", "a", encoding="utf-8") as f:
        for row in con.execute("SELECT run_id, stage, started, ended, items_in, items_out, notes FROM stages WHERE run_id=?",
                               (summary["run_id"],)):
            f.write(json.dumps(dict(zip(("run_id", "stage", "started", "ended", "items_in", "items_out", "notes"), row)),
                               ensure_ascii=False) + "\n")
    (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


def main():
    ap = argparse.ArgumentParser(description="Run the full staged pipeline on a review CSV")
    ap.add_argument("csv")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--provider", choices=["openai", "mock"], default="mock")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--budget", type=float, required=True, help="hard USD cap for all model calls in this run dir")
    ap.add_argument("--service-tier", choices=["standard", "flex"], default=C.DEFAULT_SERVICE_TIER)
    ap.add_argument("--verify-rate", type=float, default=0.01)
    ap.add_argument("--verify-min", type=int, default=20)
    ap.add_argument("--limit-texts", type=int)
    ap.add_argument("--stop-after-batches", type=int, help="interruption demo: stop enrichment after N batches")
    args = ap.parse_args()
    C.load_env()
    summary = run(args)
    print(json.dumps({k: v for k, v in summary.items() if k not in ("enrich", "verify", "group")}, indent=2))


if __name__ == "__main__":
    main()
