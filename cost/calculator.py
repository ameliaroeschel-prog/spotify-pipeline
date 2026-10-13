#!/usr/bin/env python3
"""100-review cost and runtime calculator (stdlib only, Python 3).

Three commands:

  python3 cost/calculator.py                  # OFFLINE REPLAY (default). No API key, no network.
                                              # Reads cost/pilot_*.jsonl + cost/rates.csv, recomputes
                                              # every charge, writes cost/report.md and cost/usage.csv.
  python3 cost/calculator.py export --run-dir runs/pilot
                                              # Offline. Copies saved evidence out of runs/pilot/state.db
                                              # into cost/pilot_calls.jsonl, pilot_records.jsonl,
                                              # pilot_stages.jsonl, pilot_runs.jsonl and usage.csv.
  python3 cost/calculator.py pilot --budget 0.25 --yes
                                              # The ONLY paid path. Runs the real pipeline on
                                              # data/cost_100.csv twice (cold, then warm) and exports.

Cost rule used everywhere (COST_CALCULATOR.md section 3):
    item_cost  = billed_units x price_per_unit
    total_cost = sum(item_cost)
with three mutually exclusive token items per call:
    input        = input_tokens - cached_input_tokens   (uncached input)
    cached_input = cached_input_tokens
    output       = output_tokens                        (reasoning tokens are already inside this)

Importing this file does nothing except define functions: no network, no subprocess, no files written.
"""

import argparse
import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

COST_DIR = Path(__file__).resolve().parent
ROOT = COST_DIR.parent

# Full-run scope from GRADING_CONTRACT.md / data/manifest.json.
FULL_ROWS = 660_622
FULL_NONEMPTY = 660_609
FULL_EMPTY = 13
FULL_DISTINCT = 484_189

STAGES = ["ingest", "enrich", "verify", "group", "rank", "memo", "export"]
API_ROLES = ["enrich", "verify", "group", "memo"]
ITEMS = ["input", "cached_input", "output"]          # item names used in rates.csv
CALL_FIELDS = ["attempt_id", "request_id", "run_id", "role", "phase", "model", "service_tier",
               "label_config", "review_ids", "outcome", "error", "attempt", "input_tokens",
               "cached_input_tokens", "output_tokens", "reasoning_tokens", "cost_usd", "started", "ended"]


# ----------------------------------------------------------------------------------------------
# Small file helpers
# ----------------------------------------------------------------------------------------------

def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def money(x, digits=6):
    """Format USD; None means unknown and must never print as $0."""
    return "unknown" if x is None else f"${x:,.{digits}f}"


def num(x, digits=0):
    if x is None:
        return "unknown"
    return f"{x:,.{digits}f}"


def secs(x):
    return "unknown" if x is None else f"{x:,.1f} s"


# ----------------------------------------------------------------------------------------------
# Rates and per-call pricing
# ----------------------------------------------------------------------------------------------

def load_rates(path):
    """rates.csv -> {(provider, model, service_tier, item): row}. Checks per-unit vs per-million prices agree."""
    rates = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["price_per_unit_usd"] = float(row["price_per_unit_usd"])
            per_million = row.get("price_per_million_usd")
            if per_million not in (None, ""):
                if abs(float(per_million) / 1_000_000 - row["price_per_unit_usd"]) > 1e-15:
                    raise ValueError(f"rates.csv: per-unit and per-million prices disagree for {row}")
            rates[(row["provider"], row["model"], row["service_tier"], row["item"])] = row
    return rates


def provider_of(call):
    if call.get("provider"):
        return call["provider"]
    model = (call.get("model") or "").lower()
    if model.startswith("gpt") or model.startswith("o"):
        return "openai"
    if model.startswith("claude"):
        return "anthropic"
    return model or "unknown"


def rate_tier_of(call):
    """Which price row applies. The verify role uses OpenAI's Decisions API, which has its own prices."""
    tier = call.get("service_tier") or "standard"
    if call.get("role") == "verify" or tier == "decisions":
        return "decisions"
    return tier


def billed_units(call):
    """Split usage into mutually exclusive billing items. Returns None when usage is unknown."""
    inp, cached, out = call.get("input_tokens"), call.get("cached_input_tokens"), call.get("output_tokens")
    if inp is None or out is None:
        return None
    cached = cached or 0
    return {"input": inp - cached, "cached_input": cached, "output": out}


def price_call(call, rates, multiplier=1.0):
    """Recompute one attempt's charge from usage x rates. multiplier scales every API price.
    Returns {"status": priced | usage_unknown | no_rate, "cost": float or None, "items": {...}}."""
    units = billed_units(call)
    if units is None:
        return {"status": "usage_unknown", "cost": None, "items": {}}
    key = (provider_of(call), call.get("model"), rate_tier_of(call))
    items, total = {}, 0.0
    for item, n in units.items():
        row = rates.get(key + (item,))
        if row is None:
            if n == 0:
                continue          # nothing billed for this item, a missing rate does not matter
            return {"status": "no_rate", "cost": None, "items": {}, "missing": key + (item,)}
        price = row["price_per_unit_usd"] * multiplier
        items[item] = {"units": n, "unit": row["unit"], "price": price, "cost": n * price,
                       "source_url": row["source_url"], "checked_date": row["checked_date"]}
        total += n * price
    return {"status": "priced", "cost": total, "items": items}


# ----------------------------------------------------------------------------------------------
# Measured pilot: summarise calls per run (cold/warm) and role
# ----------------------------------------------------------------------------------------------

def review_ids_of(call):
    ids = call.get("review_ids") or []
    if isinstance(ids, str):
        ids = json.loads(ids) if ids.strip() else []
    return ids


