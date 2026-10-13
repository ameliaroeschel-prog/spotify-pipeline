// Builds a SMALL, SYNTHETIC export folder that follows the pipeline's export
// contract, so the dashboard can be developed and tested without the real run.
//
// Everything here is invented: review texts, IDs, labels, costs and the memo.
// The output is deterministic (seeded random numbers), so tests are repeatable.
//
//   node fixtures/make_fixture.js      -> writes fixtures/synthetic-export/

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, 'synthetic-export');
const N = 300;

// Small seeded random generator (mulberry32) so every run is identical.
let seed = 20261013;
function rand() {
  seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const pick = (arr) => arr[Math.floor(rand() * arr.length)];
const hex = (n) => Array.from({ length: n }, () => Math.floor(rand() * 16).toString(16)).join('');
const uuid = () => `${hex(8)}-${hex(4)}-4${hex(3)}-a${hex(3)}-${hex(12)}`;

const MONTHS = [];
for (let i = 0; i < 18; i++) {
  const d = new Date(Date.UTC(2022, 4 + i, 1));
  MONTHS.push(d.toISOString().slice(0, 7));
}

// Six invented issues. `quotes` are short phrases that become evidence quotes.
const ISSUES = [
  { issue_id: 'ISS-01', name: 'App crashes or freezes on launch', topic: 'playback', sev: [4, 4, 3, 5], weight: 9,
    description: 'SYNTHETIC. App closes, freezes on the logo screen or will not open after an update.',
    quotes: ['the app crashes every time I open it', 'stuck on the logo screen', 'freezes right after the update'] },
  { issue_id: 'ISS-02', name: 'Downloads disappear from library', topic: 'downloads', sev: [3, 4, 3], weight: 6,
    description: 'SYNTHETIC. Offline downloads vanish or need to be re-downloaded.',
    quotes: ['all my downloads disappeared', 'had to download everything again', 'offline songs are gone'] },
  { issue_id: 'ISS-03', name: 'Too many ads / ad interruptions', topic: 'usability', sev: [2, 2, 3], weight: 8,
    description: 'SYNTHETIC. Free-tier listeners complain about ad frequency and length.',
    quotes: ['an ad after every song', 'too many ads', 'ads are louder than the music'] },
  { issue_id: 'ISS-04', name: 'Unexpected charges or price increase', topic: 'billing', sev: [3, 5, 2, 3], weight: 4,
    description: 'SYNTHETIC. Charged after cancelling, double charges, or unhappy with the new price.',
    quotes: ['charged me twice this month', 'still billed after I cancelled', 'the price went up again'] },
  { issue_id: 'ISS-05', name: 'Cannot log in / account locked', topic: 'access', sev: [4, 4, 3], weight: 3,
    description: 'SYNTHETIC. Login loops, password resets that fail, or locked accounts.',
    quotes: ['keeps logging me out', 'cannot log in at all', 'password reset never arrives'] },
  { issue_id: 'ISS-06', name: 'Shuffle and queue feel broken', topic: 'usability', sev: [2, 3, 2], weight: 5,
    description: 'SYNTHETIC. Shuffle repeats the same songs and the queue reorders itself.',
    quotes: ['shuffle plays the same ten songs', 'the queue keeps changing', 'shuffle is not random'] },
];
const issueWeights = ISSUES.flatMap((iss) => Array(iss.weight).fill(iss));

const PRAISE = ['Best music app I have used, love the playlists.', 'Great recommendations, discover weekly is amazing.', 'Works perfectly, no complaints.'];
const REQUESTS = ['Please add a sleep timer to the car view.', 'Would love a way to hide podcasts from home.', 'Please bring back the old shuffle button.'];
const UNCLEAR = ['ok', 'Spotify.', 'hello from the bus'];

const records = [];
const texts = new Map(); // review_id -> text (only kept for evidence)
let lastByText = new Map(); // text -> first completed record (for cache_source_id)
const LABEL_CONFIG = 'synthetic-fixture+prompt-v0+schema-v0';

for (let i = 0; i < N; i++) {
  const review_id = uuid();
  const review_month = pick(MONTHS);
  const source_sha256 = hex(64);

  // 3 quarantined rows (empty review text), like the real corpus.
  if (i % 100 === 7) {
    records.push({ review_id, source_sha256, status: 'quarantined', reason: 'empty_review_text',
      review_month, review_rating: String(1 + Math.floor(rand() * 5)), issue_id: null });
    continue;
  }

  const r = rand();
  let rec;
  if (r < 0.62) {
    // complaint or cancellation linked to an issue
    const iss = pick(issueWeights);
    const quote = pick(iss.quotes);
    const cancel = rand() < 0.18;
    const text = cancel
      ? `Honestly ${quote}. I am cancelling my subscription and uninstalling.`
      : `Really frustrating, ${quote}. Please fix this.`;
    rec = { topic: iss.topic, intent: cancel ? 'cancellation' : 'complaint', severity: pick(iss.sev),
      sentiment: -(0.3 + Math.round(rand() * 60) / 100), evidence_quote: quote, issue_id: iss.issue_id,
      review_rating: String(1 + Math.floor(rand() * 2)), text };
  } else if (r < 0.82) {
    const text = pick(PRAISE);
    rec = { topic: 'other', intent: 'praise', severity: 1, sentiment: 0.8, evidence_quote: text, issue_id: null,
      review_rating: '5', text };
  } else if (r < 0.94) {
    const text = pick(REQUESTS);
    rec = { topic: 'usability', intent: 'request', severity: 1, sentiment: 0.1, evidence_quote: text, issue_id: null,
      review_rating: String(3 + Math.floor(rand() * 3)), text };
  } else {
    const text = pick(UNCLEAR);
    rec = { topic: 'other', intent: 'unclear', severity: 1, sentiment: 0, evidence_quote: text, issue_id: null,
      review_rating: String(1 + Math.floor(rand() * 5)), text };
  }

  const out = {
    review_id, source_sha256, status: 'completed', topic: rec.topic, intent: rec.intent,
    sentiment: rec.sentiment, severity: rec.severity, entities: [], evidence_quote: rec.evidence_quote,
    needs_review: rand() < 0.05, label_config: LABEL_CONFIG,
    review_month, review_rating: rec.review_rating, issue_id: rec.issue_id,
  };
  // Exact duplicate text with the same labels -> reuse via cache_source_id.
  const prior = lastByText.get(rec.text);
  if (prior && prior.topic === out.topic && prior.intent === out.intent && prior.severity === out.severity
      && prior.sentiment === out.sentiment && prior.issue_id === out.issue_id) {
    out.cache_source_id = prior.review_id;
    out.evidence_quote = prior.evidence_quote;
    out.needs_review = prior.needs_review;
  } else if (!prior) {
    lastByText.set(rec.text, out);
  }
  texts.set(review_id, rec.text);
  records.push(out);
}

// ---- membership + baseline ranking (exactly as the grading contract says) ----
const members = records.filter((r) => r.status === 'completed' && r.issue_id &&
  (r.intent === 'complaint' || r.intent === 'cancellation'));
const membership = members.map((r) => [r.issue_id, r.review_id]);

function meanHalfUp(sum, count) {
  // sum/count to 6 decimals, decimal half-up, using integers only.
  const scaled = (BigInt(sum) * 2000000n + BigInt(count)) / (2n * BigInt(count));
  const s = scaled.toString().padStart(7, '0');
  return `${s.slice(0, -6)}.${s.slice(-6)}`;
}
const stats = ISSUES.map((iss) => {
  const m = members.filter((r) => r.issue_id === iss.issue_id);
  const severity_sum = m.reduce((a, r) => a + r.severity, 0);
  return { issue_id: iss.issue_id, complaint_count: m.length, severity_sum,
    mean_severity: meanHalfUp(severity_sum, m.length), priority_score: severity_sum };
});
stats.sort((a, b) => b.priority_score - a.priority_score || (a.issue_id < b.issue_id ? -1 : 1));
stats.forEach((s, i) => { s.rank = i + 1; });

// ---- issues.json with up to 5 example review IDs each ----
const issuesJson = ISSUES.map((iss) => ({
  issue_id: iss.issue_id, name: iss.name, description: iss.description, topic: iss.topic,
  example_review_ids: members.filter((r) => r.issue_id === iss.issue_id && !r.cache_source_id)
    .sort((a, b) => b.severity - a.severity).slice(0, 5).map((r) => r.review_id),
}));

// ---- claims for the top three issues ----
const claims = [];
let c = 1;
for (const s of stats.slice(0, 3)) {
  for (const metric of ['complaint_count', 'mean_severity', 'priority_score']) {
    claims.push({ claim_id: `C${c++}`, issue_id: s.issue_id, metric, value: String(s[metric]) });
  }
}
const claim = (issue, metric) => claims.find((x) => x.issue_id === issue && x.metric === metric).claim_id;

const [top, second, third] = stats;
const nameOf = (id) => ISSUES.find((i) => i.issue_id === id).name;
const ex = (id, k) => issuesJson.find((i) => i.issue_id === id).example_review_ids[k];
const cited_review_ids = [ex(top.issue_id, 0), ex(top.issue_id, 1), ex(second.issue_id, 0), ex(third.issue_id, 0)];

const recommendation = [
  `**SYNTHETIC FIXTURE — not real findings.** Prioritise **${nameOf(top.issue_id)}** (${top.issue_id}) next quarter.`,
  '',
  `It has the highest baseline priority score (${top.priority_score} [${claim(top.issue_id, 'priority_score')}]) from ` +
  `${top.complaint_count} complaints [${claim(top.issue_id, 'complaint_count')}] with a mean severity of ` +
  `${top.mean_severity} [${claim(top.issue_id, 'mean_severity')}]. Representative reviews: ${cited_review_ids[0]}, ${cited_review_ids[1]}.`,
  '',
  `- **${nameOf(second.issue_id)}** is close behind (score ${second.priority_score} [${claim(second.issue_id, 'priority_score')}]), e.g. ${cited_review_ids[2]}.`,
  `- **${nameOf(third.issue_id)}** has ${third.complaint_count} complaints [${claim(third.issue_id, 'complaint_count')}], e.g. ${cited_review_ids[3]}.`,
].join('\n');

const memo = {
  recommendation,
  priority_issue_id: top.issue_id,
  alternatives: [
    { issue_id: second.issue_id, why_not: 'SYNTHETIC. Lower total severity; partly a workaround exists.' },
    { issue_id: third.issue_id, why_not: 'SYNTHETIC. Fewer complaints; mostly annoyance-level severity.' },
  ],
  limitations: [
    'SYNTHETIC fixture data generated for software testing.',
    'App-store reviews are self-selected and skew negative.',
    'Expressed intent to cancel is not observed churn.',
  ],
  cited_claim_ids: claims.map((x) => x.claim_id).filter((id) => recommendation.includes(`[${id}]`)),
  cited_review_ids,
  model: 'synthetic-fixture (no model call)',
  generated_at: '2026-10-13T00:00:00Z',
};

// ---- evidence.jsonl: examples + cited reviews, with full text ----
const evidenceIds = new Set([...issuesJson.flatMap((i) => i.example_review_ids), ...cited_review_ids]);
const evidence = records.filter((r) => evidenceIds.has(r.review_id)).map((r) => ({
  review_id: r.review_id, review_text: texts.get(r.review_id), evidence_quote: r.evidence_quote,
  issue_id: r.issue_id, topic: r.topic, intent: r.intent, severity: r.severity,
  review_rating: r.review_rating, review_month: r.review_month,
}));

const completed = records.filter((r) => r.status === 'completed');
const runSummary = {
  run_id: 'synthetic-fixture',
  synthetic: true,
  source_rows: records.length,
  completed: completed.length,
  quarantined: records.length - completed.length,
  quarantine_reasons: { empty_review_text: records.length - completed.length },
  distinct_texts: new Set(completed.map((r) => texts.get(r.review_id))).size,
  cache_reuse_rows: completed.filter((r) => r.cache_source_id).length,
  golden: { topic_agreement: 0.86, intent_agreement: 0.9, severity_exact: 0.72, severity_mae: 0.32, n: 50 },
  verifier: { sample_size: 30, topic_agreement: 0.9, intent_agreement: 0.93, severity_exact: 0.77 },
  cost_usd: 0.0,
  wall_seconds: 1.5,
  model: 'synthetic-fixture (no model call)',
  label_config: LABEL_CONFIG,
};

// ---- write files ----
fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });
const w = (name, data) => fs.writeFileSync(path.join(OUT, name), data);
const csvLine = (cells) => cells.join(',') + '\n';

