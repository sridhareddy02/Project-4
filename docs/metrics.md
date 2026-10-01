# Metric catalog

Generated from `src/mktg_copilot/metrics.yaml` by `python -m mktg_copilot catalog-doc`. Do not edit by hand.

Ratios are always the ratio of summed numerators and denominators, never an average of daily ratios.

| Metric | Formula | Better when | Scope | Plain meaning |
|---|---|---|---|---|
| Spend | `SUM(spend)` | n/a | all channels | Money spent on paid media (search, social and display). Email and organic search have no media spend in this data. |
| Impressions | `SUM(impressions)` | n/a | all channels | Times an ad or email was shown or delivered, or an organic result appeared in search. |
| Clicks | `SUM(clicks)` | n/a | all channels | Clicks on an ad, email or organic search result. |
| Sessions | `SUM(sessions)` | n/a | all channels | Visits to the website that started from a click. |
| Orders | `SUM(orders)` | n/a | all channels | Completed purchases. Conversions in this warehouse are orders. |
| Revenue | `SUM(revenue)` | n/a | all channels | Order value in dollars before returns, discounts beyond the order price, or costs. |
| New customers | `SUM(new_customers)` | n/a | all channels | Customers placing their first order. |
| CTR | `SUM(clicks) / SUM(impressions)` | higher | all channels | Share of impressions that were clicked. |
| CPC | `SUM(spend) / SUM(clicks)` | lower | paid channels only | Average media spend per click. |
| CPM | `SUM(spend) / SUM(impressions) x 1000` | lower | paid channels only | Media spend per thousand impressions. |
| Conversion rate | `SUM(orders) / SUM(sessions)` | higher | all channels | Share of sessions that ended in an order. |
| AOV | `SUM(revenue) / SUM(orders)` | higher | all channels | Average revenue per order. |
| ROAS | `SUM(revenue) / SUM(spend)` | higher | paid channels only | Revenue generated per dollar of paid media spend. |
| CAC | `SUM(spend) / SUM(new_customers)` | lower | paid channels only | Paid media spend per new customer. |
| CPA | `SUM(spend) / SUM(orders)` | lower | paid channels only | Paid media spend per order (new or returning customers). |
| Revenue per session | `SUM(revenue) / SUM(sessions)` | higher | all channels | Revenue divided by sessions. |

## Funnel factors used by driver analysis

Each chain multiplies back to the metric exactly.

* **ROAS** = Average order value x Conversion rate x Click-to-session rate x Click-through rate x Impressions per dollar (the inverse of CPM)
* **CAC** = Cost per thousand impressions (CPM) x Impressions per click (the inverse of CTR) x Clicks per session x Sessions per order (the inverse of conversion rate) x Orders per new customer
* **CPA** = Cost per thousand impressions (CPM) x Impressions per click (the inverse of CTR) x Clicks per session x Sessions per order (the inverse of conversion rate)
* **CPC** = Cost per thousand impressions (CPM) x Impressions per click (the inverse of CTR)
* **Revenue per session** = Average order value x Conversion rate
* **Revenue** = Average order value x Conversion rate x Click-to-session rate x Click-through rate x Impressions (volume)
* **Orders** = Conversion rate x Click-to-session rate x Click-through rate x Impressions (volume)
* **Sessions** = Click-to-session rate x Click-through rate x Impressions (volume)
* **Clicks** = Click-through rate x Impressions (volume)
* **New customers** = New-customer share of orders x Conversion rate x Click-to-session rate x Click-through rate x Impressions (volume)
* **Spend** = Cost per thousand impressions (CPM) x Impressions (volume)

## Dimensions

* **Channel**: Paid Search, Paid Social, Display, Email, Organic Search
* **Region**: Northeast, Midwest, South, West
* **Channel group**: Paid, Owned
* **Objective**: Acquisition, Retargeting, Retention, Demand capture
* **Campaign**: Display - Programmatic Prospecting, Display - Retargeting, Display - Sponsored Placements, Email - Welcome Series, Email - Cart Abandonment, Email - Weekly Newsletter, Email - Win-back, Email - Promotions, Organic - Brand Terms, Organic - Category Terms, Organic - Content and Blog, Search - Brand Core, Search - Non-brand Category, Search - Competitor Terms, Search - Shopping Feed, Social - Prospecting Lookalike, Social - Prospecting Interest, Social - Retargeting Cart, Social - Retargeting Viewers, Social - Creator UGC

## Questions the copilot refuses, and why

* **profit, margin, gross margin**: the warehouse has revenue and media spend only, with no costs of goods or fees, so profit cannot be computed.
* **roi, return on investment**: ROI needs all costs, not just media spend.
* **ltv, lifetime value, clv**: there is no customer-level history in this dataset, only daily aggregates.
* **churn, retention rate, repeat rate**: retention needs customer-level data, which this warehouse does not hold (Project-2 covers cohorts).
* **attribution, multi-touch, multi touch**: facts are channel-level last-touch totals, so attribution and incrementality cannot be estimated here (Project-1 covers this).
* **bounce rate, time on site, nps**: those measures are not loaded in this warehouse.
* **budget recommendation, optimal budget, optimize budget**: the copilot describes performance; budget allocation needs experiments or a causal model.
