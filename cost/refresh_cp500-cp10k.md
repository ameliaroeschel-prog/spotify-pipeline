# Full-run estimate refresh: cp500-cp10k

Generated offline by `python3 cost/calculator.py refresh` from each checkpoint's saved `state.db` (no API calls). Costs are recomputed from usage x [`rates.csv`](rates.csv).

**Controls:** budget $10.00 | max workers checkpoint run | planned verify rate 0.01 | rate multiplier 1 | max fallback 0.02 (claude-sonnet-5) | projected rows 660,622 (484,189 distinct texts)

> cp500: WARNING: base/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: base/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: conservative/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: conservative/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: ceiling/reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp500: WARNING: ceiling/no_reuse total is UNKNOWN (a stage was not measured or has unresolved usage), cannot confirm it fits the $10.00 budget
> cp10k: WARNING: ceiling scenario (reuse) projects $12.35, which EXCEEDS the $10.00 budget
> cp10k: WARNING: ceiling scenario (no-reuse) projects $16.85, which EXCEEDS the $10.00 budget

## Is the estimate converging?

| Checkpoint | Enrich config / tier | Workers | Reviews | Texts enriched | Enrich $ per 1k texts | Throughput (texts/s, wall clock) | Invalid first pass | Retry rate | Verify $ per review | Projected enrich+verify (base) | Projected full run: base | conservative |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| cp500 | gpt-6-luna+effort-none+enrich-v2+schema-v1 / standard | 1 | 500 | 479 | $0.0207 | 4.07 | 0 (0.0%) | 0.000 | $0.000083 | $10.40 | unknown | unknown |
| cp10k | gpt-6-luna+effort-none+enrich-v4+schema-v3 / flex | 12 | 10,000 | 8,448 | $0.0060 | 33.29 | 601 (7.1%) | 0.003 | $0.000083 | $3.30 | $3.31 | $7.29 |

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

## cp10k: projection for 660,622 rows

- Records: 9,996 completed, 4 quarantined, 0 failed.
- Enrich: 290 attempts (1 failed, 1 with unknown usage), $0.050425 for 8,448 texts; wall time 253.8 s (stages table).
- Verify: checkpoint verified 2.0% of texts; full run plans 1.0%.
- Group (fixed): $0.003094; memo (fixed): $0.000664.

| Scenario | enrich | verify | group | memo | **Total with reuse** | Total without reuse | Budget |
|---|---|---|---|---|---|---|---|
| base | $2.90 | $0.40 | $0.0031 | $0.0007 | **$3.31** | $4.51 | fits |
| conservative | $6.84 | $0.44 | $0.0034 | $0.0007 | **$7.29** | $9.94 | fits |
| ceiling | $11.90 | $0.44 | $0.0034 | $0.0007 | **$12.35** | $16.85 | **OVER** |

**Time projection** (measured stage wall times; 12 worker(s) vs 12 in the checkpoint run):

| Stage | Measured seconds | Rule | Base | Conservative |
|---|---|---|---|---|
| ingest | 0.2 s | scales with input rows (local code) | 12.7 s | 12.7 s |
| enrich | 253.8 s | scales with distinct texts, / 1x workers (modeled linear speedup) | 4.04 h | 4.43 h |
| verify | 3.8 s | scales with verify-rate x texts, / 1x workers (modeled) | 107.9 s | 118.3 s |
| group | 69.0 s | fixed overhead, counted once | 69.0 s | 69.0 s |
| rank | unknown | not measured | unknown | unknown |
| memo | 7.6 s | fixed overhead, counted once | 7.6 s | 7.6 s |
| export | 0.6 s | scales with input rows (local code) | 37.7 s | 37.7 s |
| **total** | | | **4.11 h + unmeasured: rank** | **4.50 h + unmeasured: rank** |

Worker speedup is modeled as linear and is an upper bound: rate limits, retries and contention can make more workers slower. Summed call durations are not wall-clock time.

