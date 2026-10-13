// GET /api/memo — the AI-written recommendation plus everything needed to
// trace it: each cited claim's value (from the claims table) and each cited
// review's quote (from the evidence table).
import { query, route } from '../lib/db.js';

export default route(async (req, runId) => {
  const [memo] = await query(
    `SELECT recommendation, priority_issue_id, alternatives, limitations,
            cited_claim_ids, cited_review_ids, model, generated_at
     FROM memo WHERE run_id = $1`, [runId]);
  if (!memo) return null;

  const claims = await query(
    `SELECT c.claim_id, c.issue_id, i.name AS issue_name, c.metric, c.value
     FROM claims c LEFT JOIN issues i ON i.run_id = c.run_id AND i.issue_id = c.issue_id
     WHERE c.run_id = $1
     ORDER BY length(c.claim_id), c.claim_id`, [runId]);
  const reviews = await query(
    `SELECT review_id, evidence_quote, issue_id, severity, intent, review_rating
     FROM evidence WHERE run_id = $1 AND review_id = ANY($2::text[])`,
    [runId, memo.cited_review_ids]);
  const names = await query('SELECT issue_id, name FROM issues WHERE run_id = $1', [runId]);
  const issueName = Object.fromEntries(names.map((n) => [n.issue_id, n.name]));

  return {
    run_id: runId,
    ...memo,
    priority_issue_name: issueName[memo.priority_issue_id] || null,
    alternatives: (memo.alternatives || []).map((a) => ({ ...a, issue_name: issueName[a.issue_id] || null })),
    claims: Object.fromEntries(claims.map((c) => [c.claim_id, c])),
    cited_reviews: Object.fromEntries(reviews.map((r) => [r.review_id, r])),
  };
});
