"""Stage 2 — Enrich. The orchestrator (code) owns the queue, state, validation, budget and logs;
the model (enricher role) only reads review text and returns labels.

Queue: one canonical review per distinct pending text, in text-hash order (so any partial run is
an unbiased sample). Up to 50 reviews per request. Results are validated and committed in one
transaction per batch; exact-duplicate reviews then reuse the result with cache_source_id.

    python3 -m pipeline.enrich --db runs/pilot/state.db --provider openai --workers 1 --budget 1.00
"""

import argparse
import json
import signal
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from pipeline import config as C
from pipeline.ingest import connect
from pipeline.llm import PermanentError, TransientError, call, with_retries

# USD per token, standard tier (https://developers.openai.com/api/docs/models/gpt-6-luna, checked 2026-10-13).
PRICES = {"gpt-6-luna": {"input": 0.10e-6, "cached_input": 0.01e-6, "output": 0.50e-6}}

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    text_sha TEXT NOT NULL, label_config TEXT NOT NULL, origin_id TEXT NOT NULL,
    topic TEXT, intent TEXT, sentiment REAL, severity INTEGER, entities TEXT, evidence_quote TEXT,
    needs_review INTEGER, quote_source TEXT, request_id TEXT, created REAL,
    PRIMARY KEY (text_sha, label_config));
CREATE TABLE IF NOT EXISTS calls (
    attempt_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT, role TEXT, phase TEXT, model TEXT,
    label_config TEXT, review_ids TEXT, outcome TEXT, error TEXT, attempt INTEGER,
    input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
    cost_usd REAL, started REAL, ended REAL);
