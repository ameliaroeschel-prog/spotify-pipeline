// One tiny helper that every API route uses to talk to Postgres.
//
// In production (Vercel) it uses Neon's HTTP driver with DATABASE_URL, which
// should be the connection string of the READ-ONLY `dashboard_reader` role.
// Local tests swap in an in-memory Postgres (PGlite) via setQueryRunner();
// the SQL and the route code stay exactly the same.

import { neon } from '@neondatabase/serverless';

let runner = null;

/** Tests / local dev only: provide a function (text, params) => rows[]. */
export function setQueryRunner(fn) {
  runner = fn;
}

/** Run one parameterised SQL query and return an array of row objects. */
export async function query(text, params = []) {
  if (!runner) {
    const url = process.env.DATABASE_URL;
    if (!url) throw new Error('DATABASE_URL is not set');
    const sql = neon(url);
    runner = (t, p) => sql.query(t, p);
  }
  return runner(text, params);
}

/** The run the dashboard shows: the one flagged current, else the newest. */
export async function currentRunId() {
  const rows = await query(
    'SELECT run_id FROM runs ORDER BY is_current DESC, loaded_at DESC LIMIT 1'
  );
  return rows.length ? rows[0].run_id : null;
}

/** Send JSON. Works on Vercel's Node runtime and on plain node:http. */
export function sendJson(res, status, body, cacheSeconds = 300) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  // Data only changes when the loader runs, so let Vercel's CDN cache it.
  res.setHeader(
    'Cache-Control',
    status === 200 ? `public, s-maxage=${cacheSeconds}, stale-while-revalidate=86400` : 'no-store'
  );
  res.end(JSON.stringify(body));
}

/** Wrap a route: only GET allowed, errors become a clean JSON 500. */
export function route(fn) {
  return async (req, res) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.setHeader('Allow', 'GET');
      return sendJson(res, 405, { error: 'Read-only API: use GET' });
    }
    try {
      const runId = await currentRunId();
      if (!runId) return sendJson(res, 503, { error: 'No data loaded yet' });
      const result = await fn(req, runId);
      if (result === null) return sendJson(res, 404, { error: 'Not found' });
      return sendJson(res, 200, result);
    } catch (err) {
      console.error(err);
      return sendJson(res, 500, { error: 'Database error' });
    }
  };
}

/** Read the [id] part of /api/issues/[id] (Vercel puts it in req.query). */
export function pathId(req) {
  if (req.query && typeof req.query.id === 'string') return req.query.id;
  const url = new URL(req.url, 'http://localhost');
  return decodeURIComponent(url.pathname.split('/').filter(Boolean).pop() || '');
}
