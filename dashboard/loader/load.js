// Loads one pipeline export folder into Postgres (Neon).
//
// Usage (run on your laptop, NOT on Vercel):
//   export LOADER_DATABASE_URL='postgresql://neondb_owner:...'   # OWNER role
//   node loader/load.js ../runs/<run>/export            # load + make it current
//   node loader/load.js ../runs/<run>/export --schema   # also (re)apply schema.sql
//   node loader/load.js <dir> --run-id my-run --no-current
//   node loader/load.js <dir> --drop-other-runs        # keep ONLY this run (saves space)
//
// What it does, in ONE transaction (so a failure leaves the old data intact):
//   1. deletes any existing rows for this run_id (so re-loading is clean)
//   2. bulk-inserts records (5,000 rows per INSERT), issues, ranking,
//      claims, memo and evidence
//   3. computes the aggregate tables inside Postgres
//   4. re-computes the ranking from the records and checks it matches
//      ranking.csv exactly; aborts if not

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SCHEMA_SQL = fs.readFileSync(path.join(HERE, '..', 'schema.sql'), 'utf8');
const BATCH = 5000;
const COMPLAINT_INTENTS = ['complaint', 'cancellation'];

// ---------- small file helpers ----------

function parseCsv(text) {
  // Minimal RFC-4180 parser (handles quoted fields with commas/quotes/newlines).
  const rows = [];
  let row = [], field = '', inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (ch === '"') inQuotes = false;
      else field += ch;
    } else if (ch === '"') inQuotes = true;
    else if (ch === ',') { row.push(field); field = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      if (row.length > 1 || row[0] !== '') rows.push(row);
      row = [];
    } else field += ch;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  const [header, ...body] = rows;
  return body.map((r) => Object.fromEntries(header.map((h, i) => [h.trim(), r[i]])));
}

const readCsv = (dir, name) => parseCsv(fs.readFileSync(path.join(dir, name), 'utf8'));
const readJson = (dir, name) => JSON.parse(fs.readFileSync(path.join(dir, name), 'utf8'));

async function* readJsonl(file) {
  let stream = fs.createReadStream(file);
  if (file.endsWith('.gz')) stream = stream.pipe(zlib.createGunzip());
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
  for await (const line of rl) if (line.trim()) yield JSON.parse(line);
}

function findFile(dir, ...names) {
  for (const n of names) if (fs.existsSync(path.join(dir, n))) return path.join(dir, n);
  throw new Error(`None of ${names.join(', ')} found in ${dir}`);
}

const toInt = (v) => (v === null || v === undefined || v === '' ? null : parseInt(v, 10));
const toMonth = (v) => (v ? String(v).slice(0, 7) : null);

// ---------- the loader ----------

/**
 * @param db   anything with query(text, params) -> {rows}; a dedicated
 *             connection (Neon Pool client, or PGlite in tests).
 * @param dir  export folder.
 */
