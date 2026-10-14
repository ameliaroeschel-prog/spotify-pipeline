# Decision memo: where should next quarter's product effort go?

*Source: 10,000 Google Play reviews (May 2022 – Nov 2023); 9,996 classified, 4 quarantined, 0 unprocessed. Written by the memo-writer role (gpt-6-luna) from saved aggregates only; every number and ID below was checked by code (validation: passed).*

**Recommendation: usability — lead issue `usability.ads` (Too Many Repetitive Ads)**

Prioritize **usability**, with **Too Many Repetitive Ads** as the lead issue. Usability ranks first by severity sum in the supplied area ranking, with 3,193 severity points and 1,266 complaints [A2]. Within that area, repetitive ads are the largest specified issue by complaint count: 602 complaints [C9], with a severity sum of 1288 [C10]. Reviews describe interruptions directly: “One song one ad. Too much ad.” (review 79301447-1a7c-49ed-ac4e-b3b398dc65ea). This makes ad experience a concrete lead for usability work, while the area-level ranking supports investing in usability overall. **Billing/support** is the closest alternative in the area ranking, with 2,712 severity points and 964 complaints [A4]; its free-tier-limits issue has 668 complaints [C5] and severity sum of 1980 [C6], so it merits consideration but is not the top-ranked area. Playback ranks below billing/support, with 2,183 severity points and 653 complaints [A3]. This recommendation prioritizes the leading usability issue; the aggregates do not establish which intervention would have the greatest product impact.

## Alternatives considered

- **`billing.free_tier_limits`** (Basic Features Locked Behind Premium): A substantial alternative: 668 complaints [C5] and severity sum of 1980 [C6]. Billing/support ranks below usability by area severity sum [A4]; the supplied data does not establish that addressing free-tier limits would outperform the recommended usability focus.
- **`usability.controls_navigation`** (Confusing and Limited Playback Controls): Also within the recommended area, but has 203 complaints [C13] and severity sum of 592 [C14], compared with the ad issue’s 602 complaints [C9] and severity sum of 1288 [C10].

## Limitations

- The evidence consists of pre-computed aggregates and selected public reviews; reviewers are self-selected and may not represent all Spotify users.
- Complaint and severity aggregates do not establish causal impact, expected outcomes, or which proposed intervention would be most effective.
- Cancellation language, where present in reviews, indicates stated intent and is not observed churn.

## Area totals cited as [A#] (code-computed, `aggregates.csv`)

| ID | Area | Topics | Complaint + cancellation reviews | Severity sum |
|---|---|---|---|---|
| A1 | access | access | 138 | 533 |
| A2 | usability | usability | 1,266 | 3,193 |
| A3 | playback | playback, downloads | 653 | 2,183 |
| A4 | billing_support | billing, support | 964 | 2,712 |

## Issue claims cited as [C#] (`grading/claims.csv`, recomputed by the checker)

| Claim | Issue | Metric | Value |
|---|---|---|---|
| C1 | `other.general` General App Dissatisfaction | complaint_count | 1076 |
| C2 | `other.general` General App Dissatisfaction | severity_sum | 2129 |
| C3 | `other.general` General App Dissatisfaction | mean_severity | 1.978625 |
| C4 | `other.general` General App Dissatisfaction | priority_score | 2129 |
| C5 | `billing.free_tier_limits` Basic Features Locked Behind Premium | complaint_count | 668 |
| C6 | `billing.free_tier_limits` Basic Features Locked Behind Premium | severity_sum | 1980 |
| C7 | `billing.free_tier_limits` Basic Features Locked Behind Premium | mean_severity | 2.964072 |
| C8 | `billing.free_tier_limits` Basic Features Locked Behind Premium | priority_score | 1980 |
| C9 | `usability.ads` Too Many Repetitive Ads | complaint_count | 602 |
| C10 | `usability.ads` Too Many Repetitive Ads | severity_sum | 1288 |
| C11 | `usability.ads` Too Many Repetitive Ads | mean_severity | 2.139535 |
| C12 | `usability.ads` Too Many Repetitive Ads | priority_score | 1288 |
| C13 | `usability.controls_navigation` Confusing and Limited Playback Controls | complaint_count | 203 |
| C14 | `usability.controls_navigation` Confusing and Limited Playback Controls | severity_sum | 592 |
| C15 | `usability.controls_navigation` Confusing and Limited Playback Controls | mean_severity | 2.916256 |
| C16 | `usability.controls_navigation` Confusing and Limited Playback Controls | priority_score | 592 |
| C17 | `usability.forced_shuffle` Unwanted Songs in Playlists | complaint_count | 159 |
| C18 | `usability.forced_shuffle` Unwanted Songs in Playlists | severity_sum | 480 |
| C19 | `usability.forced_shuffle` Unwanted Songs in Playlists | mean_severity | 3.018868 |
| C20 | `usability.forced_shuffle` Unwanted Songs in Playlists | priority_score | 480 |
| C21 | `access.login_failure` Unable to Log In | complaint_count | 123 |
| C22 | `access.login_failure` Unable to Log In | severity_sum | 472 |
| C23 | `access.login_failure` Unable to Log In | mean_severity | 3.837398 |
| C24 | `access.login_failure` Unable to Log In | priority_score | 472 |
| C25 | `playback.loading_network` App Loading and Connection Problems | complaint_count | 138 |
| C26 | `playback.loading_network` App Loading and Connection Problems | severity_sum | 470 |
| C27 | `playback.loading_network` App Loading and Connection Problems | mean_severity | 3.405797 |
| C28 | `playback.loading_network` App Loading and Connection Problems | priority_score | 470 |
| C29 | `playback.stops_pauses` Playback Stops or Cuts Out | complaint_count | 142 |
| C30 | `playback.stops_pauses` Playback Stops or Cuts Out | severity_sum | 450 |
| C31 | `playback.stops_pauses` Playback Stops or Cuts Out | mean_severity | 3.169014 |
| C32 | `playback.stops_pauses` Playback Stops or Cuts Out | priority_score | 450 |

## Evidence quotes cited

- `79301447-1a7c-49ed-ac4e-b3b398dc65ea` (usability.ads, severity 2): "One song one ad. Too much ad."

*Reviews are self-selected public feedback. Cancellation language is stated intent, not observed churn. No revenue, plan tier or retention data exists in this dataset, so no revenue or churn impact is claimed.*
