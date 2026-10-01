# Evaluation

Run it with `python -m mktg_copilot eval --check`. It writes `reports/eval.md` and `reports/eval.json`, and
exits non-zero if any gate fails. CI runs it on every change.

## What is measured

| Suite | What it checks | Latest result |
|---|---|---|
| Plans, dev set | 58 questions the planner was built against: intent, metrics, breakdown, filters, period, comparison period | 100% exact |
| Plans, held-out set | 40 questions written separately, with different phrasings | 100% exact (see below) |
| Numbers | The first table of every answerable question against an independent pandas implementation with its own hand-written formulas (67 answers) | 67 of 67 |
| Safety | 24 questions that should be refused, clarified, or handled safely: unsupported metrics, prompt injection, SQL fragments, unknown regions and platforms, out-of-range years | 24 of 24, warehouse intact |
| Grounding | Every number in the prose traced to a computed value (98 answers) | 460 of 460 numbers |
| Injected events | Six known events, found without being told | 6 of 6 |
| False alarms | The anomaly detector on event-free data from another seed | 0.14 incidents per series-year |
| Latency | Per question, in the cloud container used for development | median about 16 ms, p95 about 1 s (forecasts and drill-downs) |

## The honest history of these numbers

The first run of the harness, before any fixes, scored:

| Suite | First run |
|---|---|
| Dev plans | 91.4% (53 of 58) |
| **Held-out plans** | **82.5% (33 of 40)** |
| Safety | 87.5% (21 of 24) |
| Injected events | 5 of 6 (one check was mis-specified: it looked for the channel name when the scope was already one channel and the answer named campaigns) |

The failures were genuine bugs, fixed one by one:

* A guard that refused to answer when a question said "by revenue" or "ROAS channel", because metric words had not been masked before the unknown-breakdown check.
* "Why did revenue spike on Black Friday?" routed to anomaly scanning instead of explaining the change.
* "orders Jan vs Feb" read only one month.
* A monthly trend with no period fell back to 12 weeks instead of the full history.
* "Net revenue" was silently treated as "revenue".
* "Churn by cohort" and "optimize my budget" were clarified rather than refused.
* Two named channels ("compare paid search and paid social ROAS") were compared with last month instead of with each other.
* "Most efficient" ranked CAC in the wrong direction.

Because the held-out failures were used to find these bugs, **the held-out set is no longer a blind test**, and its
100% should be read as "no known failures" rather than as an unbiased estimate. A fresh set of questions would
probably score between the first-run 82% and 100%.

Two further bugs were found by reading outputs rather than by the harness, and both now have regression tests:
the bootstrap interval had its baseline and current arguments swapped (a clear decline was reported with a
positive range), and the verifier mis-read "June 2026" as "June 20" and "26".

## Calibration, and what it is not

The anomaly rule (robust z of 8 and a 15% deviation) was tuned on event-free data from seeds the rest of the
project does not use, then measured on a different one. That is honest for synthetic data, but real data has
holidays, promotions and outages this generator does not, so the thresholds would need recalibrating.

The forecast band is calibrated on the same history it is evaluated on, with overlapping origins, so it is a
rough guide to typical error and not a guarantee.

## What this evaluation cannot show

* **Language coverage.** The planner is rules, so 100% on these sets says nothing about how it handles phrasing
  no one has written yet. The optional LLM planner exists for that and has not been evaluated against a live model.
* **Real-world accuracy.** All data is synthetic and every event was injected by the same author as the detector.
  The point is that the pipeline's machinery is correct and measurable, not that it would find every real fault.
