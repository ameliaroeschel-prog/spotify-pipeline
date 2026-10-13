// End-to-end local test, no internet or Neon needed.
//
// PGlite is real Postgres compiled to WebAssembly, running in this process.
// We run schema.sql, load the synthetic fixture with the real loader, then
// call the real API handlers as the read-only role and compare every number
// with the fixture files.
//
//   npm test

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { PGlite } from '@electric-sql/pglite';
import { loadExport, SCHEMA_SQL } from '../loader/load.js';
import { setQueryRunner } from '../lib/db.js';
import summary from '../api/summary.js';
import ranking from '../api/ranking.js';
import memo from '../api/memo.js';
import issue from '../api/issues/[id].js';
import review from '../api/reviews/[id].js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIX = path.join(HERE, '..', 'fixtures', 'synthetic-export');
const quiet = () => {};

// --- read the fixture the "plain" way, independently of the loader ---
const csv = (name) => {
  const [head, ...rows] = fs.readFileSync(path.join(FIX, name), 'utf8').trim().split('\n');
  const h = head.split(',');
  return rows.map((r) => Object.fromEntries(r.split(',').map((v, i) => [h[i], v])));
};
const records = zlib.gunzipSync(fs.readFileSync(path.join(FIX, 'records.jsonl.gz')))
  .toString().trim().split('\n').map((l) => JSON.parse(l));
const rankingCsv = csv('ranking.csv');
const claimsCsv = csv('claims.csv');
const membershipCsv = csv('membership.csv');
const issuesJson = JSON.parse(fs.readFileSync(path.join(FIX, 'issues.json'), 'utf8'));
const memoJson = JSON.parse(fs.readFileSync(path.join(FIX, 'memo.json'), 'utf8'));
const evidenceIds = fs.readFileSync(path.join(FIX, 'evidence.jsonl'), 'utf8').trim().split('\n')
  .map((l) => JSON.parse(l).review_id);

// --- call a handler like Vercel would ---
async function call(handler, url, query = {}, method = 'GET') {
  const res = {
    statusCode: 200, headers: {}, body: '',
    setHeader(k, v) { this.headers[k.toLowerCase()] = v; },
    end(b) { this.body = b; },
  };
  await handler({ method, url, query }, res);
  return { status: res.statusCode, headers: res.headers, json: JSON.parse(res.body) };
}

let passed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log(`  ok   ${name}`); }
  catch (err) { console.log(`  FAIL ${name}\n       ${err.message.split('\n').join('\n       ')}`); process.exitCode = 1; }
}

// ===================================================================
const pg = await PGlite.create();
await pg.exec(SCHEMA_SQL);
await pg.exec(SCHEMA_SQL); // re-running the schema must be harmless
const owner = { query: (t, p) => pg.query(t, p) };

console.log('Loader');
await test('loads fixture and self-verifies ranking', async () => {
  const id = await loadExport(owner, FIX, { log: quiet });
  assert.equal(id, 'synthetic-fixture');
});

const counts = async () => (await pg.query(
  `SELECT (SELECT count(*) FROM records)::int r, (SELECT count(*) FROM evidence)::int e,
          (SELECT count(*) FROM ranking)::int k, (SELECT count(*) FROM agg_monthly)::int m,
          (SELECT count(*) FROM runs)::int runs`)).rows[0];
const before = await counts();

await test('reloading the same run is idempotent (no duplicates)', async () => {
  await loadExport(owner, FIX, { log: quiet });
  assert.deepEqual(await counts(), before);
  assert.equal(before.r, records.length);
});

