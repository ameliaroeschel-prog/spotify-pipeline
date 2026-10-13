// GET /api/summary — headline metrics, label distributions and the monthly
// complaint share (with its denominators), all read from Postgres.
import { query, route } from '../lib/db.js';

export default route(async (req, runId) => {
  const [run] = await query(
    'SELECT run_id, loaded_at, summary, counts FROM runs WHERE run_id = $1', [runId]);
  const dist = await query(
    `SELECT dimension, label, n, complaints FROM agg_distribution
     WHERE run_id = $1 ORDER BY dimension, n DESC, label`, [runId]);
  const monthly = await query(
    `SELECT review_month AS month, total, completed, complaints, cancellations
     FROM agg_monthly WHERE run_id = $1 ORDER BY review_month`, [runId]);

  const s = run.summary || {};
  const c = run.counts || {};
  const distributions = {};
  for (const d of dist) {
    (distributions[d.dimension] ||= []).push({ label: d.label, n: d.n, complaints: d.complaints });
  }
  // Severity and star rating read better in natural order (1..5).
  for (const k of ['severity', 'rating']) {
    if (distributions[k]) distributions[k].sort((a, b) => a.label.localeCompare(b.label));
  }

  return {
    run_id: run.run_id,
    synthetic: Boolean(s.synthetic),
    loaded_at: run.loaded_at,
    counts: {
      ...c,
      // complaint share = (complaint + cancellation) / completed
      complaint_share: c.completed ? c.complaints / c.completed : null,
      cancellation_share: c.completed ? c.cancellations / c.completed : null,
    },
    pipeline: {
      // Reported by the pipeline itself (run_summary.json, stored in runs.summary)
      source_rows: s.source_rows, completed: s.completed, quarantined: s.quarantined,
      distinct_texts: s.distinct_texts, cache_reuse_rows: s.cache_reuse_rows,
      cost_usd: s.cost_usd, wall_seconds: s.wall_seconds, model: s.model,
      label_config: s.label_config, golden: s.golden || null, verifier: s.verifier || null,
    },
    distributions,
    monthly: monthly.map((m) => ({
      ...m, complaint_share: m.completed ? m.complaints / m.completed : null,
    })),
  };
});