def summarise_role(calls, rates, multiplier):
    """Totals for one group of attempts (e.g. cold-run enrich)."""
    s = {"attempts": len(calls), "succeeded": 0, "failed": 0, "retries": 0, "fallbacks": 0,
         "request_ids": set(), "ids_sent": set(), "ids_succeeded": set(), "max_batch": 0,
         "input": 0, "cached_input": 0, "uncached_input": 0, "output": 0, "reasoning": 0,
         "usage_unknown": 0, "no_rate": 0, "cost": 0.0, "cost_succeeded": 0.0,
         "item_cost": {i: 0.0 for i in ITEMS}, "item_cost_succeeded": {i: 0.0 for i in ITEMS},
         "item_units_succeeded": {i: 0 for i in ITEMS},
         "models": {}, "tiers": set(), "label_configs": set(), "call_seconds": 0.0}
    for c in calls:
        s["models"][c.get("model")] = s["models"].get(c.get("model"), 0) + 1
    primary_model = max(s["models"], key=s["models"].get) if s["models"] else None
    for c in calls:
        ok = c.get("outcome") == "succeeded"
        ids = review_ids_of(c)
        s["succeeded" if ok else "failed"] += 1
        if (c.get("attempt") or 1) > 1:
            s["retries"] += 1
        if c.get("model") != primary_model:
            s["fallbacks"] += 1
        if c.get("request_id"):
            s["request_ids"].add(c["request_id"])
        s["ids_sent"].update(ids)
        if ok:
            s["ids_succeeded"].update(ids)
            s["max_batch"] = max(s["max_batch"], len(ids))
        s["tiers"].add(c.get("service_tier") or "standard")
        if c.get("label_config"):
            s["label_configs"].add(c["label_config"])
        if c.get("started") is not None and c.get("ended") is not None:
            s["call_seconds"] += c["ended"] - c["started"]
        bill = price_call(c, rates, multiplier)
        if bill["status"] == "usage_unknown":
            s["usage_unknown"] += 1
            continue
        units = billed_units(c)
        s["input"] += c["input_tokens"]
        s["cached_input"] += units["cached_input"]
        s["uncached_input"] += units["input"]
        s["output"] += c["output_tokens"]
        s["reasoning"] += c.get("reasoning_tokens") or 0
        if bill["status"] == "no_rate":
            s["no_rate"] += 1
            continue
        s["cost"] += bill["cost"]
        for item, v in bill["items"].items():
            s["item_cost"][item] += v["cost"]
            if ok:
                s["item_cost_succeeded"][item] += v["cost"]
                s["item_units_succeeded"][item] += v["units"]
        if ok:
            s["cost_succeeded"] += bill["cost"]
    s["primary_model"] = primary_model
    s["unresolved"] = s["usage_unknown"] + s["no_rate"]
    return s


def summarise_calls(calls, rates, multiplier):
    """{"cold": {role: summary}, "warm": {...}} plus run-level totals."""
    out = {}
    for run in ("cold", "warm"):
        run_calls = [c for c in calls if c.get("cold_or_warm") == run]
        roles = {}
        for role in API_ROLES:
            role_calls = [c for c in run_calls if c.get("role") == role]
            roles[role] = summarise_role(role_calls, rates, multiplier)
        total = summarise_role(run_calls, rates, multiplier)
        out[run] = {"roles": roles, "total": total, "present": bool(run_calls)}
    return out


def bill_lines(calls, rates, multiplier):
    """Aggregate charged items by (run, role, provider/model/tier, item) for the report's billing table."""
    lines = {}
    for c in calls:
        bill = price_call(c, rates, multiplier)
        for item, v in bill["items"].items():
            key = (c.get("cold_or_warm"), c.get("role"), provider_of(c), c.get("model"), rate_tier_of(c), item)
            line = lines.setdefault(key, {"units": 0, "cost": 0.0, "price": v["price"], "unit": v["unit"],
                                          "source_url": v["source_url"], "checked_date": v["checked_date"]})
            line["units"] += v["units"]
            line["cost"] += v["cost"]
    return lines


def usage_rows(calls, rates):
    """usage.csv: one row per attempt x billing item, at rates.csv prices (no multiplier)."""
    rows = []
    for c in calls:
        bill = price_call(c, rates, 1.0)
        base = {"attempt_id": c.get("attempt_id"), "request_id": c.get("request_id"), "run_id": c.get("run_id"),
                "cold_or_warm": c.get("cold_or_warm"), "role": c.get("role"), "outcome": c.get("outcome"),
                "provider": provider_of(c), "model": c.get("model"), "rate_tier": rate_tier_of(c)}
        if bill["status"] != "priced":
            rows.append({**base, "item": "UNRESOLVED", "billed_units": "", "unit": "",
                         "price_per_unit_usd": "", "cost_usd": "", "source_url": "",
                         "note": "usage unknown" if bill["status"] == "usage_unknown" else f"no rate for {bill.get('missing')}"})
            continue
        for item, v in bill["items"].items():
            rows.append({**base, "item": item, "billed_units": v["units"], "unit": v["unit"],
                         "price_per_unit_usd": f"{v['price']:.12g}", "cost_usd": f"{v['cost']:.10f}",
                         "source_url": v["source_url"], "note": ""})
    return rows