await test('bad export (tampered ranking.csv) is rejected and old data kept', async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'dash-'));
  for (const f of fs.readdirSync(FIX)) fs.copyFileSync(path.join(FIX, f), path.join(tmp, f));
  const lines = fs.readFileSync(path.join(tmp, 'ranking.csv'), 'utf8').split('\n');
  lines[1] = lines[1].replace(/,(\d+)$/, (m, n) => `,${Number(n) + 1}`).replace(/^1,(\S+?),(\d+),/, (m, a, b) => `1,${a},${Number(b) + 1},`);
  fs.writeFileSync(path.join(tmp, 'ranking.csv'), lines.join('\n'));
  await assert.rejects(loadExport(owner, tmp, { log: quiet }), /does not match/);
  assert.deepEqual(await counts(), before);
  fs.rmSync(tmp, { recursive: true });
});

await test('a second run loaded with --no-current does not change what is shown; --drop-other-runs cleans up', async () => {
  await loadExport(owner, FIX, { runId: 'older-run', makeCurrent: false, log: quiet });
  let runs = (await pg.query('SELECT run_id, is_current FROM runs ORDER BY run_id')).rows;
  assert.deepEqual(runs.map((r) => [r.run_id, r.is_current]), [['older-run', false], ['synthetic-fixture', true]]);
  await loadExport(owner, FIX, { dropOtherRuns: true, log: quiet });
  runs = (await pg.query('SELECT run_id FROM runs')).rows;
  assert.deepEqual(runs.map((r) => r.run_id), ['synthetic-fixture']);
  assert.deepEqual(await counts(), before);
});

// From here on, the API runs as the READ-ONLY role, like production.
await pg.exec('SET ROLE dashboard_reader');
setQueryRunner(async (t, p) => (await pg.query(t, p)).rows);

console.log('Read-only role');
await test('dashboard_reader cannot write', async () => {
  await assert.rejects(pg.query("DELETE FROM records"), /permission denied/);
  await assert.rejects(pg.query("INSERT INTO runs (run_id, summary) VALUES ('x', '{}')"), /permission denied/);
  await assert.rejects(pg.query('CREATE TABLE hack (x int)'), /permission denied/);
});

console.log('API');
const completed = records.filter((r) => r.status === 'completed');
const complaints = completed.filter((r) => ['complaint', 'cancellation'].includes(r.intent));

await test('/api/summary counts match the fixture', async () => {
  const { status, json, headers } = await call(summary, '/api/summary');
  assert.equal(status, 200);
  assert.match(headers['cache-control'], /s-maxage/);
  assert.equal(json.synthetic, true);
  assert.equal(json.counts.source_rows, records.length);
  assert.equal(json.counts.completed, completed.length);
  assert.equal(json.counts.quarantined, records.length - completed.length);
  assert.equal(json.counts.complaints, complaints.length);
  assert.equal(json.counts.complaint_share, complaints.length / completed.length);
  assert.deepEqual(json.counts.quarantine_reasons, { empty_review_text: 3 });
  assert.equal(json.counts.cache_reuse_rows, records.filter((r) => r.cache_source_id).length);
  // pipeline-reported numbers agree with the DB recount
  assert.equal(json.pipeline.completed, json.counts.completed);
  assert.equal(json.pipeline.golden.topic_agreement, 0.86);
  // distributions add up to the completed count
  for (const dim of ['topic', 'intent', 'severity', 'rating']) {
    assert.equal(json.distributions[dim].reduce((a, d) => a + d.n, 0), completed.length, dim);
  }
  const playback = completed.filter((r) => r.topic === 'playback').length;
  assert.equal(json.distributions.topic.find((d) => d.label === 'playback').n, playback);
  // monthly denominators add up, and each month's share is complaints/completed
  assert.equal(json.monthly.reduce((a, m) => a + m.completed, 0), completed.length);
  assert.equal(json.monthly.reduce((a, m) => a + m.complaints, 0), complaints.length);
  const m0 = json.monthly[0];
  const exp = completed.filter((r) => r.review_month === m0.month);
  assert.equal(m0.completed, exp.length);
  assert.equal(m0.complaint_share, exp.filter((r) => ['complaint', 'cancellation'].includes(r.intent)).length / exp.length);
});

