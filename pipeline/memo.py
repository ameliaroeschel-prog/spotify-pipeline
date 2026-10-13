"""Stage 6 — Recommend. The memo-writer role sees ONLY saved aggregates and a bounded evidence pack
(never the raw dataset). Code builds the numbered claims first; the model must cite claim IDs and
evidence review IDs; code then rejects any cited ID or number that is not in the supplied pack.

    python3 -m pipeline.memo --db runs/cp10k/state.db --provider openai --budget 0.50
"""

import argparse
import hashlib
import json
import re
import time

from pipeline import config as C
from pipeline.llm import PermanentError, TransientError, call, with_retries
from pipeline.rank import compute_ranking
from pipeline.state import Attempts, Ledger, new_run_id, open_state, stage, write_calls

MEMO_PROMPT_VERSION = "memo-v1"
MEMO_EFFORT = "low"
TOP_ISSUES = 8
QUOTES_PER_ISSUE = 3
# The decision question's four product areas, mapped from the eight shared topics (code-owned).
AREAS = {"access": ["access"], "usability": ["usability"], "playback": ["playback", "downloads"],
         "billing_support": ["billing", "support"]}

MEMO_INSTRUCTIONS = """\
You advise Spotify's product leadership: where should next quarter's product effort go — access,
usability, playback, or billing/support? You receive ONLY pre-computed aggregates from a pipeline
that classified historical Google Play reviews, plus a few evidence quotes with review IDs.

Rules:
- Every number you write must be copied exactly from the supplied data (claims or area_totals). Do not
  compute new numbers, percentages, revenue, churn, or retention effects.
- Cite issue-level numbers with their claim ID in square brackets, e.g. "1,234 complaints [C1]".
- Cite evidence quotes by review ID in parentheses, e.g. (review 1a2b...). Use only supplied IDs.
- Reviews are self-selected public feedback; cancellation language is stated intent, not observed churn.
- Recommend ONE priority area and its lead issue, compare at least two alternatives, and state limitations.
Return JSON matching the schema. "recommendation" is 120-220 words of markdown prose."""
MEMO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["priority_area", "priority_issue_id", "recommendation", "alternatives", "limitations",
                 "cited_claim_ids", "cited_review_ids"],
    "properties": {
        "priority_area": {"type": "string", "enum": list(AREAS)},
        "priority_issue_id": {"type": "string"},
        "recommendation": {"type": "string"},
        "alternatives": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                    "required": ["issue_id", "why_not"],
                                                    "properties": {"issue_id": {"type": "string"},
                                                                   "why_not": {"type": "string"}}}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "cited_claim_ids": {"type": "array", "items": {"type": "string"}},
        "cited_review_ids": {"type": "array", "items": {"type": "string"}},
    }}

SCHEMA = """
CREATE TABLE IF NOT EXISTS memo (memo_key TEXT PRIMARY KEY, memo_json TEXT, pack_json TEXT, claims_json TEXT,
    validation TEXT, request_id TEXT, created REAL);
"""


def build_pack(con):
    """Code computes everything the memo may say."""
    sev = dict(con.execute("""SELECT r.review_id, x.severity FROM reviews r JOIN results x ON x.text_sha = r.text_sha
                              WHERE r.status='completed' AND x.intent IN ('complaint','cancellation')"""))
    membership = con.execute("SELECT issue_id, review_id FROM membership").fetchall()
    ranking = compute_ranking(sev, membership)
    names = {i: (n, d) for i, n, d in con.execute("SELECT issue_id, name, description FROM issues")}
    claims, top = [], ranking[:TOP_ISSUES]
    for r in top:
        for metric in ("complaint_count", "severity_sum", "mean_severity", "priority_score"):
            claims.append({"claim_id": f"C{len(claims) + 1}", "issue_id": r["issue_id"], "metric": metric,
                           "value": str(r[metric])})
    totals = dict(con.execute("SELECT status, COUNT(*) FROM reviews GROUP BY status"))
    intents = dict(con.execute("""SELECT x.intent, COUNT(*) FROM reviews r JOIN results x ON x.text_sha=r.text_sha
                                  WHERE r.status='completed' GROUP BY 1"""))
    by_topic = {t: (n, s) for t, n, s in con.execute("""
        SELECT x.topic, COUNT(*), SUM(x.severity) FROM reviews r JOIN results x ON x.text_sha=r.text_sha
        WHERE r.status='completed' AND x.intent IN ('complaint','cancellation') GROUP BY 1""")}
    area_totals = {a: {"complaints": sum(by_topic.get(t, (0, 0))[0] for t in ts),
                       "severity_sum": sum(by_topic.get(t, (0, 0))[1] or 0 for t in ts), "topics": ts}
                   for a, ts in AREAS.items()}
    evidence = []
    for r in top[:5]:
        rows = con.execute("""SELECT m.review_id, x.evidence_quote, x.severity FROM membership m
                              JOIN reviews rv ON rv.review_id=m.review_id JOIN results x ON x.text_sha=rv.text_sha
                              WHERE m.issue_id=? AND rv.review_id = x.origin_id""", (r["issue_id"],)).fetchall()
        rows.sort(key=lambda t: (-t[2], hashlib.sha256(t[0].encode()).hexdigest()))
        evidence += [{"issue_id": r["issue_id"], "review_id": rid, "quote": q[:220], "severity": s}
                     for rid, q, s in rows[:QUOTES_PER_ISSUE]]
    pack = {"source_rows": sum(totals.values()), "completed": totals.get("completed", 0),
            "quarantined": totals.get("quarantined", 0), "pending": totals.get("pending", 0),
            "intent_counts": intents, "area_totals": area_totals,
            "issues": [{"issue_id": r["issue_id"], "name": names.get(r["issue_id"], ("", ""))[0],
                        "description": names.get(r["issue_id"], ("", ""))[1], "rank": r["rank"]} for r in top],
            "claims": claims, "evidence": evidence}
    return pack, claims, ranking


