"""Seeded generator for the synthetic marketing warehouse.

Every row is (date, campaign, region). Paid campaigns are budget driven (spend -> impressions via CPM ->
clicks via CTR -> sessions -> orders -> revenue); owned and organic campaigns are volume driven. Weekly
and annual seasonality, a gentle trend, and sampling noise are added, then the events in `spec.EVENTS`
are injected so the copilot can be graded on whether it finds them.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..config import AS_OF, SEED, START
from .spec import CAMPAIGNS, CHANNEL_LABELS, OBJECTIVE_LABELS, PAID_CHANNELS, REGIONS

# Day-of-week multipliers, Monday first.
_DOW_COMMERCE = np.array([0.97, 0.98, 0.98, 1.00, 0.96, 1.01, 1.10])
_DOW_EMAIL = np.array([1.00, 1.18, 1.05, 1.15, 0.92, 0.55, 0.65])
_DOW_ORGANIC = np.array([1.03, 1.04, 1.02, 1.00, 0.95, 0.94, 1.02])

BLACK_FRIDAY = date(2025, 11, 28)
CYBER_MONDAY = date(2025, 12, 1)


def _annual_demand(dates: pd.DatetimeIndex) -> np.ndarray:
    """Traffic and conversion demand by day of year: holiday peak, January dip, quiet summer."""
    doy = dates.dayofyear.to_numpy().astype(float)
    peak = np.exp(-0.5 * ((doy - 335) / 18.0) ** 2)          # early December
    dip = np.exp(-0.5 * ((doy - 20) / 14.0) ** 2)            # mid January
    summer = np.exp(-0.5 * ((doy - 200) / 25.0) ** 2)        # July
    return 1.0 + 0.30 * peak - 0.14 * dip - 0.07 * summer


def _in_range(dates: pd.DatetimeIndex, start: str, end: str) -> np.ndarray:
    return (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))


def build_dimensions() -> dict[str, pd.DataFrame]:
    camp = pd.DataFrame(
        [
            {
                "campaign_id": c.campaign_id,
                "campaign": c.campaign,
                "channel": c.channel,
                "channel_label": CHANNEL_LABELS[c.channel],
                "channel_group": "paid" if c.paid else "owned",
                "objective": c.objective,
                "objective_label": OBJECTIVE_LABELS[c.objective],
            }
            for c in CAMPAIGNS
        ]
    )
    d = pd.date_range(START, AS_OF, freq="D")
    dates = pd.DataFrame(
        {
            "date": d.strftime("%Y-%m-%d"),
            "week_start": (d - pd.to_timedelta(d.dayofweek, unit="D")).strftime("%Y-%m-%d"),
            "month_start": d.to_period("M").to_timestamp().strftime("%Y-%m-%d"),
            "dow": d.dayofweek,
            "is_weekend": (d.dayofweek >= 5).astype(int),
        }
    )
    return {"dim_campaign": camp, "dim_date": dates}


def generate_facts(seed: int = SEED, events: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(START, AS_OF, freq="D")
    T = len(dates)
    t = np.arange(T) / (T - 1)
    dow = dates.dayofweek.to_numpy()
    demand = _annual_demand(dates)

    # Black Friday weekend and Cyber Monday lift traffic and conversion everywhere.
    bf_traffic = np.ones(T)
    bf_cvr = np.ones(T)
    if events:
        for d, tr, cv in ((BLACK_FRIDAY, 1.35, 1.9), (BLACK_FRIDAY + timedelta(1), 1.25, 1.6),
                          (BLACK_FRIDAY + timedelta(2), 1.20, 1.45), (CYBER_MONDAY, 1.30, 1.8)):
            m = dates == pd.Timestamp(d)
            bf_traffic[m] = tr
            bf_cvr[m] = cv

    frames = []
    for c in CAMPAIGNS:
        growth = rng.uniform(-0.04, 0.14)
        trend = 1.0 + growth * t
        for region, (share, aov_mult, cvr_mult) in REGIONS.items():
            noise = lambda sd: rng.lognormal(0.0, sd, T)  # noqa: E731

            if c.paid:
                dow_spend = _DOW_COMMERCE ** 0.5
                spend = c.volume * share * trend * dow_spend[dow] * (1 + 0.10 * (demand - 1)) * noise(0.07)
                cpm = c.cpm * (1 + 0.20 * np.clip(demand - 1, 0, None) * 3) * noise(0.04)
                ctr_mult = np.ones(T)
                cvr_event = np.ones(T)
                spend_mult = np.ones(T)
                if events:
                    if c.campaign_id == "SO01":  # E2 creative fatigue, then a refresh
                        ramp = _in_range(dates, "2026-05-04", "2026-06-21")
                        k = np.clip((dates - pd.Timestamp("2026-05-04")).days / 48.0, 0, 1)
                        ctr_mult = np.where(ramp, 1 - 0.38 * k, 1.0)
                        cvr_event = np.where(ramp, 1 - 0.27 * k, 1.0)
                    if c.channel == "display":  # E4 CPM inflation
                        cpm = cpm * np.where(_in_range(dates, "2026-08-03", "2026-09-27"), 1.35, 1.0)
                    if c.campaign_id == "PS02":  # E5 pacing bug
                        bug = dates == pd.Timestamp("2026-04-09")
                        spend_mult = np.where(bug, 4.0, 1.0)
                        cvr_event = np.where(bug, 0.5, cvr_event)
                    # paid media leans into the holiday weekend a little
                    spend_mult = spend_mult * np.where(bf_traffic > 1, 1.15, 1.0)
                spend = spend * spend_mult
                impressions = spend / cpm * 1000.0
            else:
                dowf = _DOW_EMAIL if c.channel == "email" else _DOW_ORGANIC
                impressions = c.volume * share * trend * dowf[dow] * demand * bf_traffic * noise(0.06)
                spend = np.zeros(T)
                cvr_event = np.ones(T)
                ctr_mult = np.ones(T)

            impressions = np.round(impressions)
            clicks = rng.poisson(impressions * c.ctr * ctr_mult * noise(0.03)).astype(float)
            sessions = rng.binomial(clicks.astype(int), c.click_to_session).astype(float)
            p_order = np.clip(c.cvr * cvr_mult * demand * bf_cvr * cvr_event * noise(0.04), 0, 0.95)
            orders = rng.binomial(sessions.astype(int), p_order).astype(float)

            if events and c.channel == "paid_search":  # E1 tracking break: most conversions are not recorded
                brk = _in_range(dates, "2026-02-10", "2026-02-13")
                kept = rng.binomial(orders.astype(int), 0.03).astype(float)
                orders = np.where(brk, kept, orders)

            shape = 4.0 * np.maximum(orders, 1e-9)
            revenue = np.where(orders > 0, rng.gamma(shape, c.aov * aov_mult * (1 + 0.03 * (demand - 1)) / 4.0), 0.0)
            new_customers = rng.binomial(orders.astype(int), c.new_share).astype(float)

            frames.append(
                pd.DataFrame(
                    {
                        "date": dates.strftime("%Y-%m-%d"),
                        "campaign_id": c.campaign_id,
                        "region": region,
                        "impressions": impressions.astype(np.int64),
                        "clicks": clicks.astype(np.int64),
                        "spend": np.round(spend, 2),
                        "sessions": sessions.astype(np.int64),
                        "orders": orders.astype(np.int64),
                        "revenue": np.round(revenue, 2),
                        "new_customers": new_customers.astype(np.int64),
                    }
                )
            )

    facts = pd.concat(frames, ignore_index=True)
    if events:  # E6 ingestion failure: organic rows missing for two days
        ch = facts["campaign_id"].map({c.campaign_id: c.channel for c in CAMPAIGNS})
        gap = ch.eq("organic_search") & facts["date"].isin(["2026-03-17", "2026-03-18"])
        facts = facts[~gap].reset_index(drop=True)
    return facts


def paid_channels() -> tuple[str, ...]:
    return PAID_CHANNELS
