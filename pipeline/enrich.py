"""Stage 2 — Enrich. The orchestrator (code) owns the queue, state, validation, budget and logs;
the model (enricher role) only reads review text and returns labels.

Queue: one canonical review per distinct pending text, in text-hash order (so any partial run is
an unbiased sample). Up to 50 reviews per request. Results are validated and committed in one
transaction per batch; exact-duplicate reviews then reuse the result with cache_source_id.

    python3 -m pipeline.enrich --db runs/pilot/state.db --provider openai --workers 1 --budget 1.00
"""

import argparse
import json
import re
import signal
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from pipeline import config as C
from pipeline.llm import PermanentError, TransientError, call, with_retries
from pipeline.state import Attempts, Ledger, new_run_id, open_state, price, write_calls

ENTITY_RX = {term: re.compile(rx, re.I) for term, rx in C.ENTITY_TERMS.items()}


def entities_for(text):
    """Deterministic feature-term extraction (code, not model)."""
    return [term for term, rx in ENTITY_RX.items() if rx.search(text)][:6]


def worst_case_cost(model, tier, items):
    p = price(model, tier)
    if not p:
        return 0.0
    in_tokens = 2500 + sum(len(t) for _, t in items) // 2       # generous: ~4 chars/token, x2 margin
    return in_tokens * p["input"] + len(items) * C.OUT_TOKENS_PER_REVIEW * p["output"]


def _normalized(text):
    """Lowercase alphanumerics only, with a map back to original character positions."""
    chars, index = [], []
    for i, ch in enumerate(text):
        if ch.isalnum():
            chars.append(ch.lower())
            index.append(i)
    return "".join(chars), index


def find_quote(text, quote):
    """Exact substring check in code. The returned quote is ALWAYS an exact span of the source text."""
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
        # The model sometimes "fixes" spacing/punctuation while copying: align on alphanumerics only,
        # then return the ORIGINAL span between the first and last matched characters.
        norm_text, index = _normalized(text)
        norm_quote, _ = _normalized(stripped)
        j = norm_text.find(norm_quote) if len(norm_quote) >= 8 else -1
        if j >= 0:
            return text[index[j]:index[j + len(norm_quote) - 1] + 1], "model_aligned"
    if len(text) <= C.FULL_TEXT_FALLBACK_CHARS:
        return text, "full_text_fallback"
    return None, None


def is_policy_block(error):
    """OpenAI rejects a whole request if any text in it trips its usage-policy filter."""
    return bool(error) and "HTTP 400" in error and ("flagged" in error or "invalid_prompt" in error)


def parse_line(line):
    """'i|topic|subtopic|intent|sev|sent|flag|quote' -> dict, or None. Quote is last so it may contain '|'."""
    parts = line.strip().split("|", 7)
    if len(parts) < 7:
        return None
    try:
        return {"i": int(parts[0]), "t": parts[1].strip(), "u": parts[2].strip(), "n": parts[3].strip(),
                "v": int(parts[4]), "s": int(parts[5]), "f": parts[6].strip() == "1",
                "q": parts[7] if len(parts) > 7 else ""}
    except ValueError:
        return None


def parse_and_validate(result, batch):
    """Returns ({review_id: label_dict}, {review_id: problem}). Never trusts model IDs blindly."""
    by_index = {i: (rid, text) for i, (rid, text) in enumerate(batch, 1)}
    good, bad = {}, {}
    if result["status"] != "completed":
        return good, {rid: f"incomplete_response:{result['incomplete_reason']}" for rid, _ in batch}
    try:
        lines = json.loads(result["text"])["r"]
    except (ValueError, KeyError, TypeError):
        return good, {rid: "unparseable_output" for rid, _ in batch}
    seen = set()
    for line in lines:
        it = parse_line(line) if isinstance(line, str) else None
        if not it or it["i"] not in by_index or it["i"] in seen:
            continue                                   # malformed, foreign or duplicated index: ignore
        seen.add(it["i"])
        rid, text = by_index[it["i"]]
        quote, source = find_quote(text, it["q"])
        if it["t"] not in C.TOPICS or it["n"] not in C.INTENTS or it["v"] not in (1, 2, 3, 4, 5) \
                or it["s"] not in (-2, -1, 0, 1, 2):
            bad[rid] = "invalid_label_values"
        elif quote is None:
            bad[rid] = "quote_not_in_source"
        else:
            sub = it["u"] if it["u"] in C.SUBTOPICS.get(it["t"], {}) else "general"   # off-list -> general
            good[rid] = {"topic": it["t"], "subtopic": sub, "intent": it["n"], "severity": it["v"], "sentiment": it["s"] / 2,
                         "entities": entities_for(text), "evidence_quote": quote, "needs_review": it["f"],
                         "quote_source": source}
    for i, (rid, _) in by_index.items():
        if i not in seen:
            bad[rid] = "missing_from_output"
    return good, bad


