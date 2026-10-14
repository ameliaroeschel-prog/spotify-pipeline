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

MEMO_PROMPT_VERSION = "memo-v5"
MEMO_EFFORT = "low"
TOP_ISSUES = 8
QUOTES_PER_ISSUE = 3
# The decision question's four product areas, mapped from the eight shared topics (code-owned).
RECENT = ("2023-08", "2023-10")                 # last three full months of the window
COMPARABLE = {"2022": ("2022-06", "2022-10"), "2023": ("2023-06", "2023-10")}
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
- Cite area-level numbers with the metric-specific ID: complaints as [A#c], severity_sum as [A#s],
  e.g. "79,177 complaint reviews [A2c]" and "a severity sum of 197,874 [A2s]". Never call a severity sum
  "complaints". Put each citation directly after the number it supports.
- Any comparison between areas must agree with area_ranking_by_severity_sum (computed by code). If you
  recommend an area that is not ranked first, say so explicitly and justify it with the supplied data.
- The issue "other.general" means no specific product topic was supported; do not recommend it as a fix.
- Describe an issue only by its supplied name/description and quotes; never infer what it is from IDs.
- Cite evidence quotes by review ID in parentheses, e.g. (review 1a2b...). Use only supplied IDs.
- Reviews are self-selected public feedback; cancellation language is stated intent, not observed churn.
- Recommend ONE priority area and its lead issue, compare at least two alternatives, and state limitations.
- recent_quarter and comparable_periods give code-computed recency evidence. Values are cited with their
  cite_as ID, e.g. "23.9% of recent complaints [R1p]". Percent values are already percentages.
- You advise at the END of the review window. Weigh both the full-window baseline (claims, area totals)
  and the recency evidence. Call out the trends and drivers an executive needs: which issues are growing
  or shrinking, what is driving the late-2023 complaint surge, and which problems product work can fix
  directly versus which depend on a pricing/business-model decision. Let the data decide the priority;
  if the full-window and recent views point different ways, say so and explain your choice.
Return JSON matching the schema. "recommendation" is 180-320 words of markdown prose."""
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
    area_totals = {a: {"aggregate_id": f"A{k}", "complaints": sum(by_topic.get(t, (0, 0))[0] for t in ts),
                       "severity_sum": sum(by_topic.get(t, (0, 0))[1] or 0 for t in ts), "topics": ts,
                       "cite_complaints_as": f"A{k}c", "cite_severity_sum_as": f"A{k}s"}
                   for k, (a, ts) in enumerate(AREAS.items(), 1)}
    # Code ranks the four areas so the model cannot get a comparison backwards (memo-v2 did).
    area_ranking = [{"rank": k, "area": a, "aggregate_id": v["aggregate_id"], "severity_sum": v["severity_sum"],
                     "complaints": v["complaints"]}
                    for k, (a, v) in enumerate(sorted(area_totals.items(),
                                                      key=lambda kv: (-kv[1]["severity_sum"], kv[0])), 1)]
    evidence = []
    for r in top[:5]:
        rows = con.execute("""SELECT m.review_id, x.evidence_quote, x.severity FROM membership m
                              JOIN reviews rv ON rv.review_id=m.review_id JOIN results x ON x.text_sha=rv.text_sha
                              WHERE m.issue_id=? AND rv.review_id = x.origin_id""", (r["issue_id"],)).fetchall()
        rows.sort(key=lambda t: hashlib.sha256(t[0].encode()).hexdigest())     # representative, not extreme
        evidence += [{"issue_id": r["issue_id"], "review_id": rid, "quote": q[:220], "severity": s}
                     for rid, q, s in rows[:QUOTES_PER_ISSUE]]
    recent, comparable = recent_views(con, area_ranking)
    pack = {"source_rows": sum(totals.values()), "completed": totals.get("completed", 0),
            "quarantined": totals.get("quarantined", 0), "pending": totals.get("pending", 0),
            "intent_counts": intents, "area_totals": area_totals, "area_ranking_by_severity_sum": area_ranking,
            "issues": [{"issue_id": r["issue_id"], "name": names.get(r["issue_id"], ("", ""))[0],
                        "description": names.get(r["issue_id"], ("", ""))[1], "rank": r["rank"]} for r in top],
            "claims": claims, "evidence": evidence,
            "recent_quarter": recent, "comparable_periods": comparable}
    return pack, claims, ranking


def recent_views(con, area_ranking):
    """Code-computed recency evidence (not part of the required baseline ranking), each value citable."""
    def issue_counts(lo, hi):
        return dict(con.execute("""SELECT m.issue_id, COUNT(*) FROM membership m JOIN reviews r USING(review_id)
            WHERE substr(r.review_timestamp,1,7) BETWEEN ? AND ? GROUP BY 1""", (lo, hi)).fetchall())
    def complaint_share(lo, hi):
        n, c, k = con.execute("""SELECT COUNT(*), SUM(x.intent IN ('complaint','cancellation')), SUM(x.intent='cancellation')
            FROM reviews r JOIN results x ON x.text_sha=r.text_sha
            WHERE r.status='completed' AND substr(r.review_timestamp,1,7) BETWEEN ? AND ?""", (lo, hi)).fetchone()
        return n, c, k
    rc = issue_counts(*RECENT)
    total = sum(rc.values())
    top = sorted(rc.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
    n, c, k = complaint_share(*RECENT)
    recent = {"period": f"{RECENT[0]}..{RECENT[1]}", "note": "last three full months; issue counts are memberships",
              "complaints_total": {"value": str(total), "cite_as": "R0c"},
              "complaint_share_of_all_reviews_pct": {"value": f"{100 * c / n:.1f}", "cite_as": "R0p"},
              "cancellation_intent_reviews": {"value": str(k), "cite_as": "R0k"},
              "top_issues": [{"issue_id": i, "complaints": {"value": str(v), "cite_as": f"R{j}c"},
                              "share_of_recent_complaints_pct": {"value": f"{100 * v / total:.1f}", "cite_as": f"R{j}p"}}
                             for j, (i, v) in enumerate(top, 1)]}
    a, b = (issue_counts(*COMPARABLE[y]) for y in ("2022", "2023"))
    na, nb = sum(a.values()), sum(b.values())
    watch = ["usability.ads", "billing.free_tier_limits", "billing.premium_pressure", "other.general",
             "other.bad_update_generic", "playback.crashes_wont_open"]
    sa, sb = complaint_share(*COMPARABLE["2022"]), complaint_share(*COMPARABLE["2023"])
    comparable = {"periods": {y: f"{lo}..{hi}" for y, (lo, hi) in COMPARABLE.items()},
                  "complaint_share_of_all_reviews_pct": {
                      "2022": {"value": f"{100 * sa[1] / sa[0]:.1f}", "cite_as": "T0a"},
                      "2023": {"value": f"{100 * sb[1] / sb[0]:.1f}", "cite_as": "T0b"}},
                  "issue_share_of_complaints_pct": [
                      {"issue_id": i, "2022": {"value": f"{100 * a.get(i, 0) / na:.1f}", "cite_as": f"T{j}a"},
                       "2023": {"value": f"{100 * b.get(i, 0) / nb:.1f}", "cite_as": f"T{j}b"}}
                      for j, i in enumerate(watch, 1)]}
    return recent, comparable


def _cites(obj, out):
    if isinstance(obj, dict):
        if "cite_as" in obj and "value" in obj:
            out[obj["cite_as"]] = {obj["value"]}
        for v in obj.values():
            _cites(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _cites(v, out)
    return out


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


def citation_values(pack):
    """Every citable ID -> the exact value(s) it stands for."""
    vals = {c["claim_id"]: {c["value"]} for c in pack["claims"]}
    for v in pack["area_totals"].values():
        vals[v["cite_complaints_as"]] = {str(v["complaints"])}
        vals[v["cite_severity_sum_as"]] = {str(v["severity_sum"])}
    _cites({"r": pack.get("recent_quarter"), "t": pack.get("comparable_periods")}, vals)
    return vals


def validate(memo, pack):
    problems = []
    cite_vals = citation_values(pack)
    # memo-v3 passed every "number exists" check yet called severity sums "complaints". Now the number
    # written right before each citation must equal the cited value.
    prose = memo["recommendation"] + " " + " ".join(a["why_not"] for a in memo["alternatives"])
    for m in re.finditer(r"(\d[\d,]*(?:\.\d+)?)[^\d\[\]]{0,60}?\[([CARTM]\d+[a-z]?)\]", prose):
        num, cid = m.group(1).replace(",", ""), m.group(2)
        if cid in cite_vals and num not in cite_vals[cid]:
            problems.append(f"{num} is cited as [{cid}] but [{cid}] = {sorted(cite_vals[cid])[0]}")
    claim_ids = {c["claim_id"] for c in pack["claims"]}
    review_ids = {e["review_id"] for e in pack["evidence"]}
    issue_ids = {i["issue_id"] for i in pack["issues"]}
    text = memo["recommendation"] + " " + " ".join(a["why_not"] for a in memo["alternatives"])
    for cid in set(re.findall(r"\[([CARTM]\d+[a-z]?)\]", text)) | set(memo["cited_claim_ids"]):
        if cid not in cite_vals:
            problems.append(f"unknown claim {cid}")
    for rid in set(memo["cited_review_ids"]) | set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)):
        if rid not in review_ids:
            problems.append(f"unknown review id {rid}")
    if memo["priority_issue_id"] not in issue_ids:
        problems.append(f"unknown priority issue {memo['priority_issue_id']}")
    for a in memo["alternatives"]:
        if a["issue_id"] not in issue_ids:
            problems.append(f"unknown alternative issue {a['issue_id']}")
    stripped = re.sub(r"\[[CARTM]\d+[a-z]?\]|[0-9a-f]{8}-[0-9a-f-]{27}", "", text)
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
