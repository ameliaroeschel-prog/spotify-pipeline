# Full-run estimate refresh: cp500 + cp10k

Generated offline by `python3 cost/calculator.py refresh` from each checkpoint's saved `state.db` (no API calls). Costs are recomputed from usage x [`rates.csv`](rates.csv).

**Controls:** budget $10.00 | max workers checkpoint run | planned verify rate 0.01 | rate multiplier 1 | max fallback 0.02 (claude-sonnet-5) | projected rows 660,622 (484,189 distinct texts)

> cp500: WARNING: base/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: base/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: conservative/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: conservative/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: ceiling/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: ceiling/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k (in progress): WARNING: base/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k (in progress): WARNING: base/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k (in progress): WARNING: conservative/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k (in progress): WARNING: conservative/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k (in progress): WARNING: ceiling/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k (in progress): WARNING: ceiling/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget

## Is the estimate converging?

| Checkpoint | Enrich config / tier | Workers | Reviews | Texts enriched | Enrich $ per 1k texts | Throughput (texts/s, wall clock) | Invalid first pass | Retry rate | Verify $ per review | Projected enrich+verify (base) | Projected full run: base | conservative |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cp500 | gpt-6-luna+effort-none+enrich-v2+schema-v1 / standard | 1 | 500 | 479 | $0.0207 | 4.07 | 0 (0.0%) | 0.000 | $0.000083 | $10.40 | unknown | unknown |
| cp10k (in progress) | gpt-6-luna+effort-none+enrich-v4+schema-v3 / flex | None | 10,000 | 2,800 | $0.0054 | 33.03 | 0 (0.0%) | 0.000 | unknown | unknown | unknown | unknown |

Rows use different configurations when the label_config or tier differs, so they are not strictly comparable: a change in prompt/schema version or flex vs standard pricing moves the cost per text. `unknown` means a stage (often group/memo) was not run in that checkpoint, so its cost is not counted as $0. Throughput is texts / enrich-stage wall-clock seconds at that run's worker count.

## cp500: projection for 660,622 rows

- Records: 500 completed, 0 quarantined, 0 failed.
- Enrich: 10 attempts (0 failed, 0 with unknown usage), $0.009893 for 479 texts; wall time 117.7 s (derived from first/last call timestamps).
- Verify: checkpoint verified 10.0% of texts; full run plans 1.0%.
- Group (fixed): unknown; memo (fixed): unknown.

| Scenario | enrich | verify | group | memo | **Total with reuse** | Total without reuse | Budget |
|---|---|---|---|---|---|---|---|
| base | $10.00 | $0.40 | unknown | unknown | **unknown** | unknown | unknown |
| conservative | $15.39 | $0.44 | unknown | unknown | **unknown** | unknown | unknown |
| ceiling | $21.98 | $0.44 | unknown | unknown | **unknown** | unknown | unknown |

**Time projection** (measured stage wall times; 1 worker(s) vs 1 in the checkpoint run):

| Stage | Measured seconds | Rule | Base | Conservative |
|---|---|---|---|---|
| ingest | unknown | not measured | unknown | unknown |
| enrich | 117.7 s | scales with distinct texts, / 1x workers (modeled linear speedup) | 33.05 h | 36.35 h |
| verify | 2.5 s | scales with verify-rate x texts, / 1x workers (modeled) | 0.07 h | 0.08 h |
| group | unknown | not measured | unknown | unknown |
| rank | unknown | not measured | unknown | unknown |
| memo | unknown | not measured | unknown | unknown |
| export | unknown | not measured | unknown | unknown |
| **total** | | | **33.12 h + unmeasured: ingest, group, rank, memo, export** | **36.43 h + unmeasured: ingest, group, rank, memo, export** |

Worker speedup is modeled as linear and is an upper bound: rate limits, retries and contention can make more workers slower. Summed call durations are not wall-clock time.

## cp10k (in progress): projection for 660,622 rows

- Records: 2,871 completed, 0 quarantined, 7,129 failed.
- Enrich: 56 attempts (0 failed, 0 with unknown usage), $0.015238 for 2,800 texts; wall time 84.8 s (derived from first/last call timestamps).
- Verify: checkpoint verified 0.0% of texts; full run plans 1.0%.
- Group (fixed): unknown; memo (fixed): unknown.

| Scenario | enrich | verify | group | memo | **Total with reuse** | Total without reuse | Budget |
|---|---|---|---|---|---|---|---|
| base | $2.64 | unknown | unknown | unknown | **unknown** | unknown | unknown |
| conservative | $5.99 | unknown | unknown | unknown | **unknown** | unknown | unknown |
| ceiling | $11.24 | unknown | unknown | unknown | **unknown** | unknown | unknown |

**Time projection** (measured stage wall times; 1 worker(s) vs 1 in the checkpoint run):

| Stage | Measured seconds | Rule | Base | Conservative |
|---|---|---|---|---|
| ingest | 0.2 s | scales with input rows (local code) | 12.7 s | 12.7 s |
| enrich | 84.8 s | scales with distinct texts, / 1x workers (modeled linear speedup) | 4.07 h | 4.48 h |
| verify | unknown | not measured | unknown | unknown |
| group | unknown | not measured | unknown | unknown |
| rank | unknown | not measured | unknown | unknown |
| memo | unknown | not measured | unknown | unknown |
| export | unknown | not measured | unknown | unknown |
| **total** | | | **4.08 h + unmeasured: verify, group, rank, memo, export** | **4.48 h + unmeasured: verify, group, rank, memo, export** |

Worker speedup is modeled as linear and is an upper bound: rate limits, retries and contention can make more workers slower. Summed call durations are not wall-clock time.

