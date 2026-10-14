# Decision memo: where should next quarter's product effort go?

*Source: 660,622 Google Play reviews (May 2022 – Nov 2023); 660,603 classified, 19 quarantined, 0 unprocessed. Written by the memo-writer role (gpt-6-luna) from saved aggregates only; every number and ID below was checked by code (validation: passed).*

**Recommendation: billing/support — lead issue `billing.free_tier_limits` (Free Tier Playback Restrictions)**

**Prioritize billing/support, led by Free Tier Playback Restrictions.** This is not the full-window severity leader: usability ranks first with a severity sum of 197874 [A2s], while billing/support ranks second with 171603 [A4s]. But the lead issue is the largest specific actionable signal in both the full-window claims and the recent quarter: 41518 complaints [C5] overall and 24198 complaints [R1c], representing 23.9% of recent complaints [R1p]. Its share of complaints increased from 8.4% [T2a] in the earlier comparable period to 16.8% [T2b] in the later one. The quotes describe restrictions on shuffle and other playback controls (e7747485-42c5-4bb9-b1e2-1b4044eedb5a; 9b42b7f8-2477-4516-b2de-6d46831fbad9). Addressing those limits is not purely a product-quality fix: it requires a pricing/business-model decision about what free listeners can do.

The complaint surge is broad: complaint share of all reviews rose from 30.6% [T0a] to 59.0% [T0b] in the comparable periods. General dissatisfaction and generic update complaints grew sharply, to 33.4% [T4b] and 6.4% [T5b] of complaints, respectively; neither identifies a specific fix, so do not make them the lead. Free-tier restrictions and premium pressure also grew, while ads fell from 20.3% [T1a] to 8.8% [T1b]. Usability remains the strongest full-window alternative, with 79177 complaint reviews [A2c], including 37359 ads complaints [C9], but ads are a shrinking share. Playback is another alternative: 43381 complaint reviews [A3c], and crashes fell from 6.2% [T6a] to 1.1% [T6b].

Treat public reviews as self-selected evidence; cancellation language is stated intent, not observed churn. Recent cancellation-intent reviews total 14101 [R0k]. The late-period deterioration is not proof of causality, and broad dissatisfaction/update feedback limits diagnosis.

## Alternatives considered

- **`usability.ads`** (Too Many Ads): Usability ranks first by full-window severity, but ads' comparable-period share fell from 20.3% [T1a] to 8.8% [T1b], while free-tier restrictions grew.
- **`playback.stops_pauses`** (Music Stops or Pauses Unexpectedly): Playback has fewer full-window complaint reviews than usability or billing/support, and the supplied recency comparison shows crashes shrinking; it does not establish a current rise in stops or pauses.

## Limitations

- Reviews are self-selected public feedback and may not represent all listeners.
- Cancellation language is stated intent, not observed churn.
- The recent-period aggregate identifies trends but does not establish their causes; generic dissatisfaction and update complaints do not specify a consistent product problem.
- Free-tier restrictions require a pricing/business-model decision, not solely product implementation.

## Area totals cited as [A#c] / [A#s] (code-computed, `analysis/aggregates.csv`)

| Area | Topics | Complaint + cancellation reviews | Severity sum |
|---|---|---|---|
| access | access | 9,666 [A1c] | 37,494 [A1s] |
| usability | usability | 79,177 [A2c] | 197,874 [A2s] |
| playback | playback, downloads | 43,381 [A3c] | 145,242 [A3s] |
| billing_support | billing, support | 61,298 [A4c] | 171,603 [A4s] |

## Recent quarter 2023-08..2023-10 cited as [R#] (code-computed, `analysis/recent_quarter.json`)

- Complaint + cancellation memberships: 101,420 [R0c]; complaint share of all reviews 62.9% [R0p]; cancellation-intent reviews 14,101 [R0k]

| Issue | Complaints | Share of recent complaints |
|---|---|---|
| `billing.free_tier_limits` | 24,198 [R1c] | 23.9% [R1p] |
| `other.general` | 17,572 [R2c] | 17.3% [R2p] |
| `other.bad_update_generic` | 9,946 [R3c] | 9.8% [R3p] |
| `usability.ads` | 9,179 [R4c] | 9.1% [R4p] |
| `billing.premium_pressure` | 6,626 [R5c] | 6.5% [R5p] |
| `usability.controls_navigation` | 4,031 [R6c] | 4.0% [R6p] |

## Comparable periods 2022-06..2022-10 vs 2023-06..2023-10 cited as [T#]

