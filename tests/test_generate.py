import hashlib

import numpy as np
import pandas as pd
import pytest

from mktg_copilot.data.generate import generate_facts
from mktg_copilot.data.spec import CAMPAIGNS, PAID_CHANNELS


def _digest(df):
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


def test_generation_is_deterministic_and_seed_dependent():
    a, b, c = generate_facts(seed=3), generate_facts(seed=3), generate_facts(seed=4)
    assert _digest(a) == _digest(b)
    assert _digest(a) != _digest(c)


def test_funnel_invariants(raw):
    assert (raw["clicks"] <= raw["impressions"]).all()
    assert (raw["sessions"] <= raw["clicks"]).all()
    assert (raw["orders"] <= raw["sessions"]).all()
    assert (raw["new_customers"] <= raw["orders"]).all()
    assert (raw[["impressions", "clicks", "spend", "sessions", "orders", "revenue", "new_customers"]] >= 0).all().all()
    assert (raw.loc[raw["channel_group"] == "owned", "spend"] == 0).all()
    assert (raw.loc[raw["channel_group"] == "paid", "spend"] > 0).all()
    assert not raw.duplicated(["date", "campaign_id", "region"]).any()


def test_row_counts_include_the_two_day_gap(raw):
    # 365 days x 20 campaigns x 4 regions, minus 2 days x 3 organic campaigns x 4 regions
    assert len(raw) == 365 * 20 * 4 - 2 * 3 * 4
    clean = generate_facts(seed=3, events=False)
    assert len(clean) == 365 * 20 * 4


def test_every_campaign_present(raw):
    assert set(raw["campaign_id"]) == {c.campaign_id for c in CAMPAIGNS}


def _day(raw, channel, start, end):
    d = raw[(raw["channel"] == channel) & (raw["date"] >= start) & (raw["date"] <= end)]
    return d.groupby("date")[["orders", "sessions", "spend", "impressions", "clicks", "revenue"]].sum()


def test_tracking_break_collapses_paid_search_orders_but_not_sessions(raw):
    brk = _day(raw, "paid_search", "2026-02-10", "2026-02-13")
    base = _day(raw, "paid_search", "2026-01-27", "2026-02-09")
    assert brk["orders"].mean() < 0.1 * base["orders"].mean()
    assert brk["sessions"].mean() > 0.8 * base["sessions"].mean()


def test_pacing_bug_quadruples_one_days_spend(raw):
    d = raw[raw["campaign_id"] == "PS02"].groupby("date")["spend"].sum()
    normal = d.loc["2026-04-01":"2026-04-08"].mean()
    assert 3.2 < d.loc["2026-04-09"] / normal < 4.8


def test_display_cpm_rises_about_35_percent_after_august_3(raw):
    d = raw[raw["channel"] == "display"].groupby("date")[["spend", "impressions"]].sum()
    d["cpm"] = d["spend"] / d["impressions"] * 1000
    ratio = d.loc["2026-08-10":"2026-09-20", "cpm"].mean() / d.loc["2026-06-20":"2026-07-30", "cpm"].mean()
    assert 1.25 < ratio < 1.45


def test_black_friday_weekend_lifts_orders(raw):
    o = raw.groupby("date")["orders"].sum()
    assert o.loc["2025-11-28"] > 1.6 * o.loc["2025-11-17":"2025-11-23"].mean()


def test_creative_fatigue_lowers_ctr_for_one_campaign_only(raw):
    def ctr(cid, a, b):
        d = raw[(raw["campaign_id"] == cid) & (raw["date"] >= a) & (raw["date"] <= b)]
        return d["clicks"].sum() / d["impressions"].sum()
    assert ctr("SO01", "2026-06-08", "2026-06-21") < 0.75 * ctr("SO01", "2026-04-06", "2026-05-03")
    assert ctr("SO02", "2026-06-08", "2026-06-21") > 0.9 * ctr("SO02", "2026-04-06", "2026-05-03")
