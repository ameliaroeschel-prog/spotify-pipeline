// Dashboard front end: plain JavaScript, no build step.
// Every number on the page comes from the /api/* routes, which read Postgres.

const $ = (sel) => document.querySelector(sel);
const fmtInt = (n) => (n == null ? '–' : Number(n).toLocaleString('en-US'));
const fmtPct = (x, d = 1) => (x == null ? '–' : `${(x * 100).toFixed(d)}%`);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
}

let MEMO = null;      // memo payload (claims + cited reviews), used by chips
let ISSUE_NAMES = {}; // issue_id -> name

// ---------------------------------------------------------------- KPIs
function renderKpis(s) {
  const c = s.counts, p = s.pipeline, g = p.golden;
  const reasons = Object.entries(c.quarantine_reasons || {}).map(([k, v]) => `${fmtInt(v)} ${k.replaceAll('_', ' ')}`).join(', ');
  const cost = p.cost_usd == null ? '–' : `$${Number(p.cost_usd).toFixed(2)}`;
  const mins = p.wall_seconds == null ? '' : p.wall_seconds < 120 ? ` · ${Math.round(p.wall_seconds)} s wall time`
    : p.wall_seconds < 7200 ? ` · ${Math.round(p.wall_seconds / 60)} min wall time` : ` · ${(p.wall_seconds / 3600).toFixed(1)} h wall time`;
  const kpis = [
    ['Reviews ingested', fmtInt(c.source_rows), 'every source review ID accounted for'],
    ['Classified', fmtInt(c.completed), `${fmtPct(c.completed / c.source_rows, 3)} of ingested (${fmtInt(c.source_rows - c.completed)} not classified)`],
    ['Quarantined', fmtInt(c.quarantined), reasons || 'none'],
    ['Complaint share', fmtPct(c.complaint_share), `${fmtInt(c.complaints)} of ${fmtInt(c.completed)} classified (${fmtPct(c.cancellation_share)} threaten to cancel)`],
    ['Model cost', cost, `full run${mins}`],
    ['Golden-set agreement', g ? fmtPct(g.topic_agreement, 0) : '–',
      g ? `topic; intent ${fmtPct(g.intent_agreement, 0)}, severity exact ${fmtPct(g.severity_exact, 0)} (n=${g.n})` : 'not available'],
  ];
  $('#kpis').innerHTML = kpis.map(([l, v, n]) =>
    `<div class="kpi"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="note">${esc(n)}</div></div>`).join('');

  $('#run-line').textContent = `Run ${s.run_id} · loaded ${new Date(s.loaded_at).toLocaleDateString('en-US', { dateStyle: 'medium' })} · ${fmtInt(c.source_rows)} public app-store reviews`;
  $('#synthetic-banner').hidden = !s.synthetic;
  const v = p.verifier;
  $('#pipeline-meta').textContent =
    `Labelling model: ${p.model || '–'} (config ${p.label_config || '–'}). ` +
    `${fmtInt(p.distinct_texts)} distinct texts; ${fmtInt(p.cache_reuse_rows)} duplicate-text rows reused a saved label. ` +
    (v ? `Verifier sample of ${fmtInt(v.sample_size)} agreed on topic ${fmtPct(v.topic_agreement, 0)}, intent ${fmtPct(v.intent_agreement, 0)}. ` : '') +
    `${fmtInt(c.needs_review)} labels are flagged as needing human review.`;
}

