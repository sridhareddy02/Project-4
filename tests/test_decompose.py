import math

import numpy as np
import pandas as pd
import pytest

from mktg_copilot.analysis.decompose import eval_metric, factor_decomposition, segment_decomposition
from mktg_copilot.analysis.stats import bootstrap_delta

MEASURES = ["impressions", "clicks", "spend", "sessions", "orders", "revenue", "new_customers"]


def random_sums(rng, scale=1.0):
    imp = rng.uniform(1e5, 1e6) * scale
    clicks = imp * rng.uniform(0.005, 0.05)
    sessions = clicks * rng.uniform(0.8, 0.98)
    orders = sessions * rng.uniform(0.01, 0.08)
    return {"impressions": imp, "clicks": clicks, "spend": clicks * rng.uniform(0.5, 3), "sessions": sessions, "orders": orders,
            "revenue": orders * rng.uniform(60, 110), "new_customers": orders * rng.uniform(0.1, 0.9)}


@pytest.mark.parametrize("metric", ["roas", "cac", "cpa", "cpc", "rps", "revenue", "orders", "sessions", "clicks", "new_customers", "spend"])
def test_factor_decomposition_is_exact(engine, metric):
    rng = np.random.default_rng(1)
    for _ in range(50):
        s0, s1 = random_sums(rng), random_sums(rng)
        facs = factor_decomposition(engine.cat, metric, s0, s1)
        assert facs is not None
        # the factors multiply back to the ratio of the metric's values
        prod = math.prod(f.after / f.before for f in facs)
        v0, v1 = eval_metric(engine.cat, metric, s0), eval_metric(engine.cat, metric, s1)
        assert prod == pytest.approx(v1 / v0, rel=1e-9)
        # and their shares of the log change add to one
        assert sum(f.log_share for f in facs) == pytest.approx(1.0, rel=1e-9)


def test_factor_decomposition_returns_none_on_zero_factor(engine):
    s0 = {m: 10.0 for m in MEASURES}
    s1 = dict(s0, new_customers=0.0)
    assert factor_decomposition(engine.cat, "cac", s0, s1) is None


def make_segments(rng, n, drop=()):
    return pd.DataFrame([random_sums(rng, scale=rng.uniform(0.2, 3)) for _ in range(n)], index=[f"s{i}" for i in range(n)]).drop(index=list(drop))


@pytest.mark.parametrize("metric", ["roas", "cac", "cvr", "aov", "ctr", "cpm"])
def test_segment_effects_add_up_to_the_total_change(engine, metric):
    rng = np.random.default_rng(2)
    for _ in range(50):
        seg0, seg1 = make_segments(rng, 6), make_segments(rng, 6)
        effects = segment_decomposition(engine.cat, metric, seg0, seg1)
        total0 = eval_metric(engine.cat, metric, seg0.sum())
        total1 = eval_metric(engine.cat, metric, seg1.sum())
        assert sum(e.contribution for e in effects) == pytest.approx(total1 - total0, rel=1e-9, abs=1e-12)
        for e in effects:
            assert e.mix + e.rate == pytest.approx(e.contribution, rel=1e-9, abs=1e-12)


def test_segment_effects_with_segment_missing_in_one_period(engine):
    rng = np.random.default_rng(3)
    seg0 = make_segments(rng, 5)
    seg1 = make_segments(rng, 5, drop=["s2"])          # segment s2 has no activity in the later period
    effects = segment_decomposition(engine.cat, "roas", seg0, seg1)
    t0, t1 = eval_metric(engine.cat, "roas", seg0.sum()), eval_metric(engine.cat, "roas", seg1.sum())
    assert sum(e.contribution for e in effects) == pytest.approx(t1 - t0, rel=1e-9)
    gone = next(e for e in effects if e.segment == "s2")
    assert gone.weight_after == 0 and gone.mix < 0 and gone.rate == pytest.approx(0)


def test_additive_segment_effects_are_raw_changes(engine):
    seg0 = pd.DataFrame({m: [10.0, 20.0] for m in MEASURES}, index=["a", "b"])
    seg1 = pd.DataFrame({m: [15.0, 18.0] for m in MEASURES}, index=["a", "b"])
    eff = {e.segment: e.contribution for e in segment_decomposition(engine.cat, "revenue", seg0, seg1)}
    assert eff == {"a": 5.0, "b": -2.0}


BASE_DAY = random_sums(np.random.default_rng(99))


def daily(rng, n, level, noise=0.05):
    """n days around a stable funnel, scaled by `level` with multiplicative day-to-day noise."""
    return pd.DataFrame([{m: v * level * rng.normal(1, noise) for m, v in BASE_DAY.items()} for _ in range(n)])


def test_bootstrap_sign_and_arguments_are_baseline_then_current(engine):
    """Regression test: the baseline comes first. Swapping the arguments flips the sign of the change."""
    rng = np.random.default_rng(4)
    lo, hi = daily(rng, 30, 1.0), daily(rng, 30, 1.3)
    up = bootstrap_delta(engine.cat, "revenue", lo, hi)
    down = bootstrap_delta(engine.cat, "revenue", hi, lo)
    assert up.delta_pct > 0 and down.delta_pct < 0
    assert up.low > 0 and up.clear and down.high < 0 and down.clear


def test_bootstrap_does_not_call_noise_a_change(engine):
    rng = np.random.default_rng(5)
    flags = []
    for _ in range(40):
        a, b = daily(rng, 28, 1.0, 0.08), daily(rng, 28, 1.0, 0.08)
        flags.append(bootstrap_delta(engine.cat, "revenue", a, b, draws=400).clear)
    assert np.mean(flags) < 0.15       # nominal 5%; allow slack for 40 trials


def test_bootstrap_per_day_handles_unequal_periods(engine):
    rng = np.random.default_rng(6)
    a, b = daily(rng, 31, 1.0), daily(rng, 27, 1.0)
    totals = bootstrap_delta(engine.cat, "revenue", a, b)
    per_day = bootstrap_delta(engine.cat, "revenue", a, b, per_day=True)
    assert totals.delta_pct < -8          # fewer days, so a smaller total
    assert abs(per_day.delta_pct) < 5     # per day they are the same level
