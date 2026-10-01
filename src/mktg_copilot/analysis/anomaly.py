"""Anomaly detection: robust seasonal decomposition, a robust z-score, and incident grouping."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL

from ..calendar import event_context

# Calibrated on event-free synthetic data from seeds the project does not use elsewhere: a plain 4-sigma rule raised
# about 18 false incidents per series per year because the residuals are heavy-tailed; requiring a robust z of 8
# AND a 15% deviation brings that to about 0.2 while every injected event (z from 13 to 104) is still flagged.
THRESHOLD = 8.0
MIN_DEVIATION = 0.15
MIN_HISTORY = 28


@dataclass
class Incident:
    metric: str
    start: date
    end: date
    direction: str                 # up | down
    peak_z: float
    observed: float                # mean over the incident days
    expected: float
    deviation_pct: float
    context: str | None = None
    where: list[tuple[str, float]] = field(default_factory=list)    # (segment label, share of deviation)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _log(x: np.ndarray) -> np.ndarray:
    pos = x[x > 0]
    eps = 0.01 * float(np.median(pos)) if len(pos) else 1.0
    return np.log(x + eps)


def score_series(values: pd.Series) -> pd.DataFrame:
    """values: daily series indexed by date. Returns expected, residual z and a flag per day."""
    y = values.astype(float)
    idx = pd.to_datetime(y.index)
    y.index = idx
    full = pd.date_range(idx.min(), idx.max(), freq="D")
    y = y.reindex(full)
    ly = pd.Series(_log(y.fillna(y.median()).to_numpy()), index=full)
    res = STL(ly, period=7, seasonal=7, robust=True).fit()
    fitted = res.trend + res.seasonal
    resid = res.resid
    mad = float(np.median(np.abs(resid - np.median(resid))))
    scale = 1.4826 * mad if mad > 0 else float(np.std(resid)) or 1.0
    z = resid / scale
    eps = 0.01 * float(np.median(y[y > 0])) if (y > 0).any() else 1.0
    expected = np.exp(fitted) - eps
    out = pd.DataFrame({"actual": y, "expected": expected, "z": z})
    with np.errstate(divide="ignore", invalid="ignore"):
        deviation = (out["actual"] / out["expected"] - 1.0).abs()
    out["flag"] = (out["z"].abs() > THRESHOLD) & (deviation > MIN_DEVIATION)
    out.loc[y.isna(), "flag"] = False
    return out


def find_incidents(metric: str, scored: pd.DataFrame, max_gap: int = 1) -> list[Incident]:
    flagged = scored.index[scored["flag"]]
    if len(flagged) == 0:
        return []
    groups: list[list[pd.Timestamp]] = [[flagged[0]]]
    for d in flagged[1:]:
        if (d - groups[-1][-1]).days <= max_gap + 1 and np.sign(scored.at[d, "z"]) == np.sign(scored.at[groups[-1][-1], "z"]):
            groups[-1].append(d)
        else:
            groups.append([d])
    out = []
    for g in groups:
        sub = scored.loc[g[0]:g[-1]]
        sub = sub[sub["flag"]]
        obs, exp = float(sub["actual"].mean()), float(sub["expected"].mean())
        z_peak = float(sub["z"].abs().max())
        direction = "up" if sub["z"].mean() > 0 else "down"
        dev = (obs / exp - 1.0) * 100.0 if exp else float("nan")
        start, end = g[0].date(), g[-1].date()
        out.append(Incident(metric, start, end, direction, z_peak, obs, exp, dev, event_context(start, end)))
    return out


def detect(metric: str, values: pd.Series) -> tuple[pd.DataFrame, list[Incident]]:
    if len(values.dropna()) < MIN_HISTORY:
        raise ValueError(f"need at least {MIN_HISTORY} days of history to detect anomalies")
    scored = score_series(values)
    return scored, find_incidents(metric, scored)


def window_baseline(start: date, days: int = 28) -> tuple[date, date]:
    """The `days` days immediately before an incident, used as its normal level."""
    return start - timedelta(days=days), start - timedelta(days=1)