// ---------------------------------------------------------------- Memo
// Tiny, safe markdown: escape everything first, then add bold, lists, headings.
function miniMarkdown(md) {
  const blocks = esc(md).split(/\n{2,}/);
  return blocks.map((b) => {
    const lines = b.split('\n');
    if (lines.every((l) => /^\s*[-*] /.test(l))) {
      return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*] /, ''))}</li>`).join('')}</ul>`;
    }
    const h = b.match(/^(#{1,4}) (.*)$/);
    if (h && lines.length === 1) return `<h4>${inline(h[2])}</h4>`;
    return `<p>${lines.map(inline).join('<br>')}</p>`;
  }).join('');
}
function inline(s) {
  return s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
}

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
function linkify(html, citedIds) {
  // [C1], [A2s], [R1p], [T1a] or lists of them -> clickable chips showing the cited value
  html = html.replace(/\[((?:[CARTFB]\d+[a-z]?)(?:\s*,\s*[CARTFB]\d+[a-z]?)*)\]/g, (m, list) =>
    list.split(/\s*,\s*/).map(claimChip).join(' '));
  // review IDs (anything the memo cites, plus any UUID-shaped token)
  const ids = (citedIds || []).map((id) => esc(id).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp(`(${[...ids, UUID].join('|')})`, 'gi');
  return html.replace(re, (id) => `<button type="button" class="rid" data-review="${id}">${id}</button>`);
}
function claimChip(id) {
  const c = MEMO.claims[id];
  if (!c) return `<button type="button" class="chip missing" data-claim="${esc(id)}" title="Claim not found in the database">${esc(id)}</button>`;
  return `<button type="button" class="chip" data-claim="${esc(id)}" title="${esc(`${c.metric} for ${c.issue_name || c.issue_id}`)}">${esc(id)}<span class="v">${esc(c.value)}</span></button>`;
}

function renderMemo(m) {
  MEMO = m;
  $('#memo').innerHTML = linkify(miniMarkdown(m.recommendation), m.cited_review_ids);
  $('#alternatives').innerHTML = (m.alternatives || []).map((a) =>
    `<li><a href="#" class="link" data-issue="${esc(a.issue_id)}">${esc(a.issue_name || a.issue_id)}</a> — ${esc(a.why_not)}</li>`).join('') || '<li>None listed</li>';
  $('#memo-limitations').innerHTML = (m.limitations || []).map((l) => `<li>${esc(l)}</li>`).join('');
  $('#memo-meta').textContent = `Priority issue: ${m.priority_issue_name || m.priority_issue_id}. Written by ${m.model || 'model'}${m.generated_at ? ` on ${m.generated_at.slice(0, 10)}` : ''} from the saved ranking and claims; ` +
    `${(m.cited_claim_ids || []).length} claims and ${(m.cited_review_ids || []).length} reviews cited.`;
}

function showClaim(id) {
  const c = MEMO.claims[id];
  const box = $('#claim-detail');
  box.hidden = false;
  box.innerHTML = c
    ? `<strong>${esc(id)}</strong>: <code>${esc(c.metric)}</code> = <strong>${esc(c.value)}</strong> for ` +
      `<a href="#" class="link" data-issue="${esc(c.issue_id)}">${esc(c.issue_name || c.issue_id)}</a> (${esc(c.issue_id)}). ` +
      `<span class="hint">Source: claims table, ranking run ${esc(MEMO.run_id)}.</span>`
    : `<strong>${esc(id)}</strong> is cited by the memo but is not in the claims table.`;
}

// ---------------------------------------------------------------- Review dialog
async function openReview(id) {
  const dlg = $('#review-dialog');
  $('#review-body').textContent = 'Loading…';
  if (!dlg.open) dlg.showModal();
  try {
    const r = await getJson(`/api/reviews/${encodeURIComponent(id)}`);
    const rows = [
      ['Review ID', `<code>${esc(r.review_id)}</code>`],
      ['Issue', r.issue_id ? `<a href="#" class="link" data-issue="${esc(r.issue_id)}" data-close="1">${esc(r.issue_name || r.issue_id)}</a>` : '—'],
      ['Topic / intent', `${esc(r.topic || '—')} / ${esc(r.intent || '—')}`],
      ['Severity', esc(r.severity ?? '—')],
      ['Star rating', r.review_rating ? `${esc(r.review_rating)} / 5` : '—'],
      ['Month', esc(r.review_month || '—')],
    ];
    let text;
    if (r.has_text) {
      let body = esc(r.review_text);
      if (r.evidence_quote) {
        const q = esc(r.evidence_quote);
        body = body.includes(q) ? body.replace(q, `<mark>${q}</mark>`) : body;
      }
      text = `<blockquote>${body}</blockquote><p class="hint">Highlighted: the evidence quote the model extracted${r.cited_in_memo ? ' · cited in the memo' : ''}.</p>`;
    } else {
      text = `<p class="hint">Full text is stored only for evidence reviews; these are this review's saved labels.</p>`;
    }
    $('#review-body').innerHTML = text + `<dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;
  } catch (e) {
    $('#review-body').textContent = `Could not load review ${id}.`;
  }
}

// ---------------------------------------------------------------- Ranking
function renderRanking(r) {
  const max = Math.max(...r.issues.map((i) => i.priority_score), 1);
  ISSUE_NAMES = Object.fromEntries(r.issues.map((i) => [i.issue_id, i.name]));
  $('#ranking tbody').innerHTML = r.issues.map((i) => `
    <tr tabindex="0" data-issue="${esc(i.issue_id)}" aria-label="Show detail for ${esc(i.name)}">
      <td>${i.rank}</td>
      <td><span class="issue-name">${esc(i.name || i.issue_id)}</span><br><span class="issue-id">${esc(i.issue_id)} · ${esc(i.topic || '')}</span></td>
      <td class="num">${fmtInt(i.complaint_count)}</td>
      <td class="num hide-sm">${Number(i.mean_severity).toFixed(2)}</td>
      <td class="num"><strong>${fmtInt(i.priority_score)}</strong></td>
      <td class="bar-col"><div class="scorebar" style="width:${(100 * i.priority_score / max).toFixed(1)}%"></div></td>
    </tr>`).join('');
}

async function openIssue(id, scroll = true) {
  document.querySelectorAll('#ranking tr.sel').forEach((tr) => tr.classList.remove('sel'));
  document.querySelector(`#ranking tr[data-issue="${CSS.escape(id)}"]`)?.classList.add('sel');
  const card = $('#issue-card');
  card.hidden = false;
  $('#issue-body').innerHTML = '<p class="hint">Loading…</p>';
  if (scroll) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  history.replaceState(null, '', `#issue=${encodeURIComponent(id)}`);
  try {
    const d = await getJson(`/api/issues/${encodeURIComponent(id)}`);
    $('#issue-h').textContent = d.name;
    $('#issue-tag').textContent = `${d.issue_id}${d.rank ? ` · rank ${d.rank}` : ''}`;
    const stats = [
      ['Complaints', fmtInt(d.complaint_count)],
      ['Mean severity', d.mean_severity],
      ['Priority score', fmtInt(d.priority_score)],
      ['Cancellation threats', `${fmtInt(d.cancellations)} (${fmtPct(d.members ? d.cancellations / d.members : null, 0)})`],
      ['Mean star rating', d.mean_rating ?? '–'],
      ['Mean sentiment', d.mean_sentiment ?? '–'],
    ];
    const claims = d.claims.length
      ? `<p class="hint">Memo claims about this issue: ${d.claims.map((c) => `<strong>${esc(c.claim_id)}</strong> ${esc(c.metric)} = ${esc(c.value)}`).join(' · ')}</p>` : '';
    $('#issue-body').innerHTML = `
      <p>${esc(d.description || '')}</p>
      <div class="issue-stats">${stats.map(([l, v]) => `<div><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join('')}</div>
      ${claims}
      <h3>Complaints per month</h3>
      <div id="issue-chart" class="chart"></div>
      <h3 class="mt">Evidence reviews (${d.evidence.length})</h3>
      <ul class="evidence">${d.evidence.map((e) => `
        <li><blockquote>“${esc(e.evidence_quote || e.review_text)}”</blockquote>
          <div class="m"><button type="button" class="rid" data-review="${esc(e.review_id)}">${esc(e.review_id)}</button>
          <span>${esc(e.intent)}</span><span>severity ${esc(e.severity)}</span><span>${esc(e.review_rating)}★</span><span>${esc(e.review_month)}</span>
          ${e.cited_in_memo ? '<span class="cited">cited in memo</span>' : ''}</div></li>`).join('') || '<li class="hint">No evidence reviews stored.</li>'}
      </ul>`;
    lineChart($('#issue-chart'), d.monthly.map((m) => ({
      x: m.month, y: m.n,
      tip: `<b>${esc(m.month)}</b><br>${fmtInt(m.n)} complaints in this issue<br>${fmtPct(m.share_of_complaints)} of ${fmtInt(m.complaints)} complaints that month`,
    })), { yFormat: fmtInt });
  } catch (e) {
    $('#issue-body').innerHTML = `<p class="hint">Could not load issue ${esc(id)}.</p>`;
  }
}

// ---------------------------------------------------------------- Charts
function bars(el, rows, labelFn = (r) => r.label) {
  const total = rows.reduce((a, r) => a + r.n, 0) || 1;
  const max = Math.max(...rows.map((r) => r.n), 1);
  el.innerHTML = `<div class="bars">${rows.map((r) => `
    <div class="brow" data-tip="${esc(`<b>${esc(labelFn(r))}</b><br>${fmtInt(r.n)} reviews (${fmtPct(r.n / total)})<br>${fmtInt(r.complaints)} are complaints`)}">
      <span class="lab">${esc(labelFn(r))}</span>
      <span class="track"><span class="fill" style="display:block;width:${(100 * r.n / max).toFixed(1)}%"></span></span>
      <span class="num">${fmtPct(r.n / total)}</span>
    </div>`).join('')}</div>`;
}

const SEVERITY_NAMES = { 1: '1 · none', 2: '2 · minor', 3: '3 · degraded', 4: '4 · blocked', 5: '5 · serious harm' };

// Simple responsive SVG line chart with a hover crosshair + tooltip.
function lineChart(el, pts, { yFormat = String, yMaxHint } = {}) {
  if (!pts.length) { el.innerHTML = '<p class="hint">No monthly data.</p>'; return; }
  // Draw at the container's real pixel width so text stays readable on phones.
  const W = Math.max(300, Math.round(el.clientWidth || 720)), H = Math.round(Math.min(260, Math.max(180, W * 0.4)));
  const L = 48, R = 12, T = 12, B = 28;
  const yMax = niceMax(Math.max(yMaxHint || 0, ...pts.map((p) => p.y || 0)));
  const x = (i) => L + (pts.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (pts.length - 1));
  const y = (v) => T + (H - T - B) * (1 - v / yMax);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * yMax);
  const every = Math.ceil(pts.length / Math.max(2, Math.floor(W / 110)));
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.y || 0).toFixed(1)}`).join('');
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Line chart by month">
    <g class="axis">${ticks.map((t) => `<line class="gridline" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>
      <text x="${L - 6}" y="${y(t) + 4}" text-anchor="end">${esc(yFormat(t))}</text>`).join('')}
      ${pts.map((p, i) => (i % every === 0 || i === pts.length - 1) ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(p.x)}</text>` : '').join('')}</g>
    <path class="line" d="${path}"/>
    <line class="cross" y1="${T}" y2="${H - B}" x1="-10" x2="-10" visibility="hidden"/>
    <circle class="dot" r="5" cx="-10" cy="-10" visibility="hidden"/>
    <rect class="hit" x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent"/>
  </svg>`;
  const svg = el.querySelector('svg'), cross = svg.querySelector('.cross'), dot = svg.querySelector('.dot');
  const tip = $('#tooltip');
  const move = (ev) => {
    const box = svg.getBoundingClientRect();
    const sx = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(pts.length - 1, Math.round(((sx - L) / (W - L - R)) * (pts.length - 1))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(pts[i].y || 0)); dot.setAttribute('visibility', 'visible');
    showTip(pts[i].tip, ev.clientX, ev.clientY);
  };
  const leave = () => { cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); tip.hidden = true; };
  svg.addEventListener('pointermove', move);
  svg.addEventListener('pointerleave', leave);
}
function niceMax(v) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v)), n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}
function showTip(html, cx, cy) {
  const tip = $('#tooltip');
  tip.innerHTML = html; tip.hidden = false;
  const w = tip.offsetWidth;
  tip.style.left = `${Math.min(cx + 12, window.innerWidth - w - 8)}px`;
  tip.style.top = `${cy + 14}px`;
}

