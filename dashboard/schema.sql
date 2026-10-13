-- Spotify review analysis dashboard: database schema (Postgres / Neon)
--
-- Run once as the database OWNER (e.g. in the Neon SQL editor, or
-- `node loader/load.js --schema-only`). Safe to re-run: every statement
-- is "IF NOT EXISTS" / idempotent.
--
-- Design notes
--   * Every table is keyed by run_id so a re-run can be reloaded cleanly
--     (the loader deletes that run_id's rows and inserts fresh ones in one
--     transaction).
--   * `records` holds ONE compact row per source review (labels only, no
--     text) so all 660,622 reviews fit in Neon's 0.5 GB free tier.
--   * Full review text + evidence quotes live ONLY in `evidence`, for the
--     reviews cited by the memo or sampled as examples for each issue.
--   * The agg_* tables are computed inside Postgres by the loader
--     (INSERT ... SELECT ... GROUP BY) so the dashboard's API reads small,
--     pre-summarised tables instead of scanning 660K rows on every visit.

CREATE TABLE IF NOT EXISTS runs (
  run_id       text PRIMARY KEY,
  loaded_at    timestamptz NOT NULL DEFAULT now(),
  is_current   boolean NOT NULL DEFAULT false,   -- the run the dashboard shows
  summary      jsonb NOT NULL,                   -- run_summary.json, verbatim
  counts       jsonb                             -- totals re-counted from `records` by the loader
);
-- At most one run can be "current".
CREATE UNIQUE INDEX IF NOT EXISTS runs_one_current ON runs (is_current) WHERE is_current;

-- One row per source review (completed or quarantined). ~660K rows.
CREATE TABLE IF NOT EXISTS records (
  run_id          text     NOT NULL,
  review_id       text     NOT NULL,
  status          text     NOT NULL,             -- completed | quarantined
  topic           text,                          -- null when quarantined
  intent          text,
  severity        smallint,                      -- 1..5
  sentiment       real,                          -- -1..1
  issue_id        text,                          -- from membership.csv (null if none)
  review_month    char(7),                       -- 'YYYY-MM'
  review_rating   smallint,                      -- 1..5 stars
  needs_review    boolean  NOT NULL DEFAULT false,
  from_cache      boolean  NOT NULL DEFAULT false, -- true when cache_source_id was set
  quarantine_reason text,                        -- only for quarantined rows
  PRIMARY KEY (run_id, review_id)
);
-- Issue-detail queries look up members of one issue.
CREATE INDEX IF NOT EXISTS records_issue_idx ON records (run_id, issue_id) WHERE issue_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS issues (
  run_id             text NOT NULL,
  issue_id           text NOT NULL,
  name               text NOT NULL,
  description        text,
  topic              text,
  example_review_ids text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (run_id, issue_id)
);

-- ranking.csv, stored exactly as exported (mean_severity keeps its 6 decimals).
CREATE TABLE IF NOT EXISTS ranking (
  run_id          text    NOT NULL,
  rank            integer NOT NULL,
  issue_id        text    NOT NULL,
  complaint_count integer NOT NULL,
  severity_sum    integer NOT NULL,
  mean_severity   numeric(12,6) NOT NULL,
  priority_score  integer NOT NULL,
  PRIMARY KEY (run_id, issue_id)
);

-- claims.csv: the numbers the memo cites as [C1], [C2], ...
CREATE TABLE IF NOT EXISTS claims (
  run_id    text NOT NULL,
  claim_id  text NOT NULL,
  issue_id  text,
  metric    text NOT NULL,
  value     text NOT NULL,          -- kept as text so "3.512346" is shown exactly
  PRIMARY KEY (run_id, claim_id)
);

-- memo.json: the AI-generated recommendation (one per run).
CREATE TABLE IF NOT EXISTS memo (
  run_id            text PRIMARY KEY,
  recommendation    text NOT NULL,
  priority_issue_id text,
  alternatives      jsonb NOT NULL DEFAULT '[]',
  limitations       jsonb NOT NULL DEFAULT '[]',
  cited_claim_ids   text[] NOT NULL DEFAULT '{}',
  cited_review_ids  text[] NOT NULL DEFAULT '{}',
  model             text,
  generated_at      text
);

-- evidence.jsonl: full text ONLY for cited / example reviews (a few hundred rows).
CREATE TABLE IF NOT EXISTS evidence (
  run_id         text NOT NULL,
  review_id      text NOT NULL,
  review_text    text NOT NULL,
  evidence_quote text,
  issue_id       text,
  topic          text,
  intent         text,
  severity       smallint,
  review_rating  smallint,
  review_month   char(7),
  cited_in_memo  boolean NOT NULL DEFAULT false,
  PRIMARY KEY (run_id, review_id)
);
CREATE INDEX IF NOT EXISTS evidence_issue_idx ON evidence (run_id, issue_id);

-- Aggregates computed by the loader inside Postgres -----------------------

-- Monthly totals: denominators for "complaint share" per month.
CREATE TABLE IF NOT EXISTS agg_monthly (
  run_id        text    NOT NULL,
  review_month  char(7) NOT NULL,
  total         integer NOT NULL,   -- all source rows that month
  completed     integer NOT NULL,   -- classified rows (the denominator)
  complaints    integer NOT NULL,   -- intent complaint OR cancellation
  cancellations integer NOT NULL,   -- intent cancellation only
  PRIMARY KEY (run_id, review_month)
);

-- Label distributions (dimension = 'topic' | 'intent' | 'severity' | 'rating').
CREATE TABLE IF NOT EXISTS agg_distribution (
  run_id     text    NOT NULL,
  dimension  text    NOT NULL,
  label      text    NOT NULL,
  n          integer NOT NULL,      -- completed records
  complaints integer NOT NULL,      -- of which complaint/cancellation
  PRIMARY KEY (run_id, dimension, label)
);

-- Per-issue monthly trend.
CREATE TABLE IF NOT EXISTS agg_issue_monthly (
  run_id        text    NOT NULL,
  issue_id      text    NOT NULL,
  review_month  char(7) NOT NULL,
  n             integer NOT NULL,
  severity_sum  integer NOT NULL,
  cancellations integer NOT NULL,
  PRIMARY KEY (run_id, issue_id, review_month)
);

-- Read-only role for the public dashboard ---------------------------------
-- The website connects as `dashboard_reader`, which can only SELECT.
-- This block creates the role WITHOUT a password (it cannot log in yet);
-- README step 3 gives it a password. Re-running is harmless.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_reader') THEN
    CREATE ROLE dashboard_reader NOLOGIN;
  END IF;
END
$$;

GRANT USAGE ON SCHEMA public TO dashboard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_reader;
-- Tables created later by the owner are readable too.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO dashboard_reader;
-- Explicitly no INSERT/UPDATE/DELETE/TRUNCATE; also stop it creating objects.
REVOKE CREATE ON SCHEMA public FROM dashboard_reader;
ALTER ROLE dashboard_reader SET default_transaction_read_only = on;
ALTER ROLE dashboard_reader SET statement_timeout = '10s';
