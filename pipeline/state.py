"""Shared run state: SQLite tables, the spend ledger, call logging and stage timing.
Every model role (enrich, verify, group, memo) logs each attempt here and draws from one budget."""

import json
import threading
import time
import uuid
from contextlib import contextmanager

from pipeline import config as C
from pipeline.ingest import connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    text_sha TEXT NOT NULL, label_config TEXT NOT NULL, origin_id TEXT NOT NULL,
    topic TEXT, intent TEXT, sentiment REAL, severity INTEGER, entities TEXT, evidence_quote TEXT,
    needs_review INTEGER, quote_source TEXT, request_id TEXT, created REAL,
    PRIMARY KEY (text_sha, label_config));
CREATE TABLE IF NOT EXISTS calls (
    attempt_id TEXT PRIMARY KEY, request_id TEXT, run_id TEXT, role TEXT, phase TEXT, model TEXT,
    label_config TEXT, review_ids TEXT, outcome TEXT, error TEXT, attempt INTEGER,
    input_tokens INTEGER, cached_input_tokens INTEGER, output_tokens INTEGER, reasoning_tokens INTEGER,
    cost_usd REAL, started REAL, ended REAL, service_tier TEXT);
CREATE TABLE IF NOT EXISTS failures (
    review_id TEXT, label_config TEXT, attempts INTEGER, reason TEXT, PRIMARY KEY (review_id, label_config));
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, phase TEXT, started REAL, ended REAL, settings TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS stages (
    run_id TEXT, stage TEXT, started REAL, ended REAL, items_in INTEGER, items_out INTEGER, notes TEXT);
"""


def open_state(db_path):
    con = connect(db_path)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(calls)")}
    if "service_tier" not in cols:                       # older runs (cp500) predate this column
        con.execute("ALTER TABLE calls ADD COLUMN service_tier TEXT DEFAULT 'standard'")
    return con


def new_run_id():
    return time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


def price(model, tier):
    return C.PRICES.get((model, tier))


def cost_of(model, tier, usage):
    p = price(model, tier)
    if not p or usage.get("input_tokens") is None:
        return None
    cached = usage.get("cached_input_tokens") or 0
    # Mutually exclusive categories; reasoning tokens are already inside output_tokens.
    return ((usage["input_tokens"] - cached) * p["input"] + cached * p["cached_input"]
            + (usage.get("output_tokens") or 0) * p["output"])


class Ledger:
    """One spend ledger shared by all workers and stages. Reserve worst-case cost before dispatch;
    stop admitting work once spent + reserved + next reservation would exceed the cap."""

    def __init__(self, con, cap):
        self.cap = cap
        self.spent = con.execute("SELECT COALESCE(SUM(cost_usd),0) FROM calls").fetchone()[0]
        self.reserved = 0.0
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


class Attempts:
    """Collects per-attempt records inside a worker thread; written later by the single DB writer."""

    def __init__(self, model, tier):
        self.model, self.tier, self.items = model, tier, []

    def __call__(self, n, outcome, result, error, started):
        usage = (result or {}).get("usage") or {}
        tier = (result or {}).get("service_tier") or self.tier
        if (result or {}).get("api") == "decisions":
            tier = "decisions"
        self.items.append({"attempt_id": uuid.uuid4().hex, "request_id": (result or {}).get("request_id"),
                           "outcome": outcome, "error": error, "attempt": n, "usage": usage, "tier": tier,
                           "cost": cost_of(self.model, tier, usage) if result else None,
                           "started": started, "ended": time.time()})

    def total_cost(self):
        return sum(a["cost"] or 0 for a in self.items)


def write_calls(con, attempts, run_id, role, phase, label_config, review_ids):
    for a in attempts.items:
        u = a["usage"]
        con.execute("INSERT INTO calls (attempt_id, request_id, run_id, role, phase, model, label_config, review_ids,"
                    " outcome, error, attempt, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,"
                    " cost_usd, started, ended, service_tier) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        a["attempt_id"], a["request_id"], run_id, role, phase, attempts.model, label_config,
                        json.dumps(review_ids), a["outcome"], a["error"], a["attempt"], u.get("input_tokens"),
                        u.get("cached_input_tokens"), u.get("output_tokens"), u.get("reasoning_tokens"),
                        a["cost"], a["started"], a["ended"], a["tier"]))


@contextmanager
def stage(con, run_id, name, items_in=None):
    """Times a stage and records it; the body may set info['items_out'] / info['notes']."""
    info = {"items_out": None, "notes": None}
    started = time.time()
    yield info
    with con:
        con.execute("INSERT INTO stages VALUES (?,?,?,?,?,?,?)", (
            run_id, name, started, time.time(), items_in, info["items_out"],
            json.dumps(info["notes"]) if info["notes"] is not None else None))