function renderDistributions(s) {
  const d = s.distributions;
  bars($('#topic-bars'), d.topic || []);
  bars($('#severity-bars'), d.severity || [], (r) => SEVERITY_NAMES[r.label] || r.label);
  bars($('#intent-bars'), d.intent || []);
  lineChart($('#monthly-chart'), s.monthly.map((m) => ({
    x: m.month, y: m.complaint_share,
    tip: `<b>${esc(m.month)}</b><br>${fmtPct(m.complaint_share)} complaint share<br>${fmtInt(m.complaints)} of ${fmtInt(m.completed)} classified reviews<br>${fmtInt(m.cancellations)} cancellation threats`,
  })), { yFormat: (v) => fmtPct(v, 0) });
  $('#monthly-table').innerHTML = `<table><thead><tr><th>Month</th><th class="num">Classified</th><th class="num">Complaints</th><th class="num">Cancellation threats</th><th class="num">Share</th></tr></thead><tbody>${
    s.monthly.map((m) => `<tr><td>${esc(m.month)}</td><td class="num">${fmtInt(m.completed)}</td><td class="num">${fmtInt(m.complaints)}</td><td class="num">${fmtInt(m.cancellations)}</td><td class="num">${fmtPct(m.complaint_share)}</td></tr>`).join('')
  }</tbody></table>`;
}

