// Local preview server (not used on Vercel).
//
//   npm run dev                      -> in-memory Postgres (PGlite) loaded with
//                                       the synthetic fixture, at http://localhost:3000
//   EXPORT_DIR=../runs/x/export npm run dev   -> same, but with a real export
//   DATABASE_URL=postgres://... npm run dev   -> use a real (Neon) database instead
//
// It serves public/ and routes /api/* to the SAME handler files Vercel runs.

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT || 3000);

if (!process.env.DATABASE_URL) {
  const { PGlite } = await import('@electric-sql/pglite');
  const { loadExport, SCHEMA_SQL } = await import('./loader/load.js');
  const { setQueryRunner } = await import('./lib/db.js');
  const pg = await PGlite.create();
  await pg.exec(SCHEMA_SQL);
  await loadExport({ query: (t, p) => pg.query(t, p) },
    path.resolve(process.env.EXPORT_DIR || path.join(HERE, 'fixtures', 'synthetic-export')));
  await pg.exec('SET ROLE dashboard_reader'); // serve as the read-only role, like production
  setQueryRunner(async (t, p) => (await pg.query(t, p)).rows);
}

const routes = [
  [/^\/api\/summary\/?$/, './api/summary.js'],
  [/^\/api\/ranking\/?$/, './api/ranking.js'],
  [/^\/api\/memo\/?$/, './api/memo.js'],
  [/^\/api\/issues\/([^/]+)\/?$/, './api/issues/[id].js'],
  [/^\/api\/reviews\/([^/]+)\/?$/, './api/reviews/[id].js'],
];
const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };

http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  for (const [re, file] of routes) {
    const m = url.pathname.match(re);
    if (m) {
      req.query = { ...Object.fromEntries(url.searchParams), ...(m[1] ? { id: decodeURIComponent(m[1]) } : {}) };
      const handler = (await import(file)).default;
      return handler(req, res);
    }
  }
  if (url.pathname.startsWith('/api/')) { res.statusCode = 404; return res.end('{"error":"Not found"}'); }
  const rel = url.pathname === '/' ? 'index.html' : url.pathname.slice(1);
  const file = path.join(HERE, 'public', path.normalize(rel));
  if (!file.startsWith(path.join(HERE, 'public')) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
    res.statusCode = 404; return res.end('Not found');
  }
  res.setHeader('Content-Type', TYPES[path.extname(file)] || 'application/octet-stream');
  fs.createReadStream(file).pipe(res);
}).listen(PORT, () => console.log(`Dashboard running at http://localhost:${PORT}`));