def write_usage_csv(path, calls, rates):
    fields = ["attempt_id", "request_id", "run_id", "cold_or_warm", "role", "outcome", "provider", "model",
              "rate_tier", "item", "billed_units", "unit", "price_per_unit_usd", "cost_usd", "source_url", "note"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(usage_rows(calls, rates))


# ----------------------------------------------------------------------------------------------
# Pilot inputs, records and timing
# ----------------------------------------------------------------------------------------------

def input_check(input_csv, manifest_path):
    """Checksum of data/cost_100.csv vs manifest, plus its IDs and distinct texts."""
    info = {"path": str(input_csv), "sha256": None, "expected": None, "ok": False, "ids": [], "distinct_texts": None}
    if not Path(input_csv).exists():
        return info
    info["sha256"] = file_sha256(input_csv)
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        info["expected"] = manifest["files"][Path(input_csv).name]["sha256"]
    except (OSError, KeyError, ValueError):
        pass
    info["ok"] = info["sha256"] == info["expected"]
    with open(input_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    info["ids"] = [r["review_id"] for r in rows]
    nonempty = [r["review_text"] for r in rows if r["review_text"].strip()]
    info["nonempty"] = len(nonempty)
    info["distinct_texts"] = len(set(nonempty))
    return info


def stage_times(stages, run):
    """{stage: seconds} for one run from pilot_stages.jsonl rows; also end-to-end clock."""
    out, e2e = {}, None
    for s in stages:
        if s.get("cold_or_warm") != run:
            continue
        if s.get("started") is None or s.get("ended") is None:
            continue
        seconds = s["ended"] - s["started"]
        if s["stage"] == "end_to_end":
            e2e = {"seconds": seconds, "source": s.get("source", "")}
        else:
            out[s["stage"]] = out.get(s["stage"], 0.0) + seconds
    return out, e2e


def run_settings(runs, run):
    """Merged settings JSON of the runs labelled cold/warm (first one wins on conflicts)."""
    merged = {}
    for r in runs:
        if r.get("cold_or_warm") == run:
            for source in (r.get("summary") or {}, r.get("settings") or {}):
                for k, v in source.items():
                    merged.setdefault(k, v)
    return merged


# ----------------------------------------------------------------------------------------------
# Projection to the full run
# ----------------------------------------------------------------------------------------------

def scope(projected_rows):
    """Rows, nonempty, empty and distinct texts for the projected run. Exact contract numbers at the
    default size; other sizes scale proportionally (labelled as such)."""
    if projected_rows == FULL_ROWS:
        return {"rows": FULL_ROWS, "nonempty": FULL_NONEMPTY, "empty": FULL_EMPTY, "distinct": FULL_DISTINCT,
                "exact": True}
    r = projected_rows / FULL_ROWS
    empty = round(FULL_EMPTY * r)
    return {"rows": projected_rows, "nonempty": projected_rows - empty, "empty": empty,
            "distinct": round(FULL_DISTINCT * r), "exact": False}


def project(measured, stage_secs, settings, rates, args):
    """Base / conservative / ceiling scenarios. Uses ONLY cold-run measurements (a full run starts cold)."""
    roles = measured["cold"]["roles"]
    sc = scope(args.projected_rows)
    enrich, verify = roles["enrich"], roles["verify"]
    enrich_units = len(enrich["ids_succeeded"])        # distinct texts actually classified by the model
    verify_units = len(verify["ids_succeeded"])

    def per_unit(role_summary, units):
        if not units:
            return None
        return {item: role_summary["item_cost_succeeded"][item] / units for item in ITEMS}

    def tokens_per_unit(role_summary, units):
        if not units:
            return None
        return {item: role_summary["item_units_succeeded"][item] / units for item in ITEMS}

    def retry_rate(role_summary):
        return role_summary["failed"] / role_summary["succeeded"] if role_summary["succeeded"] else 0.0

    measured_retry = retry_rate(enrich)
    base_retry = args.retry_rate if args.retry_rate is not None else measured_retry
    cons_retry = max(0.10, 2 * base_retry)
    measured_fallback = (enrich["fallbacks"] / enrich["attempts"]) if enrich["attempts"] else 0.0
    measured_verify_rate = (verify_units / enrich_units) if enrich_units else 0.0
    verify_rate = args.verify_rate if args.verify_rate is not None else measured_verify_rate

    enrich_pu, verify_pu = per_unit(enrich, enrich_units), per_unit(verify, verify_units)
    enrich_tpu = tokens_per_unit(enrich, enrich_units)

    # Price of the stage's primary model/tier, for the output-token-cap ceiling.
    out_price = None
    if enrich["primary_model"]:
        tier = sorted(enrich["tiers"])[0] if enrich["tiers"] else "standard"
        row = rates.get((provider_of({"model": enrich["primary_model"]}), enrich["primary_model"], tier, "output"))
        out_price = row["price_per_unit_usd"] * args.rate_multiplier if row else None

    # Fallback = re-running a text on the stronger model (all its input billed as uncached).
    fb_in = rates.get(("anthropic", args.fallback_model, "standard", "input")) or \
        rates.get(("openai", args.fallback_model, "standard", "input"))
    fb_out = rates.get(("anthropic", args.fallback_model, "standard", "output")) or \
        rates.get(("openai", args.fallback_model, "standard", "output"))
    fb_unit_cost = None
    if enrich_tpu and fb_in and fb_out:
        fb_unit_cost = args.rate_multiplier * (
            (enrich_tpu["input"] + enrich_tpu["cached_input"]) * fb_in["price_per_unit_usd"]
            + enrich_tpu["output"] * fb_out["price_per_unit_usd"])

    scenarios = {
        "base": {"retry": base_retry, "fallback": measured_fallback, "output_at_cap": False},
        "conservative": {"retry": cons_retry, "fallback": args.fallback_fraction, "output_at_cap": False},
        "ceiling": {"retry": cons_retry, "fallback": args.fallback_fraction, "output_at_cap": True},
    }
    results = {}
    for name, sv in scenarios.items():
        res = {"assumptions": sv}
        for label, texts in (("reuse", sc["distinct"]), ("no_reuse", sc["nonempty"])):
            stages = {}
            # enrich: variable work, one unit per distinct text (or per nonempty row without reuse)
            if enrich_pu is None:
                stages["enrich"] = None
            else:
                pu = dict(enrich_pu)
                if sv["output_at_cap"] and out_price is not None:
                    pu["output"] = max(enrich_tpu["output"], args.output_token_cap) * out_price
                unit = sum(pu.values())
                cost = texts * unit * (1 + sv["retry"])
                if sv["fallback"]:
                    cost = None if fb_unit_cost is None else cost + texts * sv["fallback"] * fb_unit_cost
                stages["enrich"] = cost
            # verify: verify-rate x texts, each at the measured per-review verify cost
            if verify_pu is not None:
                stages["verify"] = texts * verify_rate * sum(verify_pu.values()) * (1 + sv["retry"])
            elif args.verify_rate == 0 or (verify_rate == 0 and "verify" in stage_secs):
                stages["verify"] = 0.0        # declared off, or the stage ran without API calls
            else:
                stages["verify"] = None       # not measured: unknown, never $0
            # group, memo: fixed overhead counted once (measured cold cost, incl. its failed attempts)
            for role in ("group", "memo"):
                r = roles[role]
                if r["unresolved"] or (not r["attempts"] and role not in stage_secs):
                    stages[role] = None       # stage not measured in the cold pilot: unknown, never $0
                else:
                    stages[role] = r["cost"] * (1 + (sv["retry"] if name != "base" else 0))
            total = None if any(v is None for v in stages.values()) else sum(stages.values())
            res[label] = {"stages": stages, "total": total, "texts": texts}
        results[name] = res

    # ---- time projection (rate multiplier never touches this) ----
    pilot_workers = settings.get("workers") or 1
    workers = args.max_workers or pilot_workers
    speedup = workers / pilot_workers
    pilot_rows = measured.get("pilot_rows") or 100
    time_rows = {}
    for stage in STAGES:
        t = stage_secs.get(stage)
        if t is None:
            time_rows[stage] = {"base": None, "conservative": None, "rule": "not measured"}
            continue
        if stage == "enrich":
            work = sc["distinct"] / enrich_units if enrich_units else None
            rule = f"scales with distinct texts, / {speedup:g}x workers (modeled linear speedup)"
            base = None if work is None else t * work / speedup
        elif stage == "verify":
            work = (sc["distinct"] * verify_rate) / verify_units if verify_units else None
            rule = f"scales with verify-rate x texts, / {speedup:g}x workers (modeled)"
            base = None if work is None else t * work / speedup
        elif stage in ("group", "memo"):
            rule, base = "fixed overhead, counted once", t
        else:
            rule, base = "scales with input rows (local code)", t * sc["rows"] / pilot_rows
        # Measured stage time already includes the pilot's own retries; conservative adds the extra ones.
        extra = (1 + cons_retry) / (1 + measured_retry)
        cons = None if base is None else (base * extra if stage in ("enrich", "verify") else base)
        time_rows[stage] = {"base": base, "conservative": cons, "rule": rule, "measured": t}
    # Totals add the measured stages; stages with no timing are listed, never counted as 0 seconds.
    totals = {k: sum(v[k] for v in time_rows.values() if v[k] is not None) for k in ("base", "conservative")}
    totals["missing"] = [stage for stage, v in time_rows.items() if v["base"] is None]

    return {"scope": sc, "scenarios": results, "time": time_rows, "time_totals": totals,
            "workers": workers, "pilot_workers": pilot_workers,
            "inputs": {"enrich_units": enrich_units, "verify_units": verify_units,
                       "measured_retry": measured_retry, "base_retry": base_retry, "cons_retry": cons_retry,
                       "measured_fallback": measured_fallback, "verify_rate": verify_rate,
                       "measured_verify_rate": measured_verify_rate, "enrich_per_unit": enrich_pu,
                       "enrich_tokens_per_unit": enrich_tpu, "verify_per_unit": verify_pu,
                       "fallback_unit_cost": fb_unit_cost, "output_price": out_price}}


def budget_warnings(proj, budget):
    warnings = []
    for name, res in proj["scenarios"].items():
        for label in ("reuse", "no_reuse"):
            total = res[label]["total"]
            if total is None:
                warnings.append(f"WARNING: {name}/{label} total is UNKNOWN (unresolved usage), cannot confirm it fits the ${budget:,.2f} budget")
            elif total > budget:
                warnings.append(f"WARNING: {name} scenario ({label.replace('_', '-')}) projects {money(total, 2)}, "
                                f"which EXCEEDS the ${budget:,.2f} budget")
    return warnings


# ----------------------------------------------------------------------------------------------
# Replay: build the whole report
# ----------------------------------------------------------------------------------------------

def compute(args):
    """Everything the report shows, as one dict (used by the CLI and the tests)."""
    cost_dir = Path(args.cost_dir)
    rates = load_rates(args.rates)
    calls = read_jsonl(cost_dir / "pilot_calls.jsonl")
    records = read_jsonl(cost_dir / "pilot_records.jsonl")
    stages = read_jsonl(cost_dir / "pilot_stages.jsonl")
    runs = read_jsonl(cost_dir / "pilot_runs.jsonl")
    if not calls and not records:
        raise SystemExit(f"No pilot evidence in {cost_dir}. Run `python3 cost/calculator.py export --run-dir runs/pilot` "
                         f"(offline) or the explicit paid pilot first. See cost/README.md.")

    measured = summarise_calls(calls, rates, args.rate_multiplier)
    inp = input_check(args.input_csv, args.manifest)
    measured["pilot_rows"] = len(inp["ids"]) or 100

    status = {}
    for r in records:
        status[r.get("status")] = status.get(r.get("status"), 0) + 1
    record_ids = [r["review_id"] for r in records]
    cache_hits = sum(1 for r in records if r.get("cache_source_id"))

    timing = {}
    for run in ("cold", "warm"):
        st, e2e = stage_times(stages, run)
        api_cost = measured[run]["total"]["cost"]
        unresolved = measured[run]["total"]["unresolved"]
        completed = status.get("completed", 0)
        wall = e2e["seconds"] if e2e else None
        timing[run] = {"stages": st, "e2e": e2e, "call_seconds": measured[run]["total"]["call_seconds"],
                       "api_cost": api_cost, "unresolved": unresolved,
                       "per_1000_inputs": api_cost / measured["pilot_rows"] * 1000,
                       "per_completed": api_cost / completed if completed else None,
                       "throughput": (measured["pilot_rows"] / wall) if wall else None}

    settings = run_settings(runs, "cold")
    proj = project(measured, timing["cold"]["stages"], settings, rates, args)
    warnings = budget_warnings(proj, args.budget)
    return {"measured": measured, "input": inp, "status": status, "record_ids": record_ids,
            "cache_hits": cache_hits, "timing": timing, "settings": settings,
            "warm_settings": run_settings(runs, "warm"), "projection": proj, "warnings": warnings,
            "bill_lines": bill_lines(calls, rates, args.rate_multiplier), "calls": calls, "rates": rates,
            "records": records}


def effort_of(label_configs, settings):
    for lc in label_configs:
        for part in lc.split("+"):
            if part.startswith("effort-"):
                return part[len("effort-"):]
    return settings.get("effort") or "not recorded"


def render_report(res, args):
    m, inp, timing, proj = res["measured"], res["input"], res["timing"], res["projection"]
    cold, warm = m["cold"], m["warm"]
    settings = res["settings"]
    L = []
    add = L.append
    add("# 100-review pilot: measured cost/runtime and full-run projection\n")
    add("Generated by `python3 cost/calculator.py` (offline replay, no API calls). Every dollar figure is "
        "recomputed here as `billed_units x price_per_unit` from [`pilot_calls.jsonl`](pilot_calls.jsonl) and "
        "[`rates.csv`](rates.csv); the per-attempt mapping is in [`usage.csv`](usage.csv).\n")
    add(f"**Controls used:** budget ${args.budget:,.2f} | max workers {proj['workers']} | rate multiplier "
        f"{args.rate_multiplier:g} | retry rate {'measured' if args.retry_rate is None else args.retry_rate} | "
        f"max fallback fraction {args.fallback_fraction:g} ({args.fallback_model}) | output-token cap "
        f"{args.output_token_cap}/review | verify rate "
        f"{'measured' if args.verify_rate is None else args.verify_rate} | projected rows {args.projected_rows:,}\n")
    if any("SYNTHETIC" in str(c.get("run_id")) for c in res["calls"]):
        add("> **SYNTHETIC TEST FIXTURE - not a measured pilot.** Calls, tokens and times are invented for testing.\n")
    if res["warnings"]:
        add("> " + "\n> ".join(res["warnings"]) + "\n")
    else:
        add(f"> All projected scenarios fit the ${args.budget:,.2f} budget.\n")

    # ---------------- measured ----------------
    add("## 1. Measured on the 100-review pilot\n")
    add("### Input and records\n")
    add("| Check | Value |\n|---|---|")
    add(f"| Input file | `{Path(inp['path']).name}` |")
    add(f"| SHA-256 | `{inp['sha256']}` |")
    add(f"| Manifest SHA-256 | `{inp['expected']}` -> **{'MATCH' if inp['ok'] else 'MISMATCH'}** |")
    ids_match = sorted(inp["ids"]) == sorted(res["record_ids"])
    add(f"| IDs in input / in pilot_records | {len(inp['ids'])} / {len(res['record_ids'])} -> "
        f"**{'same IDs' if ids_match else 'DIFFERENT ID sets'}** |")
    st = res["status"]
    add(f"| Completed / failed / quarantined | {st.get('completed', 0)} / {st.get('failed', 0) + st.get('pending', 0)} / "
        f"{st.get('quarantined', 0)} |")
    add(f"| Nonempty texts / distinct texts | {inp.get('nonempty')} / {inp['distinct_texts']} |")
    add(f"| Exact-text reuse inside the pilot (records with `cache_source_id`) | {res['cache_hits']} |")
    warm_enrich = warm["roles"]["enrich"]["attempts"]
    add(f"| Warm run: new enrich calls (result-cache check) | "
        f"{warm_enrich if warm['present'] or res['timing']['warm']['e2e'] else 'warm run not recorded'}"
        f"{' -> zero new enrichment calls' if warm_enrich == 0 and (warm['present'] or res['timing']['warm']['e2e']) else ''} |")
    add("")

    add("### Per-stage settings (cold run)\n")
    add("| Stage | Provider | Model | Tier | Effort | Prompt/schema version (label_config) | Max batch | Workers |")
    add("|---|---|---|---|---|---|---|---|")
    workers = settings.get("workers", "not recorded")
    for role in API_ROLES:
        r = cold["roles"][role]
        if not r["attempts"]:
            add(f"| {role} | - | no API calls in cold run | - | - | - | - | - |")
            continue
        models = ", ".join(f"{k} ({v})" for k, v in r["models"].items())
        add(f"| {role} | {settings.get('provider') or provider_of({'model': r['primary_model']})} | {models} | "
            f"{', '.join(sorted(r['tiers']))} | {effort_of(r['label_configs'], settings)} | "
            f"{', '.join(sorted(r['label_configs'])) or 'not recorded'} | {r['max_batch']} | {workers} |")
    add("| ingest, rank, export | local code | none | - | - | - | - | - |\n")

    for run, data in (("cold", cold), ("warm", warm)):
        add(f"### Requests, attempts, usage and API cost: {run} run\n")
        if not data["present"]:
            add(f"No API attempts recorded for the {run} run.\n")
            continue
        add("| Stage | Requests | Attempts | Succeeded | Failed | Retries | Fallbacks | Input tok | of which cached | "
            "Output tok | of which reasoning | Unresolved usage | API cost |")
        add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for role in API_ROLES + ["TOTAL"]:
            r = data["total"] if role == "TOTAL" else data["roles"][role]
            cost = money(r["cost"]) + (f" + {r['unresolved']} unresolved" if r["unresolved"] else "")
            add(f"| {role} | {len(r['request_ids'])} | {r['attempts']} | {r['succeeded']} | {r['failed']} | {r['retries']} | "
                f"{r['fallbacks']} | {r['input']:,} | {r['cached_input']:,} | {r['output']:,} | {r['reasoning']:,} | "
                f"{r['unresolved']} | {cost} |")
        add("")

    add("### Billing items (recomputed)\n")
    add("| Run | Stage | Provider / model / tier | Item | Billed units | Price per unit (USD) | Cost | Source (checked) |")
    add("|---|---|---|---|---|---|---|---|")
    for key in sorted(res["bill_lines"], key=lambda k: tuple(str(x) for x in k)):
        run, role, prov, model, tier, item = key
        v = res["bill_lines"][key]
        add(f"| {run} | {role} | {prov} / {model} / {tier} | {item} | {v['units']:,} {v['unit']}s | "
            f"{v['price']:.12g} | {money(v['cost'], 8)} | [{v['source_url']}]({v['source_url']}) ({v['checked_date']}) |")
    add("\nUnresolved attempts (usage unknown or no rate) are counted above and never treated as $0.\n")

    add("### Wall clock, throughput and unit cost\n")
    add("| Metric | Cold run | Warm run |\n|---|---|---|")

    def both(fn):
        return " | ".join(fn(timing[r]) for r in ("cold", "warm"))
    add(f"| End-to-end wall clock | {both(lambda t: secs(t['e2e']['seconds']) + ' (' + t['e2e']['source'] + ')' if t['e2e'] else 'not recorded')} |")
    for stage in STAGES:
        add(f"| Stage time: {stage} | {both(lambda t: secs(t['stages'].get(stage)))} |")
    add(f"| Sum of API call durations (not wall clock; overlaps when workers > 1) | {both(lambda t: secs(t['call_seconds']))} |")
    add(f"| Throughput (input rows / wall-clock second) | {both(lambda t: num(t['throughput'], 3))} |")
    def cost_text(t):
        extra = f" + {t['unresolved']} unresolved" if t["unresolved"] else ""
        return money(t["api_cost"]) + extra
    add(f"| API cost (incremental for warm run) | {both(cost_text)} |")
    add(f"| API cost per 1,000 input rows | {both(lambda t: money(t['per_1000_inputs'], 4))} |")
    add(f"| API cost per completed record | {both(lambda t: money(t['per_completed'], 6))} |")
    add("")

    add("### Local compute (separate from API spend)\n")
    add("Ingest, cache lookup, validation, ranking and export run on this laptop. Their cost (hardware, electricity) "
        "is **unknown / not measured** - it is not zero. ")
    if args.local_usd_per_hour is not None:
        hrs = (timing["cold"]["e2e"] or {}).get("seconds")
        est = None if hrs is None else hrs / 3600 * args.local_usd_per_hour
        add(f"Labelled estimate at an assumed ${args.local_usd_per_hour}/hour of laptop time: pilot cold run {money(est, 6)}.\n")
    else:
        add("Pass `--local-usd-per-hour X` to show a labelled estimate.\n")

    # ---------------- projection ----------------
    sc, pi = proj["scope"], proj["inputs"]
    add("## 2. Estimated before the full run\n")
    add("| Scope | Value |\n|---|---|")
    add(f"| Rows accounted for | {sc['rows']:,} {'(contract)' if sc['exact'] else '(scaled from contract ratios)'} |")
    add(f"| Nonempty outputs | {sc['nonempty']:,} |")
    add(f"| Empty-text quarantines (`empty_review_text`) | {sc['empty']:,} |")
    add(f"| Distinct nonempty texts (exact-text reuse on) | {sc['distinct']:,} |")
    add(f"| No-reuse comparison (every nonempty row sent) | {sc['nonempty']:,} |\n")

    add("**Assumptions (from the cold pilot unless overridden):**\n")
    epu = pi["enrich_per_unit"]
    add(f"- enrich: {pi['enrich_units']} distinct texts classified in the pilot; measured cost per text "
        f"{money(sum(epu.values()) if epu else None, 8)} (input {money(epu and epu['input'], 9)}, cached "
        f"{money(epu and epu['cached_input'], 9)}, output {money(epu and epu['output'], 9)}). Scales with distinct texts.")
    vpu = pi["verify_per_unit"]
    add(f"- verify: rate {pi['verify_rate']:.3f} of texts (measured {pi['measured_verify_rate']:.3f}); cost per verified "
        f"review {money(sum(vpu.values()) if vpu else None, 8)}. Scales with verify-rate x texts.")
    add("- group, memo: fixed overhead, counted **once** (not multiplied by the number of rows).")
    add("- ingest, rank, export: local code, $0 API (local compute unknown).")
    add(f"- retries: measured enrich retry rate {pi['measured_retry']:.3f} (failed / succeeded attempts); base uses "
        f"{pi['base_retry']:.3f}, conservative and ceiling use {pi['cons_retry']:.3f}.")
    add(f"- fallbacks: base uses the measured fraction {pi['measured_fallback']:.3f}; conservative uses the declared "
        f"maximum {args.fallback_fraction:g} of texts re-run on {args.fallback_model} at "
        f"{money(pi['fallback_unit_cost'], 8)} per text (pilot tokens x that model's rates).")
    add(f"- ceiling: conservative plus every enriched text using the full output cap of {args.output_token_cap} tokens.")
    add(f"- rate multiplier {args.rate_multiplier:g} applies to every API price; it never changes time or local compute.\n")

    add("| Scenario | enrich | verify | group | memo | **Total with reuse** | Total without reuse | Budget |")
    add("|---|---|---|---|---|---|---|---|")
    for name, r in proj["scenarios"].items():
        s = r["reuse"]["stages"]
        tot, tot_nr = r["reuse"]["total"], r["no_reuse"]["total"]
        flag = "unknown" if tot is None else ("**OVER**" if tot > args.budget or (tot_nr or 0) > args.budget else "fits")
        add(f"| {name} | {money(s['enrich'], 2)} | {money(s['verify'], 2)} | {money(s['group'], 4)} | "
            f"{money(s['memo'], 4)} | **{money(tot, 2)}** | {money(tot_nr, 2)} | {flag} |")
    add("")

    add(f"**Time projection** (measured cold stage times; {proj['workers']} worker(s) vs {proj['pilot_workers']} in the pilot):\n")
    add("| Stage | Pilot seconds | Rule | Base | Conservative |\n|---|---|---|---|---|")
    for stage, t in proj["time"].items():
        add(f"| {stage} | {secs(t.get('measured'))} | {t['rule']} | {hours(t['base'])} | {hours(t['conservative'])} |")
    tt = proj["time_totals"]
    partial = f" + unmeasured: {', '.join(tt['missing'])}" if tt["missing"] else ""
    add(f"| **total** | | | **{hours(tt['base'])}{partial}** | **{hours(tt['conservative'])}{partial}** |\n")
    add("Worker speedup is modeled as linear and is an upper bound: rate limits, retries and contention can make more "
        "workers slower. Summed call durations are not wall-clock time.\n")

    add("## 3. Formulas and sources\n")
    add("- `uncached input = input_tokens - cached_input_tokens`; `cached input = cached_input_tokens`; "
        "`output = output_tokens` (reasoning tokens are inside output and are not added again).")
    add("- `item_cost = billed_units x price_per_unit`; `price_per_unit = price_per_million / 1,000,000`; "
        "`total = sum(item_cost)`.")
    add("- Projection: `enrich = texts x cost_per_text x (1 + retry) + texts x fallback x fallback_cost_per_text`; "
        "`verify = texts x verify_rate x cost_per_verified x (1 + retry)`; `group`/`memo` = measured once.")
    add("- Evidence: [`pilot_calls.jsonl`](pilot_calls.jsonl), [`pilot_records.jsonl`](pilot_records.jsonl), "
        "[`pilot_stages.jsonl`](pilot_stages.jsonl), [`pilot_runs.jsonl`](pilot_runs.jsonl), "
        "[`usage.csv`](usage.csv), [`rates.csv`](rates.csv).")
    return "\n".join(L) + "\n"


def hours(seconds):
    if seconds is None:
        return "unknown"
    if seconds < 120:
        return f"{seconds:,.1f} s"
    return f"{seconds / 3600:,.2f} h"


def print_summary(res, args):
    m, t, p = res["measured"], res["timing"], res["projection"]
    print("Measured 100-review pilot (offline replay)")
    print(f"  input checksum: {'MATCH' if res['input']['ok'] else 'MISMATCH'}  records: {res['status']}")
    for run in ("cold", "warm"):
        tot = m[run]["total"]
        e2e = t[run]["e2e"]
        print(f"  {run}: attempts {tot['attempts']} (failed {tot['failed']}), enrich attempts "
              f"{m[run]['roles']['enrich']['attempts']}, API cost {money(tot['cost'])}"
              f"{' + ' + str(tot['unresolved']) + ' unresolved' if tot['unresolved'] else ''}, wall clock "
              f"{secs(e2e['seconds']) if e2e else 'not recorded'}")
    print(f"Projection for {p['scope']['rows']:,} rows (rate multiplier {args.rate_multiplier:g}, "
          f"{p['workers']} worker(s)):")
    for name, r in p["scenarios"].items():
        print(f"  {name:12s} reuse {money(r['reuse']['total'], 2):>12s}   no-reuse {money(r['no_reuse']['total'], 2):>12s}")
    tt = p["time_totals"]
    print(f"  time (base / conservative): {hours(tt['base'])} / {hours(tt['conservative'])}"
          + (f"  (not measured, excluded: {', '.join(tt['missing'])})" if tt["missing"] else ""))
    for w in res["warnings"]:
        print(w)
    if not res["warnings"]:
        print(f"All scenarios fit the ${args.budget:,.2f} budget.")


def cmd_replay(args):
    res = compute(args)
    report = render_report(res, args)
    out = Path(args.out) if args.out else Path(args.cost_dir) / "report.md"
    out.write_text(report, encoding="utf-8")
    write_usage_csv(Path(args.cost_dir) / "usage.csv", res["calls"], res["rates"])
    print_summary(res, args)
    print(f"Wrote {out}")
    return res


# ----------------------------------------------------------------------------------------------
# Export: state.db -> cost/*.jsonl (offline, read-only)
# ----------------------------------------------------------------------------------------------

def table_columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def export(run_dir, out_dir, cold_run_id=None, warm_run_ids=None, rates_path=None):
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    db = run_dir / "state.db"
    if not db.exists():
        raise SystemExit(f"{db} not found")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)     # read-only: export never changes the run
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # Runs, ordered by start time.
    runs = []
    if "runs" in tables:
        for r in con.execute("SELECT * FROM runs ORDER BY started"):
            d = dict(r)
            for k in ("settings", "summary"):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else {}
                except ValueError:
                    pass
            runs.append(d)

    # Which run is cold and which warm: explicit IDs > clock windows saved by `pilot` > first run is cold.
    timing_path = run_dir / "pilot_timing.json"
    timing = json.loads(timing_path.read_text()) if timing_path.exists() else {}
    warm_run_ids = set(warm_run_ids or [])

    def label_by_time(t):
        for name in ("cold", "warm"):
            w = timing.get(name)
            if w and t is not None and w["started"] <= t <= w["ended"]:
                return name
        return None

    # Calls (missing columns, e.g. service_tier in older runs, are filled in).
    calls = []
    if "calls" in tables:
        cols = table_columns(con, "calls")
        for row in con.execute("SELECT * FROM calls ORDER BY started"):
            d = dict(row)
            c = {f: d.get(f) for f in CALL_FIELDS}
            for extra in cols:                       # keep any extra provider billing fields
                if extra not in c:
                    c[extra] = d[extra]
            if "service_tier" not in cols or not c["service_tier"]:
                c["service_tier"] = "standard"
                c["service_tier_note"] = "not logged; assumed standard"
            c["review_ids"] = review_ids_of(c)
            calls.append(c)

    # Every run ID seen (runs table or calls), ordered by when it started.
    first_seen = {}
    for r in runs:
        first_seen[r["run_id"]] = r.get("started")
    for c in calls:
        t, rid = c.get("started"), c.get("run_id")
        if rid not in first_seen or (t is not None and (first_seen[rid] is None or t < first_seen[rid])):
            first_seen[rid] = t
    ordered = sorted(first_seen, key=lambda rid: first_seen[rid] if first_seen[rid] is not None else float("inf"))
    label = {}
    for i, rid in enumerate(ordered):
        if cold_run_id or warm_run_ids:
            label[rid] = "cold" if rid == cold_run_id else ("warm" if rid in warm_run_ids else "other")
        else:
            label[rid] = label_by_time(first_seen[rid]) or ("cold" if i == 0 else "warm")
    for r in runs:
        r["cold_or_warm"] = label[r["run_id"]]
    for c in calls:
        c["cold_or_warm"] = label[c.get("run_id")]

    # Records in the grading-contract format.
    records = []
    enrich_configs = {}
    for c in calls:
        if c["role"] == "enrich" and c["outcome"] == "succeeded" and c.get("label_config"):
            enrich_configs[c["label_config"]] = enrich_configs.get(c["label_config"], 0) + 1
    preferred = max(enrich_configs, key=enrich_configs.get) if enrich_configs else None
    results = {}
    if "results" in tables:
        for r in con.execute("SELECT * FROM results"):
            r = dict(r)
            current = results.get(r["text_sha"])
            if current is None or r["label_config"] == preferred:
                results[r["text_sha"]] = r
    failures = {}
    if "failures" in tables:
        for r in con.execute("SELECT review_id, reason FROM failures"):
            failures[r["review_id"]] = r["reason"]
    if "reviews" in tables:
        for r in con.execute("SELECT * FROM reviews ORDER BY row_idx"):
            r = dict(r)
            rec = {"review_id": r["review_id"], "source_sha256": r["source_sha256"]}
            res = results.get(r["text_sha"])
            if r["status"] == "completed" and res:
                try:
                    entities = json.loads(res["entities"]) if res["entities"] else []
                except ValueError:
                    entities = []
                rec.update({"status": "completed", "topic": res["topic"], "intent": res["intent"],
                            "sentiment": res["sentiment"], "severity": res["severity"], "entities": entities,
                            "evidence_quote": res["evidence_quote"], "needs_review": bool(res["needs_review"]),
                            "label_config": res["label_config"]})
                if res["origin_id"] != r["review_id"]:
                    rec["cache_source_id"] = res["origin_id"]
            elif r["status"] == "quarantined":
                rec.update({"status": "quarantined", "reason": r.get("reason") or "quarantined"})
            else:
                rec.update({"status": "failed", "reason": failures.get(r["review_id"]) or f"status_{r['status']}"})
            records.append(rec)

    # Stage times: the stages table where it has rows for a run; otherwise derived from call timestamps.
    stages = []
    if "stages" in tables:
        for r in con.execute("SELECT * FROM stages ORDER BY started"):
            d = dict(r)
            d["cold_or_warm"] = label.get(d["run_id"]) or label_by_time(d.get("started")) or "other"
            d["source"] = "stages table"
            stages.append(d)
    runs_with_stages = {s["run_id"] for s in stages}
    spans = {}
    for c in calls:
        if c["run_id"] in runs_with_stages or c.get("started") is None or c.get("ended") is None:
            continue
        s = spans.setdefault((c["run_id"], c["role"]), [c["started"], c["ended"], c["cold_or_warm"]])
        s[0], s[1] = min(s[0], c["started"]), max(s[1], c["ended"])
    for (rid, role), (a, b, cw) in spans.items():
        stages.append({"run_id": rid, "stage": role, "started": a, "ended": b, "items_in": None,
                       "items_out": None, "notes": "no stage timing logged for this run", "cold_or_warm": cw,
                       "source": "derived from first/last call timestamps"})
    # End-to-end clock: the pilot command's own clock, else the runs table.
    for name in ("cold", "warm"):
        if timing.get(name):
            w = timing[name]
            stages.append({"run_id": None, "stage": "end_to_end", "started": w["started"], "ended": w["ended"],
                           "cold_or_warm": name, "exit_code": w.get("exit_code"),
                           "source": "clock around the pipeline command (calculator pilot)"})
        else:
            mine = [r for r in runs if r["cold_or_warm"] == name and r.get("started") and r.get("ended")]
            if mine:
                stages.append({"run_id": ",".join(r["run_id"] for r in mine), "stage": "end_to_end",
                               "started": min(r["started"] for r in mine), "ended": max(r["ended"] for r in mine),
                               "cold_or_warm": name, "source": "runs table start/end"})
    con.close()

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "pilot_calls.jsonl", calls)
    write_jsonl(out_dir / "pilot_records.jsonl", records)
    write_jsonl(out_dir / "pilot_stages.jsonl", stages)
    write_jsonl(out_dir / "pilot_runs.jsonl", runs)
    write_usage_csv(out_dir / "usage.csv", calls, load_rates(rates_path or COST_DIR / "rates.csv"))
    counts = {k: sum(1 for c in calls if c["cold_or_warm"] == k) for k in ("cold", "warm", "other")}
    print(f"Exported from {db}: {len(calls)} call attempts {counts}, {len(records)} records, "
          f"{len(stages)} stage rows, {len(runs)} runs -> {out_dir}")
    for r in runs:
        print(f"  run {r['run_id']}: {r['cold_or_warm']}")
    return {"calls": calls, "records": records, "stages": stages, "runs": runs}


