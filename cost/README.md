# Cost and runtime calculator (100-review pilot)

This folder answers two questions: **what did it actually cost and how long did it take to run the
pipeline on the 100 reviews in `data/cost_100.csv`**, and **what will the full 660,622-review run cost
and take**? It is plain Python (standard library only) and needs no API key to check.

## The three commands

| What | Command | Costs money? |
|---|---|---|
| **Offline replay (default)** - recompute every number from saved evidence and write `report.md` | `python3 cost/calculator.py` | No. No key, no network. |
| **Export** - copy saved evidence from a run folder's `state.db` into this folder | `python3 cost/calculator.py export --run-dir runs/pilot` | No. Reads the database read-only. |
| **Explicit paid pilot** - run the real pipeline cold, then warm, then export | `python3 cost/calculator.py pilot --budget 0.25 --yes` | **Yes** (capped at `--budget` across both runs) |

Without `--yes` the pilot command only prints its plan. Opening or importing `calculator.py` never
starts paid calls. The pilot refuses to run if the input checksum does not match
`data/manifest.json` or if `runs/pilot` already exists (a cold run needs an empty result cache).

What the pilot does, step by step:

1. **Cold run**: `python3 -m pipeline.run data/cost_100.csv --run-dir runs/pilot --provider openai --workers 1 --budget 0.25 --verify-rate 0.1` with a stopwatch around it.
2. **Warm run**: the same command again on the same folder. The saved result cache should mean **zero new enrichment calls**.
3. **Export**: writes the evidence files below. The two stopwatch readings are saved in `runs/pilot/pilot_timing.json`.

Export decides which run is "cold" and which is "warm" from those stopwatch windows. Without them, the
first run is cold and later runs are warm. To choose by hand: `--cold-run-id X --warm-run-id Y`.

## Changing the assumptions (offline)

Every control is a flag on the replay command. Changing them only changes the **projection**; the
measured 100-review numbers stay exactly the same.

| Flag | Default | Meaning |
|---|---|---|
| `--budget` | 10 | USD the full run must fit. Any scenario above it prints a `WARNING:` line. |
| `--max-workers` | the pilot's (1) | Parallel workers assumed for the time estimate (modeled linear speedup = best case). |
| `--fallback-fraction` | 0.02 | Declared maximum share of texts re-run on a stronger model (conservative case). |
| `--fallback-model` | claude-sonnet-5 | Which `rates.csv` model prices the fallback. |
| `--output-token-cap` | 60 | Output tokens per review; the "ceiling" case assumes every review uses all of it. |
| `--retry-rate` | measured | Extra attempts per successful one (0.1 = 10% more calls). |
| `--verify-rate` | measured | Share of texts sent to the verify stage. |
| `--rate-multiplier` | 1 | Multiplies every API price. `2` doubles API spend; time and local compute do not change. |
| `--projected-rows` | 660,622 | Size of the full run. |
| `--local-usd-per-hour` | none | Optional, labelled estimate for laptop time. Without it, local compute shows as unknown. |
| `--out` | `cost/report.md` | Write the report somewhere else (handy for experiments). |

Example: `python3 cost/calculator.py --rate-multiplier 2 --max-workers 2 --out /tmp/report_2x.md`

## How the money is calculated

Each API call reports token counts. The calculator splits them into three **separate** billing items, so
nothing is counted twice:

- **input** (uncached) = `input_tokens - cached_input_tokens`
- **cached_input** = `cached_input_tokens` (the provider's own prompt cache, which is cheaper)
- **output** = `output_tokens` (reasoning tokens are already inside this number)

Then `item_cost = billed_units x price_per_unit` and `total = sum of item costs`. Prices come from
`rates.csv` (price per token = price per million / 1,000,000). The verify stage uses OpenAI's Decisions
API, which charges for input only. Calls whose usage was not recorded (for example a timeout) are shown
as **unresolved**, never as $0.

**Projection:** each stage grows with its own kind of work. Enrichment grows with distinct review
texts (484,189 with exact-text reuse, 660,609 without). Verify grows with verify rate x texts.
Grouping and the memo are done once, so their cost is added once. There are three scenarios: **base**
(measured retry and fallback rates), **conservative** (higher retries and the declared maximum fallback
share) and **ceiling** (conservative, with every review also using its full output-token cap).

**API spend vs local compute:** ingest, caching, ranking and export run on the laptop. They have no API
bill, but they still use hardware and electricity. That cost is reported as **unknown**, not zero.

## Files

| File | What it is |
|---|---|
| `calculator.py` | The calculator (replay / export / pilot). |
| `rates.csv` | Prices in their billing units, with source links and the date they were checked. Flex prices are derived (50% of standard) and should be re-checked. |
| `pilot_calls.jsonl` | One line per API attempt (including failures and retries) for the cold and warm runs: stage, model, tier, tokens, timing, run ID, request ID. No keys. |
| `pilot_records.jsonl` | One result per pilot review ID in the grading-contract format. |
| `pilot_stages.jsonl` | Stage start/end times plus the end-to-end stopwatch for each run. |
| `pilot_runs.jsonl` | Run settings (provider, effort, batch size, workers, budget). |
| `usage.csv` | Each attempt split into billing items and matched to a `rates.csv` row. |
| `report.md` | The measured table and the full-run estimate. Written by the replay. |
| `tests/` | A **synthetic** fixture (invented numbers) and offline tests: `python3 -m unittest discover -s cost/tests -v` |
