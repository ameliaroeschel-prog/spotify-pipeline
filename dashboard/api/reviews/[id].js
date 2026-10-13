// GET /api/reviews/:id — one review. Full text is stored only for evidence
// reviews (cited in the memo or used as issue examples); for any other review
// we return its labels so every ID is still traceable.
import { query, route, pathId } from '../../lib/db.js';

export default route(async (req, runId) => {
  const id = pathId(req);
  const [ev] = await query(
    `SELECT e.review_id, e.review_text, e.evidence_quote, e.issue_id, i.name AS issue_name,
            e.topic, e.intent, e.severity, e.review_rating, e.review_month, e.cited_in_memo
     FROM evidence e LEFT JOIN issues i ON i.run_id = e.run_id AND i.issue_id = e.issue_id
     WHERE e.run_id = $1 AND e.review_id = $2`, [runId, id]);
  if (ev) return { run_id: runId, has_text: true, ...ev };

  const [rec] = await query(
    `SELECT r.review_id, r.status, r.topic, r.intent, r.severity, round(r.sentiment::numeric, 3)::float8 AS sentiment,
            r.issue_id, i.name AS issue_name, r.review_rating, r.review_month, r.needs_review,
            r.quarantine_reason
     FROM records r LEFT JOIN issues i ON i.run_id = r.run_id AND i.issue_id = r.issue_id
     WHERE r.run_id = $1 AND r.review_id = $2`, [runId, id]);
  return rec ? { run_id: runId, has_text: false, ...rec } : null;
});
