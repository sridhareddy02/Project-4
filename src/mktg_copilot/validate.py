"""The allowlist between a proposed plan (from any planner) and the warehouse."""
from __future__ import annotations

from datetime import date

from .plan import QueryPlan
from .semantic import Catalog

NEEDS_METRIC = ("summary", "compare", "explain_change", "trend", "rank", "anomaly", "forecast")


def validate_plan(plan: QueryPlan, cat: Catalog, start: date, end: date) -> list[str]:
    out: list[str] = []
    for m in plan.metrics:
        if m not in cat.metrics:
            out.append(f"Unknown metric {m!r}. Available: {', '.join(cat.metrics)}.")
    for g in plan.group_by:
        if g not in cat.dimensions:
            out.append(f"Unknown dimension {g!r}.")
    for f in plan.filters:
        if f.dimension not in cat.dimensions:
            out.append(f"Unknown filter dimension {f.dimension!r}.")
            continue
        bad = [v for v in f.values if v not in cat.dimensions[f.dimension].values]
        if bad:
            out.append(f"Unknown {f.dimension} value(s): {bad}.")
    for label, r in (("period", plan.period), ("comparison period", plan.compare_to)):
        if r is not None and (r.start < start or r.end > end):
            out.append(f"The {label} {r.start} to {r.end} is outside the loaded data ({start} to {end}).")
    if plan.intent in NEEDS_METRIC and not plan.metrics:
        out.append("The plan names no metric.")
    if plan.intent in NEEDS_METRIC and plan.period is None:
        out.append("The plan has no period.")
    if plan.intent in ("compare", "explain_change") and plan.compare_to is None:
        out.append("A comparison needs two periods.")
    if plan.intent == "rank" and not plan.group_by:
        out.append("A ranking needs a dimension to rank.")
    if plan.intent == "definition" and not (plan.subject or plan.metrics):
        out.append("A definition question needs a subject.")
    return out
