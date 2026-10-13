#!/bin/bash
# Interruption/resume demonstration for the full run (recorded for the grader).
# Shows: saved progress -> resume with identical settings -> new completed work -> no completed ID re-sent.
cd "$(dirname "$0")/.." || exit 1
DB=runs/full/state.db
echo "=== 1. Progress saved before the interruption ==="
python3 -c "import json;d=json.load(open('runs/full/checkpoint_before.json'));print('checkpoint_before.json:', d['count'], 'completed review IDs')"
sqlite3 "$DB" "SELECT '   reviews by status: ' || group_concat(status || '=' || n, ', ') FROM (SELECT status, COUNT(*) n FROM reviews GROUP BY status);"
echo
echo "=== 2. Resuming with the same settings (completed reviews are skipped, not re-bought) ==="
python3 -m pipeline.run data/spotify_reviews_18months.csv --run-dir runs/full --provider openai \
  --workers 12 --budget 8 --verify-rate 0.01 --stop-after-batches 20 2>&1 | grep -E "^\[resume\]|stopped_after|spent_usd"
echo
echo "=== 3. Progress after resuming ==="
python3 -m pipeline.export checkpoint --db "$DB" --out runs/full/checkpoint_after.json
python3 - <<'PY'
import json, sqlite3
before = set(json.load(open("runs/full/checkpoint_before.json"))["completed_ids"])
after = set(json.load(open("runs/full/checkpoint_after.json"))["completed_ids"])
con = sqlite3.connect("runs/full/state.db")
resent = sum(len(before & set(json.loads(ids))) for (ids,) in
             con.execute("SELECT review_ids FROM calls WHERE role='enrich' AND phase='resume'"))
calls = con.execute("SELECT COUNT(*) FROM calls WHERE role='enrich' AND phase='resume'").fetchone()[0]
print(f"before: {len(before):,} completed   after: {len(after):,} completed   new: {len(after - before):,}")
print(f"every 'before' ID still completed: {before <= after}")
print(f"resume-phase enrichment calls: {calls}; completed IDs re-sent to the model: {resent}")
PY
