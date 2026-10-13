# Full-run estimate refresh: cp500 + cp10k_v4a

Generated offline by `python3 cost/calculator.py refresh` from each checkpoint's saved `state.db` (no API calls). Costs are recomputed from usage x [`rates.csv`](rates.csv).

**Controls:** budget $10.00 | max workers checkpoint run | planned verify rate 0.01 | rate multiplier 1 | max fallback 0.02 (claude-sonnet-5) | projected rows 660,622 (484,189 distinct texts)

> cp500: WARNING: base/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: base/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: conservative/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: conservative/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: ceiling/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: ceiling/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k_v4a: WARNING: conservative scenario (no-reuse) projects $10.40, which EXCEEDS the $10.00 budget
> cp10k_v4a: WARNING: ceiling scenario (reuse) projects $12.82, which EXCEEDS the $10.00 budget
> cp10k_v4a: WARNING: ceiling scenario (no-reuse) projects $17.49, which EXCEEDS the $10.00 budget

## Is the estimate converging?

| Checkpoint | Enrich config / tier | Workers | Reviews | Texts enriched | Enrich $ per 1k texts | Throughput (texts/s, wall clock) | Invalid first pass | Retry rate | Verify $ per review | Projected enrich+verify (base) | Projected full run: base | conservative |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cp500 | gpt-6-luna+effort-none+enrich-v2+schema-v1 / standard | 1 | 500 | 479 | $0.0207 | 4.07 | 0 (0.0%) | 0.000 | $0.000083 | $10.40 | unknown | unknown |
| cp10k_v4a | gpt-6-luna+effort-none+enrich-v4+schema-v3 / flex | 12 | 10,000 | 8,448 | $0.0070 | 26.39 | 452 (5.4%) | 0.008 | $0.000083 | $3.81 | $3.81 | $7.62 |

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

## cp10k_v4a: projection for 660,622 rows

- Records: 9,991 completed, 9 quarantined, 0 failed.
- Enrich: 262 attempts (2 failed, 2 with unknown usage), $0.058949 for 8,448 texts; wall time 320.1 s (stages table).
- Verify: checkpoint verified 2.0% of texts; full run plans 1.0%.
- Group (fixed): $0.003094; memo (fixed): $0.000615.

| Scenario | enrich | verify | group | memo | **Total with reuse** | Total without reuse | Budget |
|---|---|---|---|---|---|---|---|
| base | $3.40 | $0.41 | $0.0031 | $0.0006 | **$3.81** | $5.20 | fits |
| conservative | $7.18 | $0.44 | $0.0034 | $0.0007 | **$7.62** | $10.40 | **OVER** |
| ceiling | $12.38 | $0.44 | $0.0034 | $0.0007 | **$12.82** | $17.49 | **OVER** |

**Time projection** (measured stage wall times; 12 worker(s) vs 12 in the checkpoint run):

| Stage | Measured seconds | Rule | Base | Conservative |
|---|---|---|---|---|
| ingest | 0.2 s | scales with input rows (local code) | 14.1 s | 14.1 s |
| enrich | 320.1 s | scales with distinct texts, / 1x workers (modeled linear speedup) | 5.10 h | 5.56 h |
| verify | 3.7 s | scales with verify-rate x texts, / 1x workers (modeled) | 105.4 s | 115.1 s |
| group | 71.3 s | fixed overhead, counted once | 71.3 s | 71.3 s |
| rank | unknown | not measured | unknown | unknown |
| memo | 5.8 s | fixed overhead, counted once | 5.8 s | 5.8 s |
| export | 0.6 s | scales with input rows (local code) | 38.7 s | 38.7 s |
| **total** | | | **5.16 h + unmeasured: rank** | **5.63 h + unmeasured: rank** |

Worker speedup is modeled as linear and is an upper bound: rate limits, retries and contention can make more workers slower. Summed call durations are not wall-clock time.

