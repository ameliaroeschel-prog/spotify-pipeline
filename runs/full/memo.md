# Decision memo: where should next quarter's product effort go?

*Source: 660,622 Google Play reviews (May 2022 – Nov 2023); 660,603 classified, 19 quarantined, 0 unprocessed. Written by the memo-writer role (gpt-6-luna) from saved aggregates only; every number and ID below was checked by code (validation: passed).*

**Recommendation: billing (pricing and free-tier design; the brief's billing/support area) — lead issue `billing.free_tier_limits` (Free Tier Playback Restrictions)**

**Bottom line:** Prioritize billing: pricing and free-tier design, led by Free Tier Playback Restrictions; next quarter’s work should redesign the free-tier fence rather than remove it.

**What to do next quarter:**
- Test clearer explanations of which playback controls are available on each plan, at the point users encounter a restriction.
- Prototype less disruptive free-tier playback rules and compare them with the current experience, including how shuffle and song choice are presented.
- Review upgrade prompts alongside these flows so restrictions and their alternatives are understandable rather than surprising.

**Why the data points here:**
- The full-window billing: pricing and free-tier design area has 61,298 complaint reviews [A4c] and a severity sum of 171,603 [A4s], ranking second behind usability’s severity sum of 197,874 [A2s]. Support accounts for 593 complaints [B7], while billing accounts for 60,705 [B6]; this is principally a pricing and free-tier issue, not a support workload.
- Free Tier Playback Restrictions drew 41,518 full-window complaints [C5]. In the recent quarter, it drew 24,198 complaints [R1c], representing 23.9% of recent complaints [R1p]. Its share of complaints rose between the comparable earlier and later windows, from 8.4% [T2a] to 16.8% [T2b].
- The broader free-tier levers set—ads, restrictions, and premium pressure—accounts for 88,806 full-window complaints [F0c] and 29.8% of complaints [F0p], versus 40,003 recent complaints [F1c] and 39.4% of recent complaints [F1p]. Premium-pressure share also rose from 0.8% [T3a] to 4.4% [T3b]. That is the clearest product-facing signal in the late-window mix.
- The surge is not solely free-tier backlash: overall complaint share rose from 30.6% [T0a] to 59.0% [T0b], while general dissatisfaction rose from 8.1% [T4a] to 33.4% [T4b] and generic update disappointment from 0.8% [T5a] to 6.4% [T5b]. Ads are a countertrend, falling from 20.3% [T1a] to 8.8% [T1b].

**The obvious pushback:** Free-tier friction is often deliberate versioning—a fence that nudges upgrades. The goal is a better-designed fence, not removing it. Reviews show backlash, including “wouldnt even let us shuffle” (review e7747485-42c5-4bb9-b1e2-1b4044eedb5a), but cannot establish conversion, revenue, or actual churn. Cancellation language is stated intent, not observed churn.

**What to pair this with:** Before committing, add internal experiment results and plan-level evidence on upgrade conversion, revenue, exposure to restrictions, and actual cancellations. Public reviews are self-selected feedback, and the recent-quarter increase in complaint share does not establish its cause.

## Alternatives considered

- **`usability.ads`** (Too Many Ads): Usability ranks first by full-window severity, with 197,874 [A2s], but the ads issue’s share of complaints declined between comparable windows, from 20.3% [T1a] to 8.8% [T1b].
- **`other.general`** (General App Dissatisfaction): General dissatisfaction is prominent and rose from 8.1% [T4a] to 33.4% [T4b], but it identifies no supported specific product topic and is not an actionable fix.
- **`playback.stops_pauses`** (Music Stops or Pauses Unexpectedly): Playback stops or pauses are a specific product issue, but playback is third by area severity, at 145,242 [A3s], and the supplied recency evidence does not establish a comparable increase.

## Limitations

- Historical Google Play reviews are self-selected public feedback, not a representative sample.
- Cancellation language records stated intent, not observed churn; the supplied data cannot show conversion, revenue, or causal effects.
- The late-window complaint-share surge and issue mix do not establish why individual users complained.

## Area totals cited as [A#c] / [A#s] (code-computed, `analysis/aggregates.csv`)

| Area | Topics | Complaint + cancellation reviews | Severity sum |
|---|---|---|---|
| access | access | 9,666 [A1c] | 37,494 [A1s] |
| usability | usability | 79,177 [A2c] | 197,874 [A2s] |
| playback | playback, downloads | 43,381 [A3c] | 145,242 [A3s] |
| billing_support | billing, support | 61,298 [A4c] | 171,603 [A4s] |

Pooled areas split by topic: playback 37,480 [B3]; downloads 5,901 [B4]; billing 60,705 [B6]; support 593 [B7]

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

## Free-tier monetization levers cited as [F#] (code-computed grouping, not the baseline ranking)

*complaint/cancellation reviews in issues usability.ads, billing.free_tier_limits, billing.premium_pressure (free-tier monetization design: ads, feature restrictions, upsell pressure)*

| Measure | Value |
|---|---|
| Complaints, full window | 88806 [F0c] |
| Share of all complaints, full window (%) | 29.8 [F0p] |
| Cancellation-intent reviews, full window | 10693 [F0k] |
| Complaints, recent quarter | 40003 [F1c] |
| Share of complaints, recent quarter (%) | 39.4 [F1p] |
| Share of complaints, Jun-Oct 2022 (%) | 29.6 [F2a] |
| Share of complaints, Jun-Oct 2023 (%) | 30.0 [F2b] |

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

*Reviews are self-selected public feedback. Cancellation language is stated intent, not observed churn. No revenue, plan tier or retention data exists in this dataset, so no revenue or churn impact is claimed.*
