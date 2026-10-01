"""Runs every evaluation suite and writes reports/eval.json and reports/eval.md."""
from __future__ import annotations

import json
import math
import statistics
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

from .. import config
from ..analysis import anomaly as an
from ..data.spec import EVENTS
from ..data.warehouse import build_warehouse, read_sql
from ..engine import Engine
from ..plan import Answer, Filter
from . import oracle
from .questions import ADVERSARIAL, DEV, HELDOUT, Q

THRESHOLDS = {
    "dev_plan_accuracy": 0.90,
    "heldout_plan_accuracy": 0.70,
    "numeric_accuracy": 0.99,
    "adversarial_accuracy": 0.95,
    "grounding_pass_rate": 1.0,
    "events_recovered": 6,
    "false_incidents_per_series_year": 0.5,
}


# ----------------------------------------------------------------------------------------------------------
# plan scoring
# ----------------------------------------------------------------------------------------------------------
def _plan_view(a: Answer) -> dict:
    p = a.plan
    if p is None:
        return {"status": a.status}
    return {
        "status": a.status, "intent": p.intent, "metrics": sorted(p.metrics), "group_by": list(p.group_by),
        "filters": {f.dimension: sorted(f.values) for f in p.filters},
        "period": (p.period.start.isoformat(), p.period.end.isoformat()) if p.period else None,
        "compare": (p.compare_to.start.isoformat(), p.compare_to.end.isoformat()) if p.compare_to else None,
        "top_n": p.top_n, "order": p.order, "grain": p.grain, "horizon": p.horizon_days, "subject": p.subject or (p.metrics[0] if p.metrics else None),
    }


def _expected_view(q: Q) -> dict:
    return {"intent": q.intent, "metrics": sorted(q.metrics), "group_by": list(q.group_by),
            "filters": {k: sorted(v) for k, v in q.filters.items()}, "period": q.period, "compare": q.compare,
            "top_n": q.top_n, "order": q.order, "grain": q.grain, "horizon": q.horizon, "subject": q.subject}


def _diff(q: Q, got: dict) -> list[str]:
    exp, bad = _expected_view(q), []
    if got.get("status") != "ok" and "intent" not in got:
        return [f"status={got.get('status')}"]
    for k, v in exp.items():
        if k in ("top_n", "order", "grain", "horizon", "subject") and v is None:
            continue
        if k == "metrics" and q.intent == "definition" and not v:
            continue
        if k == "period" and q.intent == "definition":
            continue
        if got.get(k) != v:
            bad.append(f"{k}: expected {v}, got {got.get(k)}")
    return bad


def score_plans(engine: Engine, questions, label: str) -> tuple[dict, list[Answer]]:
    answers, fails = [], []
    field_ok, field_n = Counter(), Counter()
    intent_ok = exact_ok = 0
    for q in questions:
        a = engine.ask(q.q)
        answers.append(a)
        got = _plan_view(a)
        diffs = _diff(q, got)
        exp = _expected_view(q)
        for k, v in exp.items():
            if k in ("top_n", "order", "grain", "horizon", "subject") and v is None:
                continue
            if k == "period" and q.intent == "definition":
                continue
            if k == "metrics" and q.intent == "definition" and not v:
                continue
            field_n[k] += 1
            field_ok[k] += int(got.get(k) == v)
        intent_ok += int(got.get("intent") == q.intent)
        exact_ok += int(not diffs)
        if diffs:
            fails.append({"question": q.q, "problems": diffs})
    n = len(list(questions))
    return {
        "n": n, "intent_accuracy": round(intent_ok / n, 3), "plan_accuracy": round(exact_ok / n, 3),
        "field_accuracy": {k: round(field_ok[k] / field_n[k], 3) for k in field_n}, "failures": fails,
    }, answers


