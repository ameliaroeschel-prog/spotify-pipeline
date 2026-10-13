// Full-scale dry run: generate a SYNTHETIC export with 660,622 records
// (same shape as the real corpus), load it into in-memory Postgres with the
// real loader, and report load time and on-disk table sizes.
//
//   npm run size-estimate
//
// Writes the big synthetic export to the OS temp folder and deletes it after.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import crypto from 'node:crypto';
import { PGlite } from '@electric-sql/pglite';
import { loadExport, SCHEMA_SQL } from '../loader/load.js';

const N = 660622, ISSUES = 40, PER_ISSUE_EVIDENCE = 20;
const TOPICS = ['access', 'usability', 'playback', 'downloads', 'catalog', 'billing', 'support', 'other'];
const months = Array.from({ length: 18 }, (_, i) => new Date(Date.UTC(2022, 4 + i, 1)).toISOString().slice(0, 7));
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dash-size-'));
const rnd = (n) => Math.floor(Math.random() * n);

console.log(`Generating ${N.toLocaleString()} synthetic records in ${dir} ...`);
const gz = zlib.createGzip();
const out = fs.createWriteStream(path.join(dir, 'records.jsonl.gz'));
gz.pipe(out);
const members = new Map(Array.from({ length: ISSUES }, (_, i) => [`ISS-${String(i + 1).padStart(2, '0')}`, []]));
const membership = [];
const sampleText = 'Synthetic review text used only to size the evidence table, roughly as long as a typical app review.';
for (let i = 0; i < N; i++) {
  const review_id = crypto.randomUUID();
  const base = { review_id, source_sha256: crypto.randomBytes(32).toString('hex'),
    review_month: months[rnd(18)], review_rating: String(1 + rnd(5)) };
  let rec;
  if (i < 13) rec = { ...base, status: 'quarantined', reason: 'empty_review_text', issue_id: null };
  else {
    const complaint = Math.random() < 0.6;
    const issue_id = complaint ? `ISS-${String(1 + rnd(ISSUES)).padStart(2, '0')}` : null;
    rec = { ...base, status: 'completed', topic: TOPICS[rnd(8)],
      intent: complaint ? (Math.random() < 0.15 ? 'cancellation' : 'complaint') : 'praise',
      sentiment: Math.round((Math.random() * 2 - 1) * 100) / 100, severity: 1 + rnd(5), entities: [],
      evidence_quote: 'quote', needs_review: Math.random() < 0.03, label_config: 'size-test', issue_id,
      ...(Math.random() < 0.27 ? { cache_source_id: 'x' } : {}) };
    if (issue_id) { members.get(issue_id).push(rec); membership.push(`${issue_id},${review_id}`); }
  }
  if (!gz.write(JSON.stringify(rec) + '\n')) await new Promise((r) => gz.once('drain', r));
}
gz.end();
await new Promise((r) => out.on('finish', r));

const stats = [...members].map(([issue_id, m]) => {
  const sum = m.reduce((a, r) => a + r.severity, 0);
  const scaled = (BigInt(sum) * 2000000n + BigInt(m.length)) / (2n * BigInt(m.length));
  const s = scaled.toString();
  return { issue_id, count: m.length, sum, mean: `${s.slice(0, -6)}.${s.slice(-6)}` };
}).sort((a, b) => b.sum - a.sum || (a.issue_id < b.issue_id ? -1 : 1));
fs.writeFileSync(path.join(dir, 'membership.csv'), 'issue_id,review_id\n' + membership.join('\n') + '\n');
fs.writeFileSync(path.join(dir, 'ranking.csv'), 'rank,issue_id,complaint_count,severity_sum,mean_severity,priority_score\n' +
  stats.map((s, i) => `${i + 1},${s.issue_id},${s.count},${s.sum},${s.mean},${s.sum}`).join('\n') + '\n');
const evidence = [...members].flatMap(([issue_id, m]) => m.slice(0, PER_ISSUE_EVIDENCE).map((r) => ({
  review_id: r.review_id, review_text: sampleText, evidence_quote: 'Synthetic review text', issue_id,
  topic: r.topic, intent: r.intent, severity: r.severity, review_rating: r.review_rating, review_month: r.review_month })));
fs.writeFileSync(path.join(dir, 'issues.json'), JSON.stringify([...members].map(([issue_id, m]) => ({
  issue_id, name: `Synthetic issue ${issue_id}`, description: 'synthetic', topic: 'playback',
  example_review_ids: m.slice(0, PER_ISSUE_EVIDENCE).map((r) => r.review_id) }))));
fs.writeFileSync(path.join(dir, 'claims.csv'), 'claim_id,issue_id,metric,value\n' +
  `C1,${stats[0].issue_id},priority_score,${stats[0].sum}\n`);
fs.writeFileSync(path.join(dir, 'memo.json'), JSON.stringify({ recommendation: 'Synthetic [C1]', priority_issue_id: stats[0].issue_id,
  alternatives: [], limitations: [], cited_claim_ids: ['C1'], cited_review_ids: [evidence[0].review_id], model: 'none', generated_at: '' }));
fs.writeFileSync(path.join(dir, 'evidence.jsonl'), evidence.map((e) => JSON.stringify(e)).join('\n') + '\n');
fs.writeFileSync(path.join(dir, 'run_summary.json'), JSON.stringify({ run_id: 'size-test', synthetic: true, source_rows: N }));

const pg = await PGlite.create();
await pg.exec(SCHEMA_SQL);
const t0 = Date.now();
await loadExport({ query: (t, p) => pg.query(t, p) }, dir, { log: () => {} });
const loadSec = (Date.now() - t0) / 1000;
await pg.exec('VACUUM ANALYZE');

const { rows } = await pg.query(`
  SELECT c.relname AS table, pg_total_relation_size(c.oid) AS total, pg_relation_size(c.oid) AS heap
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY total DESC`);
const mb = (b) => (Number(b) / 1048576).toFixed(1);
console.log(`\nLoaded ${N.toLocaleString()} records in ${loadSec.toFixed(1)} s (local in-memory Postgres; Neon adds network time)\n`);
console.log('table'.padEnd(20), 'total MB'.padStart(9), 'heap MB'.padStart(9));
for (const r of rows) console.log(r.table.padEnd(20), mb(r.total).padStart(9), mb(r.heap).padStart(9));
const [{ db }] = (await pg.query('SELECT pg_database_size(current_database()) AS db')).rows;
const sum = rows.reduce((a, r) => a + Number(r.total), 0);
console.log(`\nAll dashboard tables: ${mb(sum)} MB (whole database incl. system catalogs: ${mb(db)} MB) of Neon's 512 MB free tier`);
await pg.close();
fs.rmSync(dir, { recursive: true, force: true });
