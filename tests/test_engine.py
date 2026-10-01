import math

import pytest

from mktg_copilot.evals import oracle
from mktg_copilot.plan import QueryPlan
from mktg_copilot.verify import verify


def test_summary_matches_oracle_and_is_grounded(engine, raw):
    a = engine.ask("What was ROAS by channel last week?")
    assert a.status == "ok" and a.grounding["passed"] and a.grounding["checked"] >= 2
    exp = oracle.compute(raw, "roas", ("2026-09-21", "2026-09-27"), {}, ["channel"])
    rows = {r["channel"]: r["roas"] for r in a.tables[0].rows}
    assert rows["Paid Search"] == pytest.approx(exp[("paid_search",)] if ("paid_search",) in exp else exp["paid_search"])
    assert set(rows) == {"Paid Search", "Paid Social", "Display"}              # owned channels have no spend


def test_every_answer_exposes_its_plan_and_sql(engine):
    a = engine.ask("Revenue by region last month")
    assert a.plan.intent == "summary" and a.sql and a.sql[0].startswith("-- primary result")
    assert "FROM marketing_daily" in a.sql[0] and "Northeast" not in a.sql[0]


def test_explain_change_finds_the_creative_fatigue_campaign(engine):
    a = engine.ask("Why did paid social ROAS drop in June?")
    camps = next(t for t in a.tables if t.title.startswith("Where the change came from"))
    assert camps.rows[0]["name"] == "Social - Prospecting Lookalike"
    assert camps.rows[0]["share"] > 0.5
    factors = next(t for t in a.tables if t.title.startswith("Funnel steps"))
    assert max(factors.rows, key=lambda r: abs(r["share"]))["factor"] == "Click-through rate"
    # the change in the headline and the confidence interval agree in sign
    assert "worsened" in a.headline and "-" in a.narrative[0]


def test_explain_change_segment_shares_sum_to_one(engine):
    a = engine.ask("Why did display CAC go up in August?")
    camps = next(t for t in a.tables if t.title.startswith("Where the change came from"))
    assert sum(r["share"] for r in camps.rows) == pytest.approx(1.0, abs=1e-6)       # every display campaign is listed
    cpm = next(r for r in next(t for t in a.tables if t.title.startswith("Funnel steps")).rows if "CPM" in r["factor"])
    assert 25 < cpm["pct"] < 40


def test_anomaly_answer_localises_and_labels(engine):
    a = engine.ask("Any anomalies in revenue over the last 12 months?")
    rows = a.tables[0].rows
    assert any("Black Friday" in r["note"] for r in rows)
    assert any("data gap" in r["note"] for r in rows)
    assert any("Organic Search has no data" in w for w in a.warnings)


def test_data_gap_warning_follows_the_scope(engine):
    assert not any("no data for" in w for w in engine.ask("revenue for paid search in March").warnings)
    assert any("Organic Search has no data" in w for w in engine.ask("revenue by channel in March").warnings)


def test_forecast_refuses_ratios_and_warns_about_holidays(engine):
    a = engine.ask("Forecast CAC for next month")
    assert a.status == "refused"
    a = engine.ask("Forecast revenue for the next 8 weeks")
    assert a.status == "ok" and a.charts and a.charts[0].band_upper
    assert not any("Black Friday" in c for c in a.caveats)       # the window ends Nov 22, before Black Friday 2026 (Nov 27)


def test_holiday_warning_logic():
    from datetime import date
    from mktg_copilot.calendar import holiday_in_window
    assert holiday_in_window(date(2026, 11, 10), date(2026, 12, 1)) == "Black Friday weekend"
    assert holiday_in_window(date(2026, 9, 28), date(2026, 11, 22)) is None


def test_owned_channels_have_no_roas(engine):
    a = engine.ask("ROAS for email last month")
    assert a.status == "refused" and "media spend" in a.narrative[0]


def test_mixed_owned_and_paid_scope_drops_the_owned_channel(engine):
    a = engine.ask("ROAS for email and paid search last week")
    assert a.status == "ok" and any("left out" in x for x in a.assumptions)
    sql = a.sql[0]                                   # the plan keeps what was asked; the executed SQL shows the scope actually used
    assert "channel IN ('paid_search')" in sql and "email" not in sql


def test_mixed_metric_scope_is_stated(engine):
    a = engine.ask("revenue and roas by channel last week")
    assert any("limited to paid channels" in x for x in a.assumptions)


def test_definition_is_curated_not_computed(engine):
    a = engine.ask("What is CAC?")
    assert a.status == "ok" and a.tables == [] and "spend" in a.narrative[0].lower()


def test_sql_injection_text_cannot_reach_the_warehouse(engine):
    before = engine.con.execute("SELECT COUNT(*) FROM fact_daily").fetchone()[0]
    for q in ["revenue last week'; DROP TABLE fact_daily;--", "orders UNION SELECT * FROM dim_campaign", "revenue for channel = 'email' OR 1=1"]:
        a = engine.ask(q)
        assert all("DROP" not in s.upper() and "UNION" not in s.upper() for s in a.sql)
    assert engine.con.execute("SELECT COUNT(*) FROM fact_daily").fetchone()[0] == before


def test_invalid_plan_is_rejected_by_validation(engine):
    bad = QueryPlan(intent="summary", metrics=["profit"], period={"start": "2026-09-01", "end": "2026-09-02"})
    problems = engine.validate(bad)
    assert any("Unknown metric" in p for p in problems)
    out_of_range = QueryPlan(intent="summary", metrics=["revenue"], period={"start": "2019-01-01", "end": "2019-01-31"})
    assert any("outside the loaded data" in p for p in engine.validate(out_of_range))


def test_tampering_with_the_narrative_is_caught(engine):
    a = engine.ask("What was revenue last month?")
    evidence = [v for t in a.tables for r in t.rows for v in r.values() if isinstance(v, float)]
    assert verify([a.headline], evidence + [a.plan.period.days]).passed
    tampered = a.headline.replace("$", "$9")
    assert not verify([tampered], evidence).passed


def test_no_answer_contains_nan_or_infinity(engine):
    import json
    for q in engine.sample_questions():
        text = engine.ask(q).model_dump_json()
        assert "NaN" not in text and "Infinity" not in text
        json.loads(text)


def test_overview_cards(engine):
    ov = engine.overview()
    assert [c["key"] for c in ov["cards"]] == ["revenue", "orders", "roas", "cac", "cvr", "spend"]
    assert all(c["value"] is not None and len(c["spark"]) >= 8 for c in ov["cards"])


def test_anomaly_feed_contains_the_injected_events(engine):
    dates = " ".join(r["dates"] for r in engine.anomaly_feed())
    assert "Nov 28" in dates and "Feb 10" in dates and "Apr 9" in dates and "Mar 17" in dates