# ----------------------------------------------------------------------------------------------
# Pilot: the only paid path
# ----------------------------------------------------------------------------------------------

def cmd_pilot(args):
    run_dir = ROOT / args.run_dir
    csv_path = ROOT / args.input_csv
    cmd = [sys.executable, "-m", "pipeline.run", str(args.input_csv), "--run-dir", str(args.run_dir),
           "--provider", "openai", "--workers", str(args.workers), "--budget", str(args.budget),
           "--verify-rate", str(args.verify_rate)]
    if args.service_tier:
        cmd += ["--service-tier", args.service_tier]
    print("PAID PILOT PLAN (real OpenAI calls, billed to your account):")
    print(f"  1. cold run: python3 {' '.join(cmd[1:])}   (fresh {args.run_dir}, empty result cache)")
    print(f"  2. warm run: the same command again on the same run dir (should make zero new enrich calls)")
    print(f"  3. export:   offline, writes cost/pilot_*.jsonl and cost/usage.csv")
    print(f"  Hard budget across both runs: ${args.budget:.2f} (enforced by the pipeline's spend ledger)")
    if not args.yes:
        print("Nothing was run. Re-run with --yes to spend money and execute this plan.")
        return
    info = input_check(csv_path, ROOT / "data" / "manifest.json")
    if not info["ok"]:
        raise SystemExit(f"Refusing: {args.input_csv} checksum {info['sha256']} does not match manifest {info['expected']}")
    if run_dir.exists():
        raise SystemExit(f"Refusing: {run_dir} already exists. A cold run needs a fresh run dir; move it aside first.")
    timing = {}
    for name in ("cold", "warm"):
        print(f"--- {name} run ---", flush=True)
        started = time.time()
        t0 = time.perf_counter()
        code = subprocess.run(cmd, cwd=ROOT).returncode
        seconds = time.perf_counter() - t0
        timing[name] = {"started": started, "ended": started + seconds, "seconds": seconds, "exit_code": code}
        print(f"{name} run finished in {seconds:.1f} s (exit code {code})")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pilot_timing.json").write_text(json.dumps(timing, indent=2))
        if code != 0:
            print(f"{name} run failed; exporting what was saved so every attempt is accounted for.")
            break
    export(run_dir, COST_DIR)
    print("Done. Now run the offline replay: python3 cost/calculator.py")


