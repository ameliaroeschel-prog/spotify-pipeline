// GET /api/ranking — the baseline issue ranking (ranking.csv), with issue names.
import { query, route } from '../lib/db.js';

export default route(async (req, runId) => {
  const rows = await query(
    `SELECT r.rank, r.issue_id, i.name, i.topic, r.complaint_count, r.severity_sum,
            r.mean_severity::text AS mean_severity, r.priority_score,
            COALESCE(t.cancellations, 0)::int AS cancellations
     FROM ranking r
     LEFT JOIN issues i ON i.run_id = r.run_id AND i.issue_id = r.issue_id
     LEFT JOIN (SELECT issue_id, sum(cancellations) AS cancellations FROM agg_issue_monthly
                WHERE run_id = $1 GROUP BY issue_id) t ON t.issue_id = r.issue_id
     WHERE r.run_id = $1
     ORDER BY r.rank`, [runId]);
  const [tot] = await query(
    `SELECT (counts->>'complaints')::int AS complaints FROM runs WHERE run_id = $1`, [runId]);
  return { run_id: runId, total_complaints: tot ? tot.complaints : null, issues: rows };
});