class Enricher:
    def __init__(self, db_path, provider, workers, budget, label_config, model, tier,
                 stop_after_batches=None, run_id=None):
        self.provider, self.workers, self.model, self.tier = provider, workers, model, tier
        self.label_config, self.stop_after_batches = label_config, stop_after_batches
        self.con = open_state(db_path)
        other = self.con.execute("SELECT DISTINCT label_config FROM results WHERE label_config<>?",
                                 (label_config,)).fetchall()
        if other:
            raise SystemExit(f"{db_path} holds results from another label_config {other}; use a fresh run dir.")
        self.ledger = Ledger(self.con, budget)
        done_before = self.con.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        self.phase = "resume" if done_before else "initial"
        self.run_id = run_id or new_run_id()
        self.stop = threading.Event()

    # ---- queue ---------------------------------------------------------------
    def apply_cache(self):
        """Exact-text reuse: a completed result under the same label_config marks all its reviews."""
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
        attempts = Attempts(self.model, self.tier)
        fn = lambda: call(self.provider, model=self.model, effort=C.EFFORT, instructions=C.ENRICH_INSTRUCTIONS,
                          user=C.enrich_user_message(items), schema=C.ENRICH_SCHEMA, schema_name="enrich",
                          max_output_tokens=max(C.MIN_OUTPUT_TOKENS, len(items) * C.OUT_TOKENS_PER_REVIEW),
                          cache_key=C.ENRICH_PROMPT_VERSION, service_tier=self.tier)
        try:
            return batch, with_retries(fn, attempts), attempts, None
        except (TransientError, PermanentError) as e:
            return batch, None, attempts, str(e)

    # ---- commit (main thread only: single writer, one transaction per batch) --
    def commit(self, batch, result, attempts, error, retry_round, isolate=False):
        review_ids = [rid for rid, _ in batch]
        good, bad = parse_and_validate(result, batch) if result else ({}, {rid: f"api_error:{error}" for rid in review_ids})
        with self.con:   # atomic: either the whole batch's results + logs land, or none do
            self.con.executemany("INSERT INTO invalid_items VALUES (?,?,?,?,?)",
                                 [(rid, self.run_id, int(retry_round), why[:200], time.time()) for rid, why in bad.items()])
            write_calls(self.con, attempts, self.run_id, "enrich", self.phase, self.label_config, review_ids)
            text_sha = dict(self.con.execute(
                f"SELECT review_id, text_sha FROM reviews WHERE review_id IN ({','.join('?' * len(review_ids))})", review_ids))
            for rid, lab in good.items():
                self.con.execute("INSERT OR IGNORE INTO results (text_sha, label_config, origin_id, topic, intent,"
                                 " sentiment, severity, entities, evidence_quote, needs_review, quote_source, request_id,"
                                 " created, subtopic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    text_sha[rid], self.label_config, rid, lab["topic"], lab["intent"], lab["sentiment"], lab["severity"],
                    json.dumps(lab["entities"]), lab["evidence_quote"], int(lab["needs_review"]), lab["quote_source"],
                    attempts.items[-1]["request_id"], time.time(), lab["subtopic"]))
                self.con.execute("UPDATE reviews SET status='completed' WHERE text_sha=? AND status='pending'", (text_sha[rid],))
            if retry_round and not isolate:   # second failure: quarantine with reason and attempt count
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
                 "stopped_reason": None, "cache_hits_reviews": cache_hits, "requests_planned": len(batches)}
        total_batches = len(batches)
        retry_queue = []      # items that failed validation once: retried ONCE in smaller requests

        def submit_next(pool, inflight, queue, retry_round):
            while queue and len(inflight) < self.workers and not self.stop.is_set():
                reserve = worst_case_cost(self.model, self.tier, queue[0])
                if not self.ledger.reserve(reserve):
                    stats["stopped_reason"] = "budget_cap"
                    self.stop.set()
                    return
                batch = queue.pop(0)
                inflight[pool.submit(self.request, batch)] = (reserve, retry_round)

        def drain(queue, retry_round):
            inflight = {}
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                submit_next(pool, inflight, queue, retry_round)
                while inflight:
                    done, _ = wait(inflight, return_when=FIRST_COMPLETED)
                    for fut in done:
                        reserve, rr = inflight.pop(fut)
                        batch, result, attempts, error = fut.result()
                        self.ledger.settle(reserve, attempts.total_cost())
                        # A policy-blocked multi-review request is split into single-review requests, so
                        # one offending text cannot quarantine its innocent neighbours.
                        isolate = rr and result is None and is_policy_block(error) and len(batch) > 1
                        good, bad = self.commit(batch, result, attempts, error, rr, isolate)
                        stats["completed_texts"] += len(good)
                        if isolate:
                            stats["policy_isolated"] = stats.get("policy_isolated", 0) + len(batch)
                            queue.extend([item] for item in batch)
                        elif rr:
                            stats["quarantined"] += len(bad)
                        elif bad:
                            stats["invalid_first_pass"] += len(bad)
                            bad_items = [(rid, t) for rid, t in batch if rid in bad]
                            retry_queue.extend(bad_items[i:i + C.RETRY_BATCH_SIZE] for i in range(0, len(bad_items), C.RETRY_BATCH_SIZE))
                        stats["batches_done"] += 1
                        if stats["batches_done"] % 10 == 0 or total_batches <= 20:
                            print(f"[{self.phase}] batch {stats['batches_done']}/{total_batches}: +{len(good)} ok, "
                                  f"{len(bad)} bad, spent ${self.ledger.spent:.4f}", flush=True)
                        if self.stop_after_batches and stats["batches_done"] >= self.stop_after_batches:
                            stats["stopped_reason"] = stats["stopped_reason"] or "stop_after_batches (interruption demo)"
                            self.stop.set()
                    submit_next(pool, inflight, queue, retry_round)

        def on_sigint(*_):
            self.stop.set()
            stats["stopped_reason"] = "interrupted (Ctrl-C): in-flight batches finish and are saved"
        prev = signal.signal(signal.SIGINT, on_sigint)
        try:
            drain(batches, retry_round=False)
            if not self.stop.is_set():
                drain(retry_queue, retry_round=True)
        finally:
            signal.signal(signal.SIGINT, prev)
        self.apply_cache()

        counts = dict(self.con.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status").fetchall())
        summary = {**stats, "run_id": self.run_id, "phase": self.phase, "label_config": self.label_config,
                   "provider": self.provider, "service_tier": self.tier, "workers": self.workers,
                   "budget_usd": self.ledger.cap, "spent_usd_total": round(self.ledger.spent, 6),
                   "wall_seconds": round(time.time() - started, 2), "review_status_counts": counts}
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)", (
                self.run_id, self.phase, started, time.time(),
                json.dumps({"model": self.model, "effort": C.EFFORT, "batch_size": C.BATCH_SIZE, "workers": self.workers,
                            "service_tier": self.tier, "budget": self.ledger.cap, "limit_texts": limit_texts}),
                json.dumps(summary)))
        return summary


def label_config_for(provider):
    return C.LABEL_CONFIG if provider == "openai" else f"mock+{C.ENRICH_PROMPT_VERSION}+{C.SCHEMA_VERSION}"


def main():
    ap = argparse.ArgumentParser(description="Stage 2: enrich pending reviews")
    ap.add_argument("--db", required=True)
    ap.add_argument("--provider", choices=["openai", "mock"], default="mock")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--service-tier", choices=["standard", "flex"], default=C.DEFAULT_SERVICE_TIER)
    ap.add_argument("--budget", type=float, required=True, help="hard USD cap across all runs on this db")
    ap.add_argument("--limit-texts", type=int, help="process at most N distinct texts this run")
    ap.add_argument("--stop-after-batches", type=int, help="simulate an interruption after N committed batches")
    args = ap.parse_args()
    C.load_env()
    model = C.MODEL if args.provider == "openai" else "mock-keyword-v1"
    summary = Enricher(args.db, args.provider, args.workers, args.budget, label_config_for(args.provider), model,
                       args.service_tier, args.stop_after_batches).run(args.limit_texts)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
