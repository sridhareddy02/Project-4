"""Is a change bigger than normal day-to-day noise? A bootstrap over days."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..semantic import Catalog
from .decompose import eval_metric


@dataclass
class DeltaCI:
    delta_pct: float
    low: float
    high: float
    clear: bool               # the interval excludes zero


def _vec_metric(cat: Catalog, key: str, sums: np.ndarray, cols: list[str]) -> np.ndarray:
    """Vectorised metric over bootstrap draws; sums has shape (B, len(cols))."""
    ix = {c: i for i, c in enumerate(cols)}
    m = cat.metrics[key]
    if m.kind == "measure":
        return sums[:, ix[key]]
    den = sums[:, ix[m.denominator]]
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(den > 0, m.scale * sums[:, ix[m.numerator]] / den, np.nan)


def bootstrap_delta(cat: Catalog, key: str, baseline: pd.DataFrame, current: pd.DataFrame, per_day: bool = False,
                    draws: int = 1000, seed: int = 11) -> DeltaCI | None:
    """95% interval for the % change from `baseline` to `current` (positive means current is higher), resampling whole days.

    Both frames have one row per day and one column per measure. For additive measures over periods of
    different length, `per_day=True` compares daily averages instead of totals."""
    if len(baseline) < 3 or len(current) < 3:
        return None
    cols = [c for c in baseline.columns if c in cat.metrics]
    a, b = baseline[cols].to_numpy(float), current[cols].to_numpy(float)
    rng = np.random.default_rng(seed)
    i0 = rng.integers(0, len(a), size=(draws, len(a)))
    i1 = rng.integers(0, len(b), size=(draws, len(b)))
    s0, s1 = a[i0].sum(axis=1), b[i1].sum(axis=1)
    v0, v1 = _vec_metric(cat, key, s0, cols), _vec_metric(cat, key, s1, cols)
    if per_day and cat.metrics[key].additive:
        v0, v1 = v0 / len(a), v1 / len(b)
    with np.errstate(divide="ignore", invalid="ignore"):
        d = (v1 / v0 - 1.0) * 100.0
    d = d[np.isfinite(d)]
    if len(d) < draws * 0.8:
        return None
    lo, hi = np.percentile(d, [2.5, 97.5])
    t0 = eval_metric(cat, key, baseline[cols].sum())
    t1 = eval_metric(cat, key, current[cols].sum())
    if per_day and cat.metrics[key].additive:
        t0, t1 = t0 / len(a), t1 / len(b)
    point = (t1 / t0 - 1.0) * 100.0 if t0 else float("nan")
    return DeltaCI(float(point), float(lo), float(hi), bool(lo > 0 or hi < 0))
