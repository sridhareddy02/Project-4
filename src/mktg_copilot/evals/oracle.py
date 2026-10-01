"""Independent pandas implementation of the metrics, used to grade the engine's numbers.

It deliberately does not share code, formulas or SQL with the engine: the formulas below are written out again
by hand, so a mistake in the semantic layer's YAML or the SQL compiler shows up as a disagreement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FORMULAS = {
    "roas": ("revenue", "spend", 1), "cac": ("spend", "new_customers", 1), "cpa": ("spend", "orders", 1),
    "cpc": ("spend", "clicks", 1), "cpm": ("spend", "impressions", 1000), "ctr": ("clicks", "impressions", 1),
    "cvr": ("orders", "sessions", 1), "aov": ("revenue", "orders", 1), "rps": ("revenue", "sessions", 1),
}
SPEND_BASED = {"roas", "cac", "cpa", "cpc", "cpm"}
BASE = ["impressions", "clicks", "spend", "sessions", "orders", "revenue", "new_customers"]


def value(sums: pd.Series, metric: str) -> float:
    if metric in FORMULAS:
        n, d, k = FORMULAS[metric]
        return float("nan") if sums[d] == 0 else k * sums[n] / sums[d]
    return float(sums[metric])


def compute(df: pd.DataFrame, metric: str, period: tuple[str, str], filters: dict | None = None,
            group_by: list[str] | None = None, grain: str | None = None) -> dict | float:
    """Returns a scalar, or {group key (or tuple): value}, or {period: value} when grain is set."""
    filters = filters or {}
    d = df[(df["date"] >= period[0]) & (df["date"] <= period[1])]
    for dim, vals in filters.items():
        d = d[d[dim].isin(vals)]
    if metric in SPEND_BASED and not any(k in filters for k in ("channel", "channel_group", "campaign")):
        d = d[d["channel_group"] == "paid"]
    keys = list(group_by or [])
    if grain:
        d = d.assign(period={"day": d["date"], "week": d["week_start"], "month": d["month_start"]}[grain])
        keys = ["period"] + keys
    if not keys:
        return value(d[BASE].sum(), metric)
    out = {}
    for k, g in d.groupby(keys):
        out[k] = value(g[BASE].sum(), metric)
    return out