CREATE TABLE IF NOT EXISTS failures (
    review_id TEXT, label_config TEXT, attempts INTEGER, reason TEXT, PRIMARY KEY (review_id, label_config));
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, phase TEXT, started REAL, ended REAL, settings TEXT, summary TEXT);
"""


def cost_of(model, usage):
    p = PRICES.get(model)
    if not p or usage.get("input_tokens") is None:
        return None
    cached = usage.get("cached_input_tokens") or 0
    # Mutually exclusive categories; reasoning tokens are already inside output_tokens.
    return (usage["input_tokens"] - cached) * p["input"] + cached * p["cached_input"] + usage["output_tokens"] * p["output"]


class Ledger:
    """One spend ledger shared by all workers. Reserve worst-case cost before dispatch; stop
    admitting work once spent + reserved + next reservation would exceed the cap."""

    def __init__(self, cap, already_spent=0.0):
        self.cap, self.spent, self.reserved = cap, already_spent, 0.0
        self.lock = threading.Lock()

    def reserve(self, amount):
        with self.lock:
            if self.spent + self.reserved + amount > self.cap:
                return False
            self.reserved += amount
            return True

    def settle(self, reserved, actual):
        with self.lock:
            self.reserved -= reserved
            self.spent += actual if actual is not None else reserved   # unknown usage: assume worst case


def worst_case_cost(model, items):
    p = PRICES.get(model)
    if not p:
        return 0.0
    in_tokens = 2500 + sum(len(t) for _, t in items) // 2       # generous: ~4 chars/token, x2 margin
    return in_tokens * p["input"] + len(items) * C.OUT_TOKENS_PER_REVIEW * p["output"]


def find_quote(text, quote):
    """Exact substring check in code. Falls back to the full text for short reviews."""
    if quote and quote.strip() and quote in text:
        return quote, "model"
    if len(text) <= C.SHORT_TEXT_CHARS:
        return text, "full_text"
    stripped = (quote or "").strip().strip('"').strip()
    if stripped and stripped in text:
        return stripped, "model_trimmed"
    if stripped:
        i = text.lower().find(stripped.lower())
        if i >= 0:
            return text[i:i + len(stripped)], "model_case_matched"
    if len(text) <= C.FULL_TEXT_FALLBACK_CHARS:
        return text, "full_text_fallback"
    return None, None


def parse_and_validate(result, batch):
    """Returns ({review_id: label_dict}, {review_id: problem}). Never trusts model IDs blindly."""
    by_index = {i: (rid, text) for i, (rid, text) in enumerate(batch, 1)}
    good, bad = {}, {}
    if result["status"] != "completed":
        return good, {rid: f"incomplete_response:{result['incomplete_reason']}" for rid, _ in batch}
    try:
        items = json.loads(result["text"])["r"]
    except (ValueError, KeyError, TypeError):
        return good, {rid: "unparseable_output" for rid, _ in batch}
    seen = set()
    for it in items:
        i = it.get("i")
        if i not in by_index or i in seen:
            continue                                   # foreign or duplicated index: ignore
        seen.add(i)
        rid, text = by_index[i]
        quote, source = find_quote(text, it.get("q", ""))
        if (it.get("t") not in C.TOPICS or it.get("n") not in C.INTENTS or it.get("v") not in (1, 2, 3, 4, 5)
                or it.get("s") not in (-2, -1, 0, 1, 2)):
            bad[rid] = "invalid_label_values"
        elif quote is None:
            bad[rid] = "quote_not_in_source"
        else:
            entities = [e.strip().lower() for e in it.get("e", []) if isinstance(e, str) and e.strip()][:5]
            good[rid] = {"topic": it["t"], "intent": it["n"], "severity": it["v"], "sentiment": it["s"] / 2,
                         "entities": entities, "evidence_quote": quote, "needs_review": bool(it.get("f")),
                         "quote_source": source}
    for i, (rid, _) in by_index.items():
        if i not in seen:
            bad[rid] = "missing_from_output"
    return good, bad


class Enricher:
    def __init__(self, db_path, provider, workers, budget, label_config, model, stop_after_batches=None):
        self.db_path, self.provider, self.workers = db_path, provider, workers
        self.model, self.label_config = model, label_config
        self.stop_after_batches = stop_after_batches
        self.con = connect(db_path)
        self.con.executescript(STATE_SCHEMA)
        prior = self.con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]
        self.ledger = Ledger(budget, prior)
        done_before = self.con.execute("SELECT COUNT(*) FROM results WHERE label_config=?", (label_config,)).fetchone()[0]
        self.phase = "resume" if done_before else "initial"
        self.run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.stop = threading.Event()
        self.log_lock = threading.Lock()
        self.pending_logs = []

    # ---- queue ---------------------------------------------------------------
    def apply_cache(self):
        """Exact-text reuse: completed result under the same label_config -> mark all its reviews."""
        cur = self.con.execute("""
            UPDATE reviews SET status='completed' WHERE status='pending' AND text_sha IN
              (SELECT text_sha FROM results WHERE label_config=?)""", (self.label_config,))
        self.con.commit()
        return cur.rowcount

    def pending_batches(self, limit_texts=None):
        rows = self.con.execute("""
            SELECT r.review_id, r.review_text FROM reviews r
            WHERE r.status='pending' AND r.review_id = r.canonical_id
              AND r.review_id NOT IN (SELECT review_id FROM failures WHERE label_config=?)
            ORDER BY r.text_sha""" + (f" LIMIT {int(limit_texts)}" if limit_texts else ""),
                                (self.label_config,)).fetchall()
        return [rows[i:i + C.BATCH_SIZE] for i in range(0, len(rows), C.BATCH_SIZE)]

    # ---- one request (runs in a worker thread; no DB writes here) -------------
    def request(self, batch):
        items = [(i, text) for i, (_, text) in enumerate(batch, 1)]
        attempts = []

        def log_attempt(n, outcome, result, error, started):
            usage = (result or {}).get("usage") or {}
            attempts.append({"attempt_id": uuid.uuid4().hex, "request_id": (result or {}).get("request_id"),
                             "outcome": outcome, "error": error, "attempt": n, "usage": usage,
                             "cost": cost_of(self.model, usage) if result else 0.0,
                             "started": started, "ended": time.time()})

        fn = lambda: call(self.provider, model=self.model, effort=C.EFFORT, instructions=C.ENRICH_INSTRUCTIONS,
                          user=C.enrich_user_message(items), schema=C.ENRICH_SCHEMA, schema_name="enrich",
                          max_output_tokens=len(items) * C.OUT_TOKENS_PER_REVIEW, cache_key=C.ENRICH_PROMPT_VERSION)
        try:
            result = with_retries(fn, log_attempt)
            return batch, result, attempts, None
        except (TransientError, PermanentError) as e:
            return batch, None, attempts, str(e)

    # ---- commit (main thread only: single writer, one transaction per batch) --
    def commit(self, batch, result, attempts, error, retry_round):
        review_ids = [rid for rid, _ in batch]
        good, bad = parse_and_validate(result, batch) if result else ({}, {rid: f"api_error:{error}" for rid in review_ids})
        with self.con:   # atomic: either the whole batch's results + logs land, or none do
            for a in attempts:
                u = a["usage"]
                self.con.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    a["attempt_id"], a["request_id"], self.run_id, "enrich", self.phase, self.model, self.label_config,
                    json.dumps(review_ids), a["outcome"], a["error"], a["attempt"], u.get("input_tokens"),
                    u.get("cached_input_tokens"), u.get("output_tokens"), u.get("reasoning_tokens"), a["cost"],
                    a["started"], a["ended"]))
            text_sha = dict(self.con.execute(
                f"SELECT review_id, text_sha FROM reviews WHERE review_id IN ({','.join('?' * len(review_ids))})", review_ids))
            for rid, lab in good.items():
                self.con.execute("INSERT OR IGNORE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    text_sha[rid], self.label_config, rid, lab["topic"], lab["intent"], lab["sentiment"], lab["severity"],
                    json.dumps(lab["entities"]), lab["evidence_quote"], int(lab["needs_review"]), lab["quote_source"],
                    attempts[-1]["request_id"], time.time()))
                self.con.execute("UPDATE reviews SET status='completed' WHERE text_sha=? AND status='pending'", (text_sha[rid],))
            if retry_round:   # second failure: quarantine with reason and attempt count
                for rid, why in bad.items():
                    self.con.execute("INSERT OR REPLACE INTO failures VALUES (?,?,?,?)", (rid, self.label_config, 2, why))
                    self.con.execute("UPDATE reviews SET status='quarantined', reason=? WHERE text_sha=? AND status='pending'",
                                     (f"enrich_failed:{why}", text_sha[rid]))
        return good, bad

    # ---- main loop ----------------------------------------------------------
    def run(self, limit_texts=None):
        started = time.time()
        cache_hits = self.apply_cache()
        batches = self.pending_batches(limit_texts)
        stats = {"batches_done": 0, "completed_texts": 0, "invalid_first_pass": 0, "quarantined": 0,
                 "stopped_reason": None, "cache_hits_reviews": cache_hits}
        retry_queue = []      # (batch, ) of items that failed validation once: retried ONCE in smaller requests

        def submit_next(pool, inflight, queue, retry_round):
            while queue and len(inflight) < self.workers and not self.stop.is_set():
                batch = queue[0]
                reserve = worst_case_cost(self.model, batch)
                if not self.ledger.reserve(reserve):
                    stats["stopped_reason"] = "budget_cap"
                    self.stop.set()
                    return
                queue.pop(0)
                inflight[pool.submit(self.request, batch)] = (batch, reserve, retry_round)

        def drain(queue, retry_round):
            inflight = {}
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                submit_next(pool, inflight, queue, retry_round)
                while inflight:
                    done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for fut in done:
                        batch, reserve, rr = inflight.pop(fut)
                        batch, result, attempts, error = fut.result()
                        self.ledger.settle(reserve, sum(a["cost"] or 0 for a in attempts) if result or attempts else 0)
                        good, bad = self.commit(batch, result, attempts, error, rr)
                        stats["completed_texts"] += len(good)
                        if rr:
                            stats["quarantined"] += len(bad)
                        elif bad:
                            stats["invalid_first_pass"] += len(bad)
                            bad_items = [(rid, t) for rid, t in batch if rid in bad]
                            retry_queue.extend(bad_items[i:i + 10] for i in range(0, len(bad_items), 10))
                        stats["batches_done"] += 1
                        print(f"[{self.phase}] batch {stats['batches_done']}: +{len(good)} ok, {len(bad)} bad, "
                              f"spent ${self.ledger.spent:.4f}", flush=True)
                        if self.stop_after_batches and stats["batches_done"] >= self.stop_after_batches:
                            stats["stopped_reason"] = stats["stopped_reason"] or "stop_after_batches (interruption demo)"
                            self.stop.set()
                    submit_next(pool, inflight, queue, retry_round)

        prev = signal.signal(signal.SIGINT, lambda *_: (self.stop.set(), stats.update(stopped_reason="interrupted (Ctrl-C)")))
        try:
            drain(batches, retry_round=False)
            if not self.stop.is_set():
                drain(retry_queue, retry_round=True)
        finally:
            signal.signal(signal.SIGINT, prev)
        self.apply_cache()

        counts = dict(self.con.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status").fetchall())
        summary = {**stats, "run_id": self.run_id, "phase": self.phase, "label_config": self.label_config,
                   "provider": self.provider, "workers": self.workers, "budget_usd": self.ledger.cap,
                   "spent_usd_total": round(self.ledger.spent, 6), "wall_seconds": round(time.time() - started, 2),
                   "review_status_counts": counts}
        with self.con:
            self.con.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (
                self.run_id, self.phase, started, time.time(),
                json.dumps({"model": self.model, "effort": C.EFFORT, "batch_size": C.BATCH_SIZE, "workers": self.workers,
                            "budget": self.ledger.cap, "limit_texts": limit_texts}), json.dumps(summary)))
        return summary


def main():
    ap = argparse.ArgumentParser(description="Stage 2: enrich pending reviews")
    ap.add_argument("--db", required=True)
    ap.add_argument("--provider", choices=["openai", "mock"], default="mock")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--budget", type=float, required=True, help="hard USD cap across all runs on this db")
    ap.add_argument("--limit-texts", type=int, help="process at most N distinct texts this run")
    ap.add_argument("--stop-after-batches", type=int, help="simulate an interruption after N committed batches")
    args = ap.parse_args()
    C.load_env()
    model = C.MODEL if args.provider == "openai" else "mock-keyword-v1"
    label_config = C.LABEL_CONFIG if args.provider == "openai" else f"mock+{C.ENRICH_PROMPT_VERSION}+{C.SCHEMA_VERSION}"
    summary = Enricher(args.db, args.provider, args.workers, args.budget, label_config, model,
                       args.stop_after_batches).run(args.limit_texts)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
