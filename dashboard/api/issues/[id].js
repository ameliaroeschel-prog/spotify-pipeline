// GET /api/issues/:id — one issue: ranking stats, label breakdown, monthly
// trend (with the monthly denominators) and evidence reviews.
import { query, route, pathId } from '../../lib/db.js';

export default route(async (req, runId) => {
  const id = pathId(req);
  const [issue] = await query(
    `SELECT i.issue_id, i.name, i.description, i.topic, i.example_review_ids,
            r.rank, r.complaint_count, r.severity_sum, r.mean_severity::text AS mean_severity,
            r.priority_score
     FROM issues i LEFT JOIN ranking r ON r.run_id = i.run_id AND r.issue_id = i.issue_id
     WHERE i.run_id = $1 AND i.issue_id = $2`, [runId, id]);
  if (!issue) return null;

  // Live breakdown of this issue's member records (uses records_issue_idx).
  const [stats] = await query(
    `SELECT count(*)::int AS members,
            count(*) FILTER (WHERE intent = 'cancellation')::int AS cancellations,
            count(*) FILTER (WHERE needs_review)::int AS needs_review,
            round(avg(sentiment)::numeric, 3)::float8 AS mean_sentiment,
            round(avg(review_rating)::numeric, 2)::float8 AS mean_rating
     FROM records WHERE run_id = $1 AND issue_id = $2`, [runId, id]);
  const severity = await query(
    `SELECT severity, count(*)::int AS n FROM records
     WHERE run_id = $1 AND issue_id = $2 GROUP BY severity ORDER BY severity`, [runId, id]);
  const monthly = await query(
    `SELECT m.review_month AS month, COALESCE(a.n, 0) AS n, COALESCE(a.cancellations, 0) AS cancellations,
            m.completed, m.complaints
     FROM agg_monthly m
     LEFT JOIN agg_issue_monthly a
       ON a.run_id = m.run_id AND a.review_month = m.review_month AND a.issue_id = $2
     WHERE m.run_id = $1 ORDER BY m.review_month`, [runId, id]);
  const evidence = await query(
    `SELECT review_id, review_text, evidence_quote, intent, severity, review_rating, review_month, cited_in_memo
     FROM evidence WHERE run_id = $1 AND issue_id = $2
     ORDER BY cited_in_memo DESC, severity DESC NULLS LAST, review_id LIMIT 20`, [runId, id]);
  const claims = await query(
    `SELECT claim_id, metric, value FROM claims WHERE run_id = $1 AND issue_id = $2
     ORDER BY length(claim_id), claim_id`, [runId, id]);

  return {
    run_id: runId,
    ...issue,
    ...stats,
    severity_breakdown: severity,
    monthly: monthly.map((m) => ({
      ...m,
      share_of_completed: m.completed ? m.n / m.completed : null,
      share_of_complaints: m.complaints ? m.n / m.complaints : null,
    })),
    evidence,
    claims,
  };
});
