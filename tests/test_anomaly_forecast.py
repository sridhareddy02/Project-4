import numpy as np
import pandas as pd
import pytest

from mktg_copilot.analysis import anomaly as an
from mktg_copilot.analysis.forecast import MAX_HORIZON, forecast_series
from mktg_copilot.data.warehouse import build_warehouse
from mktg_copilot.engine import Engine
from mktg_copilot.plan import Filter


def ps(engine, metric, channel="paid_search"):
    return engine.series(None, metric, [Filter(dimension="channel", values=[channel])])


def starts(incs):
    return {(str(i.start), str(i.end), i.direction) for i in incs}


def test_finds_tracking_break_and_pacing_bug(engine):
    _, orders = an.detect("orders", ps(engine, "orders"))
    assert ("2026-02-10", "2026-02-13", "down") in starts(orders)
    _, spend = an.detect("spend", ps(engine, "spend"))
    assert ("2026-04-09", "2026-04-09", "up") in starts(spend)


def test_finds_black_friday_and_labels_it(engine):
    _, rev = an.detect("revenue", engine.series(None, "revenue", []))
    bf = [i for i in rev if str(i.start) == "2025-11-28"]
    assert bf and bf[0].direction == "up" and "Black Friday" in (bf[0].context or "")


def test_event_free_data_raises_few_false_incidents(tmp_path):
    db = tmp_path / "clean.db"
    build_warehouse(db, seed=2024, events=False)
    eng = Engine(db)
    n = 0
    for m in ("revenue", "orders", "sessions", "clicks"):
        for ch in (None, "paid_search", "paid_social", "email"):
            filt = [Filter(dimension="channel", values=[ch])] if ch else []
            n += len(an.detect(m, eng.series(None, m, filt))[1])
    assert n <= 3         # 16 series; calibrated rate is about 0.14 per series-year


def test_short_history_is_refused():
    with pytest.raises(ValueError):
        an.detect("orders", pd.Series(np.ones(10), index=pd.date_range("2026-01-01", periods=10)))


def test_flags_need_both_a_large_z_and_a_material_deviation():
    idx = pd.date_range("2026-01-01", periods=120)
    rng = np.random.default_rng(0)
    base = 1000 * (1 + 0.1 * np.sin(np.arange(120) * 2 * np.pi / 7)) * rng.normal(1, 0.002, 120)
    small = base.copy(); small[60] *= 1.06           # statistically odd but only 6% off
    big = base.copy(); big[60] *= 1.5
    assert not an.score_series(pd.Series(small, index=idx))["flag"].any()
    assert an.score_series(pd.Series(big, index=idx))["flag"].iloc[60]


def test_forecast_shape_ordering_and_baseline(engine):
    y = engine.series(None, "revenue", [])
    f = forecast_series(y, 28)
    assert len(f.dates) == len(f.mean) == 28
    assert all(lo < m < hi for lo, m, hi in zip(f.lower, f.mean, f.upper))
    assert f.total_lower < f.total_mean < f.total_upper
    assert f.backtest_mape < f.naive_mape
    assert 0.7 < f.interval_coverage < 0.9
    assert len(f.weekly) == 4


def test_forecast_horizon_limits(engine):
    y = engine.series(None, "revenue", [])
    for bad in (0, MAX_HORIZON + 1):
        with pytest.raises(ValueError):
            forecast_series(y, bad)
