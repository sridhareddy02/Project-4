import sqlite3
from datetime import date

import numpy as np
import pytest

from mktg_copilot.data.warehouse import read_sql
from mktg_copilot.evals import oracle
from mktg_copilot.plan import DateRange, Filter
from mktg_copilot.semantic import CompileError, compile_query, inline_params, paid_only_filters

PERIOD = DateRange(start=date(2026, 6, 1), end=date(2026, 8, 31), label="summer")
PERIOD_T = ("2026-06-01", "2026-08-31")
PAID = [Filter(dimension="channel_group", values=["paid"])]
ALL_METRICS = ["spend", "impressions", "clicks", "sessions", "orders", "revenue", "new_customers", "ctr", "cpc", "cpm", "cvr", "aov", "roas", "cac", "cpa", "rps"]


def test_catalog_driver_chains_telescope(engine):
    # Catalog._check asserts this at load time; the chains must exist for the headline ratios
    for k in ("roas", "cac", "cpa", "cpc", "revenue", "orders"):
        assert k in engine.cat.drivers


@pytest.mark.parametrize("metric", ALL_METRICS)
@pytest.mark.parametrize("group", [None, "channel", "campaign", "region", "objective"])
def test_sql_matches_independent_pandas(engine, raw, metric, group):
    sql, params = compile_query(engine.cat, [metric], PERIOD, PAID, [group] if group else None)
    got = read_sql(engine.con, sql, params)
    exp = oracle.compute(raw, metric, PERIOD_T, {"channel_group": ["paid"]}, [group] if group else None)
    if group is None:
        assert got.iloc[0][metric] == pytest.approx(exp, rel=1e-9)
    else:
        by = dict(zip(got[group], got[metric]))
        assert set(by) == {k if not isinstance(k, tuple) else k[0] for k in exp}
        for k, v in exp.items():
            kk = k if not isinstance(k, tuple) else k[0]
            if np.isnan(v):
                assert by[kk] is None or np.isnan(by[kk])
            else:
                assert by[kk] == pytest.approx(v, rel=1e-9)


def test_ratios_are_ratios_of_sums_not_averages_of_ratios(engine, raw):
    sql, params = compile_query(engine.cat, ["roas"], PERIOD, PAID)
    sql_roas = read_sql(engine.con, sql, params).iloc[0]["roas"]
    d = raw[(raw["channel_group"] == "paid") & (raw["date"] >= PERIOD_T[0]) & (raw["date"] <= PERIOD_T[1])]
    naive_mean_of_daily = (d.groupby("date")["revenue"].sum() / d.groupby("date")["spend"].sum()).mean()
    assert sql_roas == pytest.approx(d["revenue"].sum() / d["spend"].sum())
    assert sql_roas != pytest.approx(naive_mean_of_daily, rel=1e-12)


def test_compiler_rejects_unknown_names(engine):
    with pytest.raises(CompileError):
        compile_query(engine.cat, ["profit"], PERIOD)
    with pytest.raises(CompileError):
        compile_query(engine.cat, ["revenue"], PERIOD, group_by=["sku"])
    with pytest.raises(CompileError):
        compile_query(engine.cat, ["revenue"], PERIOD, filters=[Filter(dimension="channel", values=["tiktok"])])
    with pytest.raises(CompileError):
        compile_query(engine.cat, ["revenue"], PERIOD, filters=[Filter(dimension="not_a_column", values=["x"])])
    with pytest.raises(CompileError):
        compile_query(engine.cat, ["revenue"], PERIOD, order_by="1; DROP TABLE fact_daily")


def test_values_are_bound_parameters_never_interpolated(engine):
    sql, params = compile_query(engine.cat, ["revenue"], PERIOD, [Filter(dimension="channel", values=["email"])])
    assert "email" not in sql and "2026" not in sql
    assert params == ["2026-06-01", "2026-08-31", "email"]
    assert "'email'" in inline_params(sql, params)          # display-only rendering


def test_connection_is_read_only(engine):
    with pytest.raises(sqlite3.OperationalError):
        engine.con.execute("DELETE FROM fact_daily")
    with pytest.raises(sqlite3.OperationalError):
        engine.con.execute("DROP TABLE dim_campaign")


def test_paid_only_scope_rule(engine):
    f, note = paid_only_filters(engine.cat, ["roas"], [])
    assert f and f[0].values == ["paid"] and note
    f, note = paid_only_filters(engine.cat, ["revenue"], [])
    assert f == [] and note is None
    f, note = paid_only_filters(engine.cat, ["roas"], [Filter(dimension="channel", values=["display"])])
    assert note is None and len(f) == 1