export async function loadExport(db, dir, { runId, makeCurrent = true, dropOtherRuns = false, log = console.log } = {}) {
  const t0 = Date.now();
  const summary = readJson(dir, 'run_summary.json');
  runId = runId || summary.run_id;
  if (!runId) throw new Error('No run_id: pass --run-id or include it in run_summary.json');

  // membership.csv is the source of truth for which issue a review belongs to.
  const membership = new Map();
  for (const m of readCsv(dir, 'membership.csv')) {
    const prev = membership.get(m.review_id);
    if (prev && prev !== m.issue_id) {
      throw new Error(`Review ${m.review_id} is in two issues (${prev}, ${m.issue_id}); ` +
        'this schema stores one issue per review (allow_multi_issue:false).');
    }
    membership.set(m.review_id, m.issue_id);
  }
  const issues = readJson(dir, 'issues.json');
  const ranking = readCsv(dir, 'ranking.csv');
  const claims = readCsv(dir, 'claims.csv');
  const memo = readJson(dir, 'memo.json');
  const citedReviews = new Set(memo.cited_review_ids || []);
  const evidence = [];
  for await (const e of readJsonl(findFile(dir, 'evidence.jsonl', 'evidence.jsonl.gz'))) evidence.push(e);

  log(`Loading run "${runId}" from ${dir}`);
  await db.query('BEGIN');
  try {
    // Remove this run's old rows (or, with --drop-other-runs, every run's rows;
    // a full run is ~150 MB, so keep at most two on Neon's 512 MB free tier).
    for (const t of ['records', 'issues', 'ranking', 'claims', 'memo', 'evidence',
      'agg_monthly', 'agg_distribution', 'agg_issue_monthly', 'runs']) {
      if (dropOtherRuns) await db.query(`DELETE FROM ${t}`);
      else await db.query(`DELETE FROM ${t} WHERE run_id = $1`, [runId]);
    }
    if (makeCurrent) await db.query('UPDATE runs SET is_current = false WHERE is_current');
    await db.query('INSERT INTO runs (run_id, is_current, summary) VALUES ($1, $2, $3)',
      [runId, makeCurrent, JSON.stringify(summary)]);

    // --- records, in batches, using unnest() over parallel arrays ---
    const cols = { id: [], status: [], topic: [], intent: [], sev: [], sent: [], issue: [],
      month: [], rating: [], needs: [], cache: [], reason: [] };
    let total = 0, issueMismatch = 0;
    const flush = async () => {
      if (!cols.id.length) return;
      await db.query(
        `INSERT INTO records (run_id, review_id, status, topic, intent, severity, sentiment,
           issue_id, review_month, review_rating, needs_review, from_cache, quarantine_reason)
         SELECT $1, * FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::smallint[],
           $7::real[], $8::text[], $9::text[], $10::smallint[], $11::boolean[], $12::boolean[], $13::text[])`,
        [runId, cols.id, cols.status, cols.topic, cols.intent, cols.sev, cols.sent, cols.issue,
          cols.month, cols.rating, cols.needs, cols.cache, cols.reason]
      );
      total += cols.id.length;
      for (const k of Object.keys(cols)) cols[k] = [];
      if (total % 100000 < BATCH) log(`  records: ${total.toLocaleString()}`);
    };
    for await (const r of readJsonl(findFile(dir, 'records.jsonl.gz', 'records.jsonl'))) {
      const done = r.status === 'completed';
      const issueId = membership.get(r.review_id) ?? null;
      if ((r.issue_id ?? null) !== issueId && r.issue_id !== undefined) issueMismatch++;
      cols.id.push(r.review_id);
      cols.status.push(r.status);
      cols.topic.push(done ? r.topic ?? null : null);
      cols.intent.push(done ? r.intent ?? null : null);
      cols.sev.push(done ? toInt(r.severity) : null);
      cols.sent.push(done && r.sentiment != null ? Number(r.sentiment) : null);
      cols.issue.push(issueId);
      cols.month.push(toMonth(r.review_month));
      cols.rating.push(toInt(r.review_rating));
      cols.needs.push(Boolean(r.needs_review));
      cols.cache.push(Boolean(r.cache_source_id));
      cols.reason.push(done ? null : r.reason ?? 'unknown');
      if (cols.id.length >= BATCH) await flush();
    }
    await flush();
    log(`  records: ${total.toLocaleString()} total`);
    if (issueMismatch) log(`  WARNING: ${issueMismatch} records had an issue_id different from membership.csv (membership.csv used)`);

    // --- headline counts, re-counted from the records we just inserted ---
    await db.query(
      `UPDATE runs SET counts = (
         SELECT jsonb_build_object(
           'source_rows',   count(*),
           'completed',     count(*) FILTER (WHERE status = 'completed'),
           'quarantined',   count(*) FILTER (WHERE status <> 'completed'),
           'complaints',    count(*) FILTER (WHERE status = 'completed' AND intent = ANY($2::text[])),
           'cancellations', count(*) FILTER (WHERE status = 'completed' AND intent = 'cancellation'),
           'in_an_issue',   count(*) FILTER (WHERE issue_id IS NOT NULL),
           'needs_review',  count(*) FILTER (WHERE needs_review),
           'cache_reuse_rows', count(*) FILTER (WHERE from_cache),
           'mean_sentiment', round(avg(sentiment)::numeric, 4),
           'quarantine_reasons', COALESCE((
             SELECT jsonb_object_agg(reason, n) FROM (
               SELECT quarantine_reason AS reason, count(*) AS n FROM records
               WHERE run_id = $1 AND status <> 'completed' GROUP BY quarantine_reason) q), '{}'::jsonb))
         FROM records WHERE run_id = $1)
       WHERE run_id = $1`, [runId, COMPLAINT_INTENTS]);

    // --- small tables ---
    for (const i of issues) {
      await db.query(
        `INSERT INTO issues (run_id, issue_id, name, description, topic, example_review_ids)
         VALUES ($1, $2, $3, $4, $5, $6::text[])`,
        [runId, i.issue_id, i.name, i.description ?? null, i.topic ?? null, i.example_review_ids || []]
      );
    }
    for (const r of ranking) {
      await db.query(
        `INSERT INTO ranking (run_id, rank, issue_id, complaint_count, severity_sum, mean_severity, priority_score)
         VALUES ($1, $2, $3, $4, $5, $6::numeric, $7)`,
        [runId, toInt(r.rank), r.issue_id, toInt(r.complaint_count), toInt(r.severity_sum),
          r.mean_severity, toInt(r.priority_score)]
      );
    }
    for (const c of claims) {
      await db.query('INSERT INTO claims (run_id, claim_id, issue_id, metric, value) VALUES ($1, $2, $3, $4, $5)',
        [runId, c.claim_id, c.issue_id || null, c.metric, c.value]);
    }
    await db.query(
      `INSERT INTO memo (run_id, recommendation, priority_issue_id, alternatives, limitations,
         cited_claim_ids, cited_review_ids, model, generated_at)
       VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::text[], $7::text[], $8, $9)`,
      [runId, memo.recommendation, memo.priority_issue_id ?? null, JSON.stringify(memo.alternatives || []),
        JSON.stringify(memo.limitations || []), memo.cited_claim_ids || [], memo.cited_review_ids || [],
        memo.model ?? null, memo.generated_at ?? null]
    );
    for (const e of evidence) {
      await db.query(
        `INSERT INTO evidence (run_id, review_id, review_text, evidence_quote, issue_id, topic, intent,
           severity, review_rating, review_month, cited_in_memo)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
         ON CONFLICT (run_id, review_id) DO NOTHING`,
        [runId, e.review_id, e.review_text ?? '', e.evidence_quote ?? null, e.issue_id ?? null,
          e.topic ?? null, e.intent ?? null, toInt(e.severity), toInt(e.review_rating),
          toMonth(e.review_month), citedReviews.has(e.review_id)]
      );
    }
    log(`  issues ${issues.length}, ranking ${ranking.length}, claims ${claims.length}, evidence ${evidence.length}`);

    // --- aggregates, computed by Postgres ---
    await db.query(
      `INSERT INTO agg_monthly (run_id, review_month, total, completed, complaints, cancellations)
       SELECT run_id, review_month, count(*),
              count(*) FILTER (WHERE status = 'completed'),
              count(*) FILTER (WHERE status = 'completed' AND intent = ANY($2::text[])),
              count(*) FILTER (WHERE status = 'completed' AND intent = 'cancellation')
       FROM records WHERE run_id = $1 AND review_month IS NOT NULL
       GROUP BY run_id, review_month`, [runId, COMPLAINT_INTENTS]);
    for (const [dim, expr] of [['topic', 'topic'], ['intent', 'intent'],
      ['severity', 'severity::text'], ['rating', 'review_rating::text']]) {
      await db.query(
        `INSERT INTO agg_distribution (run_id, dimension, label, n, complaints)
         SELECT run_id, $2, COALESCE(${expr}, 'unknown'), count(*),
                count(*) FILTER (WHERE intent = ANY($3::text[]))
         FROM records WHERE run_id = $1 AND status = 'completed'
         GROUP BY run_id, COALESCE(${expr}, 'unknown')`, [runId, dim, COMPLAINT_INTENTS]);
    }
    await db.query(
      `INSERT INTO agg_issue_monthly (run_id, issue_id, review_month, n, severity_sum, cancellations)
       SELECT run_id, issue_id, review_month, count(*), COALESCE(sum(severity), 0),
              count(*) FILTER (WHERE intent = 'cancellation')
       FROM records WHERE run_id = $1 AND issue_id IS NOT NULL AND review_month IS NOT NULL
       GROUP BY run_id, issue_id, review_month`, [runId]);

    // --- self-check: recompute ranking from records, compare with ranking.csv ---
    const recomputed = await db.query(
      `SELECT issue_id, count(*)::int AS complaint_count, sum(severity)::int AS severity_sum,
              round(sum(severity)::numeric / count(*), 6)::text AS mean_severity
       FROM records WHERE run_id = $1 AND issue_id IS NOT NULL
       GROUP BY issue_id`, [runId]);
    const byId = new Map(recomputed.rows.map((r) => [r.issue_id, r]));
    const problems = [];
    for (const r of ranking) {
      const d = byId.get(r.issue_id);
      if (!d) { problems.push(`${r.issue_id}: no member records`); continue; }
      if (d.complaint_count !== toInt(r.complaint_count) || d.severity_sum !== toInt(r.severity_sum) ||
          d.mean_severity !== r.mean_severity) {
        problems.push(`${r.issue_id}: csv ${r.complaint_count}/${r.severity_sum}/${r.mean_severity} ` +
          `vs db ${d.complaint_count}/${d.severity_sum}/${d.mean_severity}`);
      }
    }
    if (problems.length) throw new Error('ranking.csv does not match the records:\n  ' + problems.join('\n  '));

    // Warnings (do not abort): memo cites things the DB cannot show.
    const missingEvidence = [...citedReviews].filter((id) => !evidence.some((e) => e.review_id === id));
    if (missingEvidence.length) log(`  WARNING: memo cites ${missingEvidence.length} review(s) missing from evidence.jsonl`);
    const claimIds = new Set(claims.map((c) => c.claim_id));
    const missingClaims = (memo.cited_claim_ids || []).filter((id) => !claimIds.has(id));
    if (missingClaims.length) log(`  WARNING: memo cites unknown claims: ${missingClaims.join(', ')}`);

    await db.query('COMMIT');
  } catch (err) {
    await db.query('ROLLBACK');
    throw err;
  }
  log(`Done: run "${runId}" loaded in ${((Date.now() - t0) / 1000).toFixed(1)}s and verified against ranking.csv`);
  return runId;
}

