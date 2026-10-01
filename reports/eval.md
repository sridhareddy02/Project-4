# Evaluation report

Data: 29,176 rows, 2025-09-28 to 2026-09-27 (synthetic).

| Suite | Result | Gate |
|---|---|---|
| Plan accuracy, dev set (58 questions) | 100% exact, 100% intent | pass |
| Plan accuracy, held-out set (40 questions) | 100% exact, 100% intent | pass |
| Numbers versus an independent pandas oracle | 67 of 67 answers match | pass |
| Refusals, clarifications and injection safety (24 questions) | 100%; warehouse intact: True | pass |
| Grounding: every number in the prose traced to data | 98 of 98 answers; 460 of 460 numbers | pass |
| Injected events found without being told (6) | 6 of 6 | pass |
| False incidents on event-free data | 8 over 58 series, 0.138 per series-year | pass |
| Latency per question | p50 14.9 ms, p95 911.0 ms | n/a |

## Injected events

| Event | What was injected | Found | Detail |
|---|---|---|---|
| E1 | tracking break (paid search) | yes | Orders -97% on Feb 10 to Feb 13, 2026, where: Search - Shopping Feed (36%); Search - Non-brand Category (32%) |
| E3 | Black Friday weekend peak | yes | Revenue +97% on Nov 28 to Dec 1, 2025; note: overlaps Black Friday weekend and Cyber Monday, a known retail date |
| E5 | pacing bug (one day of 4x spend) | yes | Spend +111% on Apr 9, 2026, where: Search - Non-brand Category (100%) |
| E6 | ingestion gap (organic search rows missing) | yes | warned about the gap and labelled the incident as a probable data gap |
| E2 | creative fatigue (Social - Prospecting Lookalike) | yes | largest funnel step: Click-through rate; leading campaign: Social - Prospecting Lookalike |
| E4 | display CPM inflation (+35%) | yes | largest funnel step: Cost per thousand impressions (CPM) (+31.6%) |

## Dev set failures

None.

## Held-out set failures

None.

## Number mismatches

None.

## Adversarial misses

None.

## Grounding failures

None.