w('records.jsonl.gz', zlib.gzipSync(records.map((r) => JSON.stringify(r)).join('\n') + '\n'));
w('membership.csv', csvLine(['issue_id', 'review_id']) + membership.map(csvLine).join(''));
w('ranking.csv', csvLine(['rank', 'issue_id', 'complaint_count', 'severity_sum', 'mean_severity', 'priority_score']) +
  stats.map((s) => csvLine([s.rank, s.issue_id, s.complaint_count, s.severity_sum, s.mean_severity, s.priority_score])).join(''));
w('issues.json', JSON.stringify(issuesJson, null, 2));
w('claims.csv', csvLine(['claim_id', 'issue_id', 'metric', 'value']) +
  claims.map((x) => csvLine([x.claim_id, x.issue_id, x.metric, x.value])).join(''));
w('memo.json', JSON.stringify(memo, null, 2));
w('evidence.jsonl', evidence.map((e) => JSON.stringify(e)).join('\n') + '\n');
w('run_summary.json', JSON.stringify(runSummary, null, 2));
w('SYNTHETIC_README.txt', 'This folder is SYNTHETIC test data generated by make_fixture.js.\n' +
  'It follows the pipeline export contract but contains no real reviews or results.\n');

console.log(`Wrote ${OUT}: ${records.length} records, ${membership.length} memberships, ${evidence.length} evidence rows`);