| Measure | 2022 | 2023 |
|---|---|---|
| Complaint share of all reviews | 30.6% [T0a] | 59.0% [T0b] |
| `usability.ads` share of complaints | 20.3% [T1a] | 8.8% [T1b] |
| `billing.free_tier_limits` share of complaints | 8.4% [T2a] | 16.8% [T2b] |
| `billing.premium_pressure` share of complaints | 0.8% [T3a] | 4.4% [T3b] |
| `other.general` share of complaints | 8.1% [T4a] | 33.4% [T4b] |
| `other.bad_update_generic` share of complaints | 0.8% [T5a] | 6.4% [T5b] |
| `playback.crashes_wont_open` share of complaints | 6.2% [T6a] | 1.1% [T6b] |

## Issue claims cited as [C#] (`grading/claims.csv`, recomputed by the checker)

| Claim | Issue | Metric | Value |
|---|---|---|---|
| C1 | `other.general` General App Dissatisfaction | complaint_count | 69553 |
| C2 | `other.general` General App Dissatisfaction | severity_sum | 137583 |
| C3 | `other.general` General App Dissatisfaction | mean_severity | 1.978103 |
| C4 | `other.general` General App Dissatisfaction | priority_score | 137583 |
| C5 | `billing.free_tier_limits` Free Tier Playback Restrictions | complaint_count | 41518 |
| C6 | `billing.free_tier_limits` Free Tier Playback Restrictions | severity_sum | 122942 |
| C7 | `billing.free_tier_limits` Free Tier Playback Restrictions | mean_severity | 2.961173 |
| C8 | `billing.free_tier_limits` Free Tier Playback Restrictions | priority_score | 122942 |
| C9 | `usability.ads` Too Many Ads | complaint_count | 37359 |
| C10 | `usability.ads` Too Many Ads | severity_sum | 80418 |
| C11 | `usability.ads` Too Many Ads | mean_severity | 2.152574 |
| C12 | `usability.ads` Too Many Ads | priority_score | 80418 |
| C13 | `access.login_failure` Unable to Log In | complaint_count | 8493 |
| C14 | `access.login_failure` Unable to Log In | severity_sum | 32975 |
| C15 | `access.login_failure` Unable to Log In | mean_severity | 3.882609 |
| C16 | `access.login_failure` Unable to Log In | priority_score | 32975 |
| C17 | `playback.stops_pauses` Music Stops or Pauses Unexpectedly | complaint_count | 9898 |
| C18 | `playback.stops_pauses` Music Stops or Pauses Unexpectedly | severity_sum | 31943 |
| C19 | `playback.stops_pauses` Music Stops or Pauses Unexpectedly | mean_severity | 3.227218 |
| C20 | `playback.stops_pauses` Music Stops or Pauses Unexpectedly | priority_score | 31943 |
| C21 | `usability.controls_navigation` Missing or Broken Playback Controls | complaint_count | 10825 |
| C22 | `usability.controls_navigation` Missing or Broken Playback Controls | severity_sum | 31268 |
| C23 | `usability.controls_navigation` Missing or Broken Playback Controls | mean_severity | 2.888499 |
| C24 | `usability.controls_navigation` Missing or Broken Playback Controls | priority_score | 31268 |
| C25 | `usability.forced_shuffle` Unwanted Shuffle and Song Choices | complaint_count | 10400 |
| C26 | `usability.forced_shuffle` Unwanted Shuffle and Song Choices | severity_sum | 31052 |
| C27 | `usability.forced_shuffle` Unwanted Shuffle and Song Choices | mean_severity | 2.985769 |
| C28 | `usability.forced_shuffle` Unwanted Shuffle and Song Choices | priority_score | 31052 |
| C29 | `other.bad_update_generic` Disappointment with Recent Updates | complaint_count | 14108 |
| C30 | `other.bad_update_generic` Disappointment with Recent Updates | severity_sum | 28127 |
| C31 | `other.bad_update_generic` Disappointment with Recent Updates | mean_severity | 1.993692 |
| C32 | `other.bad_update_generic` Disappointment with Recent Updates | priority_score | 28127 |

## Evidence quotes cited

- `e7747485-42c5-4bb9-b1e2-1b4044eedb5a` (billing.free_tier_limits, severity 3): "wouldnt even let us shuffle"
- `9b42b7f8-2477-4516-b2de-6d46831fbad9` (billing.free_tier_limits, severity 3): "limiting it to premium users"

*Reviews are self-selected public feedback. Cancellation language is stated intent, not observed churn. No revenue, plan tier or retention data exists in this dataset, so no revenue or churn impact is claimed.*
