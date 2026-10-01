"""Exact driver decomposition for ratio metrics.

Two views of the same change, both exact (they add back to the total, which the tests check):

1. Funnel factors. A ratio such as ROAS is a product of ratios that telescope,
   ROAS = (revenue/orders) x (orders/sessions) x (sessions/clicks) x (clicks/spend).
   On a log scale the change splits exactly into one term per factor (log-mean Divisia, "LMDI").

2. Segments. R = sum over segments of weight x rate, where weight is the segment's share of the denominator.
   Each segment's change in (weight x rate) splits at the midpoint into a mix effect (weight moved) and a
   rate effect (the segment got better or worse). The pieces sum to the change in R.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from ..semantic import Catalog


def eval_metric(cat: Catalog, key: str, sums: Mapping[str, float]) -> float:
    """Value of a metric from summed measures; NaN when the denominator is zero."""
    m = cat.metrics[key]
    if m.kind == "measure":
        return float(sums[key])
    den = float(sums[m.denominator])
    if den == 0:
        return float("nan")
    return m.scale * float(sums[m.numerator]) / den


@dataclass
class Factor:
    label: str
    numerator: str
    denominator: str
    before: float
    after: float
    pct_change: float          # after / before - 1
    log_share: float           # share of the total log change; shares sum to 1 when the metric moved
    display_scale: float = 1.0 # multiplies before/after for display only (for example 1000 for CPM)


def factor_decomposition(cat: Catalog, key: str, sums0: Mapping[str, float], sums1: Mapping[str, float]) -> list[Factor] | None:
    """None when the metric has no factor chain or a factor is zero/undefined in either period.

    A factor with no denominator is a level (for example the number of impressions) rather than a ratio."""
    chain = cat.drivers.get(key)
    if not chain:
        return None
    out: list[Factor] = []
    for num, den, label, scale in chain:
        n0, n1 = float(sums0[num]), float(sums1[num])
        d0, d1 = (float(sums0[den]), float(sums1[den])) if den else (1.0, 1.0)
        if min(d0, d1, n0, n1) <= 0:
            return None
        out.append(Factor(label, num, den or "", n0 / d0, n1 / d1, (n1 / d1) / (n0 / d0) - 1, 0.0, float(scale)))
    total_log = sum(math.log(f.after / f.before) for f in out)
    for f in out:
        f.log_share = math.log(f.after / f.before) / total_log if abs(total_log) > 1e-12 else 0.0
    return out


@dataclass
class SegmentEffect:
    segment: str
    before: float | None
    after: float | None
    weight_before: float
    weight_after: float
    contribution: float        # change in this segment's weight x rate (ratio metrics) or its raw change (additive)
    mix: float
    rate: float


def segment_decomposition(cat: Catalog, key: str, seg0: pd.DataFrame, seg1: pd.DataFrame) -> list[SegmentEffect]:
    """seg0 and seg1 are indexed by segment and hold the summed measures for each period."""
    m = cat.metrics[key]
    segs = sorted(set(seg0.index) | set(seg1.index))
    z = pd.Series(0.0, index=list(cat.metrics.keys() & set(seg0.columns) | set(seg1.columns)))

    def row(df: pd.DataFrame, s: str) -> pd.Series:
        return df.loc[s] if s in df.index else z.reindex(df.columns).fillna(0.0)

    out: list[SegmentEffect] = []
    if m.kind == "measure":
        for s in segs:
            a, b = float(row(seg0, s)[key]), float(row(seg1, s)[key])
            out.append(SegmentEffect(s, a, b, 0.0, 0.0, b - a, 0.0, b - a))
        return out

    D0 = float(seg0[m.denominator].sum())
    D1 = float(seg1[m.denominator].sum())
    for s in segs:
        r0_, r1_ = row(seg0, s), row(seg1, s)
        d0, d1 = float(r0_[m.denominator]), float(r1_[m.denominator])
        n0, n1 = float(r0_[m.numerator]), float(r1_[m.numerator])
        w0 = d0 / D0 if D0 else 0.0
        w1 = d1 / D1 if D1 else 0.0
        rate0 = m.scale * n0 / d0 if d0 else None
        rate1 = m.scale * n1 / d1 if d1 else None
        # A segment with no denominator in one period has no rate there; treat the missing rate as unchanged.
        ra = rate0 if rate0 is not None else rate1
        rb = rate1 if rate1 is not None else rate0
        if ra is None:
            out.append(SegmentEffect(s, None, None, w0, w1, 0.0, 0.0, 0.0))
            continue
        mix = (w1 - w0) * (ra + rb) / 2.0
        rate = (rb - ra) * (w0 + w1) / 2.0
        out.append(SegmentEffect(s, rate0, rate1, w0, w1, w1 * rb - w0 * ra, mix, rate))
    return out