# ----------------------------------------------------------------------------------------------
# Command line
# ----------------------------------------------------------------------------------------------

def add_replay_args(p):
    p.add_argument("--budget", type=float, default=10.0, help="USD budget the full-run scenarios must fit (default 10)")
    p.add_argument("--max-workers", type=int, default=None, help="workers for the time projection (default: pilot's)")
    p.add_argument("--fallback-fraction", type=float, default=0.02,
                   help="declared max fraction of texts re-run on the fallback model (conservative case, default 0.02)")
    p.add_argument("--fallback-model", default="claude-sonnet-5", help="fallback model priced in rates.csv")
    p.add_argument("--output-token-cap", type=int, default=60, help="output tokens per review cap (ceiling case)")
    p.add_argument("--retry-rate", type=float, default=None, help="base retry overhead (default: measured)")
    p.add_argument("--verify-rate", type=float, default=None, help="fraction of texts verified (default: measured)")
    p.add_argument("--rate-multiplier", type=float, default=1.0, help="multiply every API price (2 = double)")
    p.add_argument("--projected-rows", type=int, default=FULL_ROWS, help=f"rows in the full run (default {FULL_ROWS:,})")
    p.add_argument("--local-usd-per-hour", type=float, default=None, help="optional labelled local-compute estimate")
    p.add_argument("--cost-dir", default=str(COST_DIR), help="folder with pilot_*.jsonl (default cost/)")
    p.add_argument("--rates", default=str(COST_DIR / "rates.csv"))
    p.add_argument("--input-csv", default=str(ROOT / "data" / "cost_100.csv"))
    p.add_argument("--manifest", default=str(ROOT / "data" / "manifest.json"))
    p.add_argument("--out", default=None, help="report path (default <cost-dir>/report.md)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("replay", "export", "pilot"):
        argv = ["replay"] + argv
    ap = argparse.ArgumentParser(description="100-review cost/runtime calculator (offline replay by default)")
    sub = ap.add_subparsers(dest="command")
    add_replay_args(sub.add_parser("replay", help="offline replay (default)"))
    ex = sub.add_parser("export", help="offline: copy evidence from a run dir's state.db into cost/")
    ex.add_argument("--run-dir", required=True)
    ex.add_argument("--cold-run-id")
    ex.add_argument("--warm-run-id", action="append", help="repeatable")
    ex.add_argument("--out-dir", default=str(COST_DIR))
    pi = sub.add_parser("pilot", help="PAID: run the real pipeline cold + warm on data/cost_100.csv, then export")
    pi.add_argument("--budget", type=float, required=True, help="hard USD cap across both runs, e.g. 0.25")
    pi.add_argument("--run-dir", default="runs/pilot")
    pi.add_argument("--input-csv", default="data/cost_100.csv")
    pi.add_argument("--workers", type=int, default=1)
    pi.add_argument("--verify-rate", type=float, default=0.1)
    pi.add_argument("--service-tier", choices=["flex", "standard"], default=None)
    pi.add_argument("--yes", action="store_true", help="actually spend money")
    args = ap.parse_args(argv)
    if args.command == "export":
        export(args.run_dir, args.out_dir, args.cold_run_id, args.warm_run_id)
    elif args.command == "pilot":
        cmd_pilot(args)
    else:
        cmd_replay(args)


if __name__ == "__main__":
    main()