await test('/api/ranking matches ranking.csv exactly', async () => {
  const { json } = await call(ranking, '/api/ranking');
  assert.equal(json.issues.length, rankingCsv.length);
  json.issues.forEach((row, i) => {
    const c = rankingCsv[i];
    assert.deepEqual(
      [String(row.rank), row.issue_id, String(row.complaint_count), String(row.severity_sum), row.mean_severity, String(row.priority_score)],
      [c.rank, c.issue_id, c.complaint_count, c.severity_sum, c.mean_severity, c.priority_score]);
    assert.equal(row.name, issuesJson.find((x) => x.issue_id === c.issue_id).name);
  });
  assert.equal(json.total_complaints, membershipCsv.length);
});

await test('/api/memo resolves every [Cn] citation to claims.csv', async () => {
  const { json } = await call(memo, '/api/memo');
  assert.equal(json.recommendation, memoJson.recommendation);
  const cited = [...json.recommendation.matchAll(/\[(C\d+)\]/g)].map((m) => m[1]);
  assert.ok(cited.length >= 3);
  for (const id of cited) {
    const c = claimsCsv.find((x) => x.claim_id === id);
    assert.equal(json.claims[id].value, c.value, id);
    assert.equal(json.claims[id].issue_id, c.issue_id, id);
    assert.ok(json.claims[id].issue_name, id);
  }
  for (const rid of memoJson.cited_review_ids) assert.ok(json.cited_reviews[rid]?.evidence_quote, rid);
  assert.equal(json.priority_issue_name, issuesJson.find((x) => x.issue_id === memoJson.priority_issue_id).name);
  assert.equal(json.alternatives.length, memoJson.alternatives.length);
});

await test('/api/issues/:id stats, trend and evidence for every issue', async () => {
  for (const iss of issuesJson) {
    const { status, json } = await call(issue, `/api/issues/${iss.issue_id}`, { id: iss.issue_id });
    assert.equal(status, 200);
    const r = rankingCsv.find((x) => x.issue_id === iss.issue_id);
    const members = completed.filter((x) => x.issue_id === iss.issue_id);
    assert.equal(json.complaint_count, Number(r.complaint_count));
    assert.equal(json.mean_severity, r.mean_severity);
    assert.equal(json.members, members.length);
    assert.equal(json.cancellations, members.filter((x) => x.intent === 'cancellation').length);
    assert.equal(json.monthly.reduce((a, m) => a + m.n, 0), members.length);
    assert.equal(json.severity_breakdown.reduce((a, s) => a + s.n, 0), members.length);
    for (const ex of iss.example_review_ids) assert.ok(json.evidence.some((e) => e.review_id === ex), ex);
    assert.ok(json.evidence.length <= 20);
  }
});

await test('/api/issues/:id also works when id only comes from the URL; 404 for unknown', async () => {
  const ok = await call(issue, '/api/issues/ISS-02');
  assert.equal(ok.json.issue_id, 'ISS-02');
  const missing = await call(issue, '/api/issues/NOPE', { id: 'NOPE' });
  assert.equal(missing.status, 404);
});

await test('/api/reviews/:id returns text for evidence, labels otherwise', async () => {
  const ev = await call(review, '', { id: evidenceIds[0] });
  assert.equal(ev.json.has_text, true);
  assert.ok(ev.json.review_text.length > 0);
  const other = completed.find((r) => !evidenceIds.includes(r.review_id));
  const lab = await call(review, '', { id: other.review_id });
  assert.equal(lab.json.has_text, false);
  assert.equal(lab.json.topic, other.topic);
  assert.equal(lab.json.sentiment, other.sentiment);
  assert.equal((await call(review, '', { id: 'missing' })).status, 404);
});

await test('API refuses non-GET methods', async () => {
  assert.equal((await call(summary, '/api/summary', {}, 'POST')).status, 405);
});

await pg.close();
console.log(`\n${passed} tests passed${process.exitCode ? ', SOME FAILED' : ''}`);
