import json

import httpx
import pytest

from mktg_copilot.engine import Engine
from mktg_copilot.nlu.llm import LLMPlanner

GOOD_PLAN = {"kind": "plan", "plan": {"intent": "summary", "metrics": ["roas"], "group_by": ["channel"],
                                       "period": {"start": "2026-09-21", "end": "2026-09-27", "label": "last week"}}}


def make_engine(db_path, replies):
    """An engine whose LLM planner is backed by a stub transport returning `replies` in order."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        r = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(r, int):
            return httpx.Response(r, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [{"message": {"content": r if isinstance(r, str) else json.dumps(r)}}]})

    eng = Engine(db_path)
    eng.llm = LLMPlanner(eng.cat, eng.retriever, eng.rules, eng.start, eng.end, api_key="test", transport=httpx.MockTransport(handler))
    return eng, calls


def test_valid_plan_is_used_and_executed_by_the_same_engine(db_path):
    eng, calls = make_engine(db_path, [GOOD_PLAN])
    a = eng.ask("how are channels returning on spend lately", planner="llm")
    assert a.status == "ok" and a.planner == "llm" and a.plan.metrics == ["roas"]
    assert "FROM marketing_daily" in a.sql[0] and a.grounding["passed"]
    assert len(calls) == 1 and calls[0]["temperature"] == 0
    assert "Catalog" in calls[0]["messages"][0]["content"]          # the model is given the vocabulary, not data
    assert "revenue" not in calls[0]["messages"][1]["content"]


def test_unknown_metric_is_rejected_then_retried_then_falls_back(db_path):
    bad = {"kind": "plan", "plan": {"intent": "summary", "metrics": ["profit"], "period": {"start": "2026-09-21", "end": "2026-09-27"}}}
    eng, calls = make_engine(db_path, [bad, bad])
    a = eng.ask("What was ROAS by channel last week?", planner="llm")
    assert len(calls) == 2 and "rejected" in calls[1]["messages"][-1]["content"]
    assert a.status == "ok" and a.plan.metrics == ["roas"]                 # rule-based plan took over
    assert any("rule-based planner was used" in w for w in a.warnings)


def test_second_attempt_can_succeed(db_path):
    eng, calls = make_engine(db_path, ["not json at all", GOOD_PLAN])
    a = eng.ask("roas by channel last week", planner="llm")
    assert len(calls) == 2 and a.status == "ok" and not any("rule-based" in w for w in a.warnings)


def test_smuggled_sql_field_is_dropped_and_never_executed(db_path):
    evil = {"kind": "plan", "sql": "DROP TABLE fact_daily", "plan": {**GOOD_PLAN["plan"], "sql": "DELETE FROM fact_daily", "raw_query": "DROP TABLE x"}}
    eng, _ = make_engine(db_path, [evil])
    a = eng.ask("roas by channel", planner="llm")
    assert a.status == "ok"
    assert all("DROP" not in s and "DELETE" not in s for s in a.sql)
    assert eng.con.execute("SELECT COUNT(*) FROM fact_daily").fetchone()[0] > 0


def test_out_of_range_dates_and_bad_dimensions_are_rejected(db_path):
    far = {"kind": "plan", "plan": {"intent": "summary", "metrics": ["revenue"], "period": {"start": "2019-01-01", "end": "2019-01-31"}}}
    dim = {"kind": "plan", "plan": {"intent": "summary", "metrics": ["revenue"], "group_by": ["sku"], "period": {"start": "2026-09-21", "end": "2026-09-27"}}}
    for reply in (far, dim):
        eng, calls = make_engine(db_path, [reply])
        a = eng.ask("revenue last week", planner="llm")
        assert len(calls) == 2 and any("rule-based" in w for w in a.warnings)


def test_model_may_refuse_or_ask_for_clarification(db_path):
    eng, _ = make_engine(db_path, [{"kind": "refuse", "message": "That needs cost data we do not have."}])
    a = eng.ask("what is our margin", planner="llm")
    assert a.status == "refused" and "cost data" in a.narrative[0]
    eng, _ = make_engine(db_path, [{"kind": "clarify", "message": "Which period?", "suggestions": ["Last week"]}])
    assert eng.ask("revenue", planner="llm").status == "clarify"


def test_http_failure_falls_back_without_raising(db_path):
    eng, calls = make_engine(db_path, [500])
    a = eng.ask("What was ROAS by channel last week?", planner="llm")
    assert a.status == "ok" and any("model call failed" in w for w in a.warnings)


def test_llm_is_off_unless_configured(db_path, monkeypatch):
    monkeypatch.delenv("COPILOT_LLM_API_KEY", raising=False)
    eng = Engine(db_path)
    assert eng.llm is None
    a = eng.ask("What was ROAS by channel last week?", planner="llm")
    assert a.status == "ok" and any("not configured" in w for w in a.warnings)