def allowed_numbers(pack):
    nums = set()
    def walk(v):
        if isinstance(v, dict):
            [walk(x) for x in v.values()]
        elif isinstance(v, list):
            [walk(x) for x in v]
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            nums.add(str(v))
        elif isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v):
            nums.add(v)
    walk({k: v for k, v in pack.items() if k != "evidence"})
    return nums | {"1", "2", "3", "4", "5"}          # severity scale levels may be named


def validate(memo, pack):
    problems = []
    claim_ids = {c["claim_id"] for c in pack["claims"]}
    review_ids = {e["review_id"] for e in pack["evidence"]}
    issue_ids = {i["issue_id"] for i in pack["issues"]}
    text = memo["recommendation"] + " " + " ".join(a["why_not"] for a in memo["alternatives"])
    for cid in set(re.findall(r"\[(C\d+)\]", text)) | set(memo["cited_claim_ids"]):
        if cid not in claim_ids:
            problems.append(f"unknown claim {cid}")
    for rid in set(memo["cited_review_ids"]) | set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)):
        if rid not in review_ids:
            problems.append(f"unknown review id {rid}")
    if memo["priority_issue_id"] not in issue_ids:
        problems.append(f"unknown priority issue {memo['priority_issue_id']}")
    for a in memo["alternatives"]:
        if a["issue_id"] not in issue_ids:
            problems.append(f"unknown alternative issue {a['issue_id']}")
    stripped = re.sub(r"\[C\d+\]|[0-9a-f]{8}-[0-9a-f-]{27}", "", text)
    ok = allowed_numbers(pack)
    for num in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", stripped):
        if num.replace(",", "") not in ok:
            problems.append(f"number not in supplied data: {num}")
    if not re.search(r"\[C\d+\]", memo["recommendation"]):
        problems.append("recommendation cites no claim IDs")
    return problems


def write_memo(db, provider, budget, run_id=None):
    con = open_state(db)
    con.executescript(SCHEMA)
    run_id = run_id or new_run_id()
    with stage(con, run_id, "memo") as info:
        pack, claims, ranking = build_pack(con)
        model = C.MODEL if provider == "openai" else "mock-keyword-v1"
        key = hashlib.sha256(json.dumps([MEMO_PROMPT_VERSION, model, MEMO_EFFORT, pack], sort_keys=True).encode()).hexdigest()
        cached = con.execute("SELECT memo_json, validation FROM memo WHERE memo_key=?", (key,)).fetchone()
        if cached:
            info["notes"] = {"reused": True}
            return json.loads(cached[0]), pack, claims, json.loads(cached[1])
        ledger, memo, problems, request_id = Ledger(con, budget), None, ["not generated"], None
        user = json.dumps(pack, ensure_ascii=False)
        for attempt in (1, 2):                                  # one corrective retry, then give up
            if provider != "openai" or not ledger.reserve(0.01):
                break
            attempts = Attempts(model, "standard")
            try:
                res = with_retries(lambda: call(provider, model=model, effort=MEMO_EFFORT, instructions=MEMO_INSTRUCTIONS,
                                                user=user, schema=MEMO_SCHEMA, schema_name="memo",
                                                max_output_tokens=2500, service_tier="standard"), attempts)
                memo = json.loads(res["text"])
                problems, request_id = validate(memo, pack), res["request_id"]
            except (TransientError, PermanentError, ValueError) as e:
                problems = [f"call failed: {e}"]
            ledger.settle(0.01, attempts.total_cost())
            with con:
                write_calls(con, attempts, run_id, "memo", "initial", f"{model}+{MEMO_PROMPT_VERSION}", [])
            if not problems:
                break
            user = json.dumps(pack, ensure_ascii=False) + "\n\nYour previous draft was rejected for: " + "; ".join(problems)
        validation = {"passed": not problems, "problems": problems}
        with con:
            con.execute("INSERT OR REPLACE INTO memo VALUES (?,?,?,?,?,?,?)", (
                key, json.dumps(memo), json.dumps(pack), json.dumps(claims), json.dumps(validation), request_id,
                time.time()))
        info["notes"] = validation
    return memo, pack, claims, validation


def main():
    ap = argparse.ArgumentParser(description="Stage 6: grounded recommendation memo")
    ap.add_argument("--db", required=True)
    ap.add_argument("--provider", choices=["openai", "mock"], default="mock")
    ap.add_argument("--budget", type=float, required=True)
    args = ap.parse_args()
    C.load_env()
    memo, pack, claims, validation = write_memo(args.db, args.provider, args.budget)
    print(json.dumps(validation, indent=2))
    print(json.dumps(memo, indent=2, ensure_ascii=False)[:3000])


if __name__ == "__main__":
    main()