// ---------- command line ----------

async function main() {
  const args = process.argv.slice(2);
  const dir = args.find((a) => !a.startsWith('--') && args[args.indexOf(a) - 1] !== '--run-id');
  const runIdIdx = args.indexOf('--run-id');
  const runId = runIdIdx >= 0 ? args[runIdIdx + 1] : undefined;
  const url = process.env.LOADER_DATABASE_URL;
  if (!dir || !url) {
    console.error('Usage: LOADER_DATABASE_URL=... node loader/load.js <export-dir> [--schema] [--run-id ID] [--no-current] [--drop-other-runs]');
    process.exit(1);
  }
  const { Pool, neonConfig } = await import('@neondatabase/serverless');
  if (globalThis.WebSocket) neonConfig.webSocketConstructor = globalThis.WebSocket; // Node 22+
  const pool = new Pool({ connectionString: url });
  const client = await pool.connect();
  try {
    if (args.includes('--schema')) {
      await client.query(SCHEMA_SQL);
      console.log('schema.sql applied');
    }
    await loadExport(client, path.resolve(dir), { runId, makeCurrent: !args.includes('--no-current'), dropOtherRuns: args.includes('--drop-other-runs') });
  } finally {
    client.release();
    await pool.end();
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => { console.error(err.message || err); process.exit(1); });
}