// ---------------------------------------------------------------- Events
document.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-claim],[data-review],[data-issue]');
  if (!t) return;
  if (t.dataset.claim) { ev.preventDefault(); showClaim(t.dataset.claim); }
  else if (t.dataset.review) { ev.preventDefault(); openReview(t.dataset.review); }
  else if (t.dataset.issue) {
    ev.preventDefault();
    if (t.dataset.close) $('#review-dialog').close();
    openIssue(t.dataset.issue);
  }
});
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && ev.target.matches('#ranking tr[data-issue]')) openIssue(ev.target.dataset.issue);
});
document.addEventListener('pointermove', (ev) => {
  const row = ev.target.closest?.('[data-tip]');
  if (row) showTip(row.dataset.tip, ev.clientX, ev.clientY);
  else if (!ev.target.closest?.('.chart svg')) $('#tooltip').hidden = true;
});

// ---------------------------------------------------------------- Boot
(async function main() {
  try {
    const [summary, ranking, memo] = await Promise.all([
      getJson('/api/summary'), getJson('/api/ranking'), getJson('/api/memo'),
    ]);
    renderKpis(summary);
    renderRanking(ranking);
    renderMemo(memo);
    renderDistributions(summary);
    const m = location.hash.match(/issue=([^&]+)/);
    if (m) openIssue(decodeURIComponent(m[1]), false);
  } catch (e) {
    const box = $('#error');
    box.hidden = false;
    box.textContent = `Could not load data from the API (${e.message}). Please try again shortly.`;
    $('#run-line').textContent = '';
  }
})();