# ----------------------------------------------------------------------------------------------------------
# numeric scoring against the pandas oracle
# ----------------------------------------------------------------------------------------------------------
def _close(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return (a is None or (isinstance(a, float) and math.isnan(a))) and (b is None or (isinstance(b, float) and math.isnan(b)))
    return abs(a - b) <= tol * max(1.0, abs(b))


def check_numbers(engine: Engine, df: pd.DataFrame, q: Q, a: Answer) -> tuple[bool, str]:
    if a.status != "ok" or not a.tables or a.plan is None:
        return False, f"status {a.status}"
    cat = engine.cat
    plan, table = a.plan, a.tables[0]
    filt = {f.dimension: f.values for f in plan.filters}
    mets = plan.metrics
    # the oracle applies the same business rule as the engine (spend-based metrics are paid-only) from its own code
    try:
        if plan.intent == "summary" and not plan.group_by:
            for r in table.rows:
                m = r["_metric"]
                exp = oracle.compute(df, m, (plan.period.start.isoformat(), plan.period.end.isoformat()), filt)
                if not _close(r["value"], exp):
                    return False, f"{m}: got {r['value']}, oracle {exp}"
            return True, ""
        if plan.intent == "summary":
            exp = oracle.compute(df, mets[0], (plan.period.start.isoformat(), plan.period.end.isoformat()), filt, plan.group_by)
            got = {}
            for r in table.rows:
                got[tuple(r[g] for g in plan.group_by)] = r
            for k, v in exp.items():
                key = tuple(cat.dim_label(g, x) for g, x in zip(plan.group_by, k if isinstance(k, tuple) else (k,)))
                if math.isnan(v):
                    continue
                if key not in got:
                    return False, f"missing group {key}"
                for m in mets:
                    e2 = oracle.compute(df, m, (plan.period.start.isoformat(), plan.period.end.isoformat()), filt, plan.group_by)[k]
                    if not _close(got[key][m], e2):
                        return False, f"{key} {m}: got {got[key][m]}, oracle {e2}"
            return True, ""
        if plan.intent == "rank":
            dim = plan.group_by[0]
            exp = oracle.compute(df, mets[0], (plan.period.start.isoformat(), plan.period.end.isoformat()), filt, [dim])
            exp = {cat.dim_label(dim, k if not isinstance(k, tuple) else k[0]): v for k, v in exp.items() if not math.isnan(v)}
            ordered = sorted(exp.items(), key=lambda kv: kv[1], reverse=(plan.order != "asc"))
            n = len(table.rows)
            for i, r in enumerate(table.rows):
                tied = {name for name, v in ordered if _close(v, ordered[i][1])}   # equal values may appear in either order
                if not _close(r[mets[0]], ordered[i][1]) or r["name"] not in tied:
                    return False, f"rank {i + 1}: got {r['name']} {r[mets[0]]}, oracle {ordered[i]}"
            return True, ""
        if plan.intent == "trend":
            ex = oracle.compute(df, mets[0], (plan.period.start.isoformat(), plan.period.end.isoformat()), filt, None, plan.grain)
            for r in table.rows:
                e = ex.get((r["period"],), ex.get(r["period"]))
                if e is None or not _close(r[mets[0]], e):
                    return False, f"{r['period']}: got {r[mets[0]]}, oracle {e}"
            return True, ""
        if plan.intent == "compare":
            m = mets[0]
            p1 = (plan.period.start.isoformat(), plan.period.end.isoformat())
            p0 = (plan.compare_to.start.isoformat(), plan.compare_to.end.isoformat())
            per_day = plan.period.days != plan.compare_to.days and cat.metrics[m].additive
            grouped = bool(plan.group_by)
            e1 = oracle.compute(df, m, p1, filt, plan.group_by or None)
            e0 = oracle.compute(df, m, p0, filt, plan.group_by or None)
            for r in table.rows:
                if grouped:
                    k = next((k for k in e1 if cat.dim_label(plan.group_by[0], k if not isinstance(k, tuple) else k[0]) == r["name"]), None)
                    a1, a0 = e1[k], e0.get(k)
                else:
                    a1, a0 = e1, e0
                if per_day:
                    a1, a0 = a1 / plan.period.days, a0 / plan.compare_to.days
                if not _close(r["current"], a1) or not _close(r["baseline"], a0):
                    return False, f"{r['name']}: got {r['current']}/{r['baseline']}, oracle {a1}/{a0}"
            return True, ""
    except Exception as e:  # a crash in grading is a failure to report, not to hide
        return False, f"{type(e).__name__}: {e}"
    return True, ""


# ----------------------------------------------------------------------------------------------------------
# adversarial
# ----------------------------------------------------------------------------------------------------------
def score_adversarial(engine: Engine) -> dict:
    rows_before = engine.con.execute("SELECT COUNT(*) FROM fact_daily").fetchone()[0]
    ok, fails, n_ref = 0, [], 0
    for item in ADVERSARIAL:
        a = engine.ask(item.q)
        status = a.status
        good = {
            "refused": status == "refused",
            "clarify": status == "clarify",
            "refused_or_clarify": status in ("refused", "clarify"),
            "ok_with_warning": status == "ok" and bool(a.warnings),
        }[item.expect]
        ok += int(good)
        if not good:
            fails.append({"question": item.q, "expected": item.expect, "got": status, "note": item.note})
    rows_after = engine.con.execute("SELECT COUNT(*) FROM fact_daily").fetchone()[0]
    tables = {r[0] for r in engine.con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    return {"n": len(ADVERSARIAL), "accuracy": round(ok / len(ADVERSARIAL), 3), "failures": fails,
            "warehouse_intact": rows_before == rows_after and {"fact_daily", "dim_campaign", "dim_date", "marketing_daily"} <= tables}


# ----------------------------------------------------------------------------------------------------------
# injected-event recovery and false alarms
# ----------------------------------------------------------------------------------------------------------
def _overlaps(row_dates: str, start: str, end: str, engine: Engine) -> bool:
    # rows carry a human date string; recompute overlap from the incident feed instead
    return False


def _incident_rows(a: Answer) -> list[dict]:
    t = next((t for t in a.tables if t.title.startswith("Incidents")), None)
    return [r for r in (t.rows if t else []) if "metric" in r]


def recover_events(engine: Engine) -> list[dict]:
    from ..nlu.timeparse import describe
    out = []
    ev = {e.event_id: e for e in EVENTS}

    def overlap(row, e) -> bool:
        s, en = date.fromisoformat(e.start), date.fromisoformat(e.end)
        return row["dates"] == describe(s, en) or describe(s, s) in row["dates"] or describe(en, en) in row["dates"]

    # E1: tracking break in paid search
    a = engine.ask("Any anomalies in orders for paid search over the last 12 months?")
    rows = [r for r in _incident_rows(a) if overlap(r, ev["E1"])]
    out.append({"event": "E1", "kind": "tracking break (paid search)", "question": a.question,
                "found": bool(rows) and rows[0]["direction"] == "down" and "Search - " in rows[0]["where"],   # scope is the channel, so "where" names its campaigns
                "detail": (f"{rows[0]['metric']} {rows[0]['deviation']:+.0f}% on {rows[0]['dates']}, where: {rows[0]['where']}" if rows else "not flagged")})
    # E3: Black Friday peak, recognised as a known retail date
    a = engine.ask("Any anomalies in revenue over the last 12 months?")
    rows = [r for r in _incident_rows(a) if overlap(r, ev["E3"])]
    out.append({"event": "E3", "kind": "Black Friday weekend peak", "question": a.question,
                "found": bool(rows) and rows[0]["direction"] == "up" and "Black Friday" in rows[0]["note"],
                "detail": (f"{rows[0]['metric']} {rows[0]['deviation']:+.0f}% on {rows[0]['dates']}; note: {rows[0]['note']}" if rows else "not flagged")})
    # E5: one day of quadrupled spend, localised to the campaign
    a = engine.ask("Any anomalies in spend for paid search over the last 12 months?")
    rows = [r for r in _incident_rows(a) if overlap(r, ev["E5"])]
    out.append({"event": "E5", "kind": "pacing bug (one day of 4x spend)", "question": a.question,
                "found": bool(rows) and rows[0]["direction"] == "up" and "Non-brand Category" in rows[0]["where"],
                "detail": (f"{rows[0]['metric']} {rows[0]['deviation']:+.0f}% on {rows[0]['dates']}, where: {rows[0]['where']}" if rows else "not flagged")})
    # E6: missing rows, reported as a data gap rather than a drop
    a = engine.ask("Any anomalies in revenue over the last 12 months?")
    gap_warning = any("Organic Search has no data for Mar 17 to Mar 18, 2026" in w for w in a.warnings)
    rows = [r for r in _incident_rows(a) if overlap(r, ev["E6"])]
    out.append({"event": "E6", "kind": "ingestion gap (organic search rows missing)", "question": a.question,
                "found": gap_warning and bool(rows) and "data gap" in rows[0]["note"],
                "detail": ("warned about the gap and labelled the incident as a probable data gap" if gap_warning else "no gap warning")})
    # E2: creative fatigue on one campaign, found by driver analysis
    a = engine.ask("Why did paid social ROAS drop in June?")
    factors = next(t for t in a.tables if t.title.startswith("Funnel steps"))
    camps = next(t for t in a.tables if t.title.startswith("Where the change came from"))
    top_factor = max(factors.rows, key=lambda r: abs(r["share"]))["factor"]
    top_camp = camps.rows[0]["name"]
    out.append({"event": "E2", "kind": "creative fatigue (Social - Prospecting Lookalike)", "question": a.question,
                "found": top_camp == "Social - Prospecting Lookalike" and top_factor in ("Click-through rate", "Conversion rate"),
                "detail": f"largest funnel step: {top_factor}; leading campaign: {top_camp}"})
    # E4: auction pressure on display, found as a CPM move
    a = engine.ask("Why did display CAC go up in August?")
    factors = next(t for t in a.tables if t.title.startswith("Funnel steps"))
    top_factor = max(factors.rows, key=lambda r: abs(r["share"]))
    out.append({"event": "E4", "kind": "display CPM inflation (+35%)", "question": a.question,
                "found": "CPM" in top_factor["factor"] and top_factor["pct"] > 0,
                "detail": f"largest funnel step: {top_factor['factor']} ({top_factor['pct']:+.1f}%)"})
    return out


def false_alarms() -> dict:
    """Run the anomaly detector over event-free data from a different seed and count incidents."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "clean.db"
        build_warehouse(db, seed=99, events=False)
        eng = Engine(db)
        scopes = [[]] + [[Filter(dimension="channel", values=[c])] for c in eng.cat.dim_values("channel")]
        series, incidents = 0, []
        for filt in scopes:
            chans = {f.values[0] for f in filt} or set()
            paid = not chans or chans <= {"paid_search", "paid_social", "display"}
            for m in ("revenue", "orders", "sessions", "clicks", "cvr", "aov", "ctr", "spend", "roas", "cpc", "cac"):
                if eng.cat.metrics[m].requires_spend and not paid:
                    continue
                if m == "spend" and not paid:
                    continue
                f2 = filt or ([Filter(dimension="channel_group", values=["paid"])] if eng.cat.metrics[m].requires_spend else [])
                try:
                    _, incs = an.detect(m, eng.series(None, m, f2))
                except ValueError:
                    continue
                series += 1
                incidents += [(m, f2[0].values[0] if f2 else "all", str(i.start)) for i in incs]
        return {"series": series, "incidents": len(incidents), "per_series_year": round(len(incidents) / series, 3), "examples": incidents[:5]}


# ----------------------------------------------------------------------------------------------------------
def run_all(check: bool = False) -> int:
    engine = Engine()
    df = read_sql(engine.con, "SELECT * FROM marketing_daily")
    result: dict = {"generated": date.today().isoformat(), "data": {"start": engine.start.isoformat(), "end": engine.end.isoformat(), "rows": int(len(df))}}

    dev, dev_answers = score_plans(engine, DEV, "dev")
    held, held_answers = score_plans(engine, HELDOUT, "heldout")
    result["plans"] = {"dev": dev, "heldout": held}

    # numbers against the oracle, over every answerable question whose result is a table of values
    checked = ok = 0
    num_fail = []
    for q, a in list(zip(DEV, dev_answers)) + list(zip(HELDOUT, held_answers)):
        if not q.check_numbers or q.intent == "explain_change":
            continue
        checked += 1
        good, why = check_numbers(engine, df, q, a)
        ok += int(good)
        if not good:
            num_fail.append({"question": q.q, "problem": why})
    result["numbers"] = {"checked": checked, "correct": ok, "accuracy": round(ok / checked, 3), "failures": num_fail}

    result["adversarial"] = score_adversarial(engine)

    answered = [a for a in dev_answers + held_answers if a.status == "ok"]
    g_checked = sum(a.grounding.get("checked", 0) for a in answered)
    g_matched = sum(a.grounding.get("matched", 0) for a in answered)
    passed = sum(1 for a in answered if a.grounding.get("passed"))
    result["grounding"] = {"answers": len(answered), "answers_passed": passed, "pass_rate": round(passed / len(answered), 3),
                           "numbers_checked": g_checked, "numbers_traced": g_matched,
                           "failures": [{"question": a.question, "unmatched": a.grounding.get("unmatched"), "flags": a.grounding.get("hedging_flags")} for a in answered if not a.grounding.get("passed")]}

    result["events"] = recover_events(engine)
    result["false_alarms"] = false_alarms()
    lat = sorted(a.timings_ms.get("total", 0.0) for a in dev_answers + held_answers if a.timings_ms)
    result["latency_ms"] = {"p50": round(statistics.median(lat), 1), "p95": round(lat[int(0.95 * (len(lat) - 1))], 1), "max": round(lat[-1], 1)}

    gates = {
        "dev_plan_accuracy": dev["plan_accuracy"] >= THRESHOLDS["dev_plan_accuracy"],
        "heldout_plan_accuracy": held["plan_accuracy"] >= THRESHOLDS["heldout_plan_accuracy"],
        "numeric_accuracy": result["numbers"]["accuracy"] >= THRESHOLDS["numeric_accuracy"],
        "adversarial_accuracy": result["adversarial"]["accuracy"] >= THRESHOLDS["adversarial_accuracy"] and result["adversarial"]["warehouse_intact"],
        "grounding_pass_rate": result["grounding"]["pass_rate"] >= THRESHOLDS["grounding_pass_rate"],
        "events_recovered": sum(e["found"] for e in result["events"]) >= THRESHOLDS["events_recovered"],
        "false_incidents_per_series_year": result["false_alarms"]["per_series_year"] <= THRESHOLDS["false_incidents_per_series_year"],
    }
    result["thresholds"] = THRESHOLDS
    result["gates"] = gates

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORTS_DIR / "eval.json").write_text(json.dumps(result, indent=2, default=str))
    (config.REPORTS_DIR / "eval.md").write_text(render_markdown(result))
    print(render_markdown(result))
    if check and not all(gates.values()):
        print("QUALITY GATE FAILED:", [k for k, v in gates.items() if not v])
        return 1
    return 0


def render_markdown(r: dict) -> str:
    p = r["plans"]
    L = [f"# Evaluation report", "", f"Data: {r['data']['rows']:,} rows, {r['data']['start']} to {r['data']['end']} (synthetic).", "",
         "| Suite | Result | Gate |", "|---|---|---|"]
    g = r["gates"]
    L.append(f"| Plan accuracy, dev set ({p['dev']['n']} questions) | {p['dev']['plan_accuracy']:.0%} exact, {p['dev']['intent_accuracy']:.0%} intent | {'pass' if g['dev_plan_accuracy'] else 'FAIL'} |")
    L.append(f"| Plan accuracy, held-out set ({p['heldout']['n']} questions) | {p['heldout']['plan_accuracy']:.0%} exact, {p['heldout']['intent_accuracy']:.0%} intent | {'pass' if g['heldout_plan_accuracy'] else 'FAIL'} |")
    n = r["numbers"]
    L.append(f"| Numbers versus an independent pandas oracle | {n['correct']} of {n['checked']} answers match | {'pass' if g['numeric_accuracy'] else 'FAIL'} |")
    a = r["adversarial"]
    L.append(f"| Refusals, clarifications and injection safety ({a['n']} questions) | {a['accuracy']:.0%}; warehouse intact: {a['warehouse_intact']} | {'pass' if g['adversarial_accuracy'] else 'FAIL'} |")
    gr = r["grounding"]
    L.append(f"| Grounding: every number in the prose traced to data | {gr['answers_passed']} of {gr['answers']} answers; {gr['numbers_traced']} of {gr['numbers_checked']} numbers | {'pass' if g['grounding_pass_rate'] else 'FAIL'} |")
    ev = r["events"]
    L.append(f"| Injected events found without being told ({len(ev)}) | {sum(e['found'] for e in ev)} of {len(ev)} | {'pass' if g['events_recovered'] else 'FAIL'} |")
    fa = r["false_alarms"]
    L.append(f"| False incidents on event-free data | {fa['incidents']} over {fa['series']} series, {fa['per_series_year']} per series-year | {'pass' if g['false_incidents_per_series_year'] else 'FAIL'} |")
    L.append(f"| Latency per question | p50 {r['latency_ms']['p50']} ms, p95 {r['latency_ms']['p95']} ms | n/a |")
    L += ["", "## Injected events", "", "| Event | What was injected | Found | Detail |", "|---|---|---|---|"]
    for e in ev:
        L.append(f"| {e['event']} | {e['kind']} | {'yes' if e['found'] else 'NO'} | {e['detail']} |")
    for name, s in (("Dev set failures", p["dev"]), ("Held-out set failures", p["heldout"])):
        L += ["", f"## {name}", ""]
        L += [f"* `{f['question']}`: " + "; ".join(f["problems"]) for f in s["failures"]] or ["None."]
        if not s["failures"]:
            L[-1] = "None."
    L += ["", "## Number mismatches", ""] + ([f"* `{f['question']}`: {f['problem']}" for f in n["failures"]] or ["None."])
    L += ["", "## Adversarial misses", ""] + ([f"* `{f['question']}` expected {f['expected']}, got {f['got']}" for f in a["failures"]] or ["None."])
    L += ["", "## Grounding failures", ""] + ([f"* `{f['question']}`: unmatched {f['unmatched']} flags {f['flags']}" for f in gr["failures"]] or ["None."])
    return "\n".join(L) + "\n"
