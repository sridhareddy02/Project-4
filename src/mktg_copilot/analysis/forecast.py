"""Forecasting: exponential smoothing with a weekly pattern, with intervals taken from rolling-origin backtests.

The model's own prediction intervals proved far too wide on this data (100% coverage of an 80% interval), so
intervals here are empirical: the model is re-fitted at many past origins, and the spread of actual/forecast
ratios at the same horizon sets the 80% band. A seasonal-naive baseline is scored on the latest 28 days.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from statsmodels.tsa.exponential_smoothing.ets import ETSModel

HOLDOUT = 28
MAX_HORIZON = 56
MIN_TRAIN = 112
ORIGIN_STEP = 7


@dataclass
class Forecast:
    dates: list[date]
    mean: list[float]
    lower: list[float]
    upper: list[float]
    backtest_mape: float             # latest 28 days, forecast made 28 days earlier
    naive_mape: float                # seasonal-naive on the same days
    origins: int                     # past forecasts used to calibrate the band
    interval_coverage: float         # share of those past daily outcomes inside the 80% band (in-sample by construction)
    total_mean: float
    total_lower: float
    total_upper: float
    weekly: list[tuple[date, date, float, float, float]]


def _fit(y: pd.Series):
    model = ETSModel(y, error="add", trend="add", damped_trend=True, seasonal="mul", seasonal_periods=7)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.fit(disp=False, maxiter=200)


def _point(y: pd.Series, steps: int) -> np.ndarray:
    return _fit(y).forecast(steps).to_numpy()


def mape(actual: np.ndarray, pred: np.ndarray) -> float:
    actual, pred = np.asarray(actual, float), np.asarray(pred, float)
    m = actual != 0
    return float(np.mean(np.abs((actual[m] - pred[m]) / actual[m])) * 100.0)


def _prepare(values: pd.Series) -> pd.Series:
    y = values.astype(float).asfreq("D").interpolate(limit_direction="both")
    if (y <= 0).any():
        y = y.clip(lower=max(float(y[y > 0].min()), 1e-6))
    return y


def forecast_series(values: pd.Series, horizon: int) -> Forecast:
    """values: daily series with a DatetimeIndex. Only the additive measures are forecast; ratios are not."""
    if horizon < 1 or horizon > MAX_HORIZON:
        raise ValueError(f"horizon must be between 1 and {MAX_HORIZON} days")
    y = _prepare(values)
    T = len(y)
    if T < MIN_TRAIN + MAX_HORIZON:
        raise ValueError("not enough history to forecast")

    # 1. headline accuracy: forecast the latest 28 days from the data before them
    train, test = y.iloc[:-HOLDOUT], y.iloc[-HOLDOUT:]
    pred = _point(train, HOLDOUT)
    naive = np.tile(train.iloc[-7:].to_numpy(), HOLDOUT // 7 + 1)[:HOLDOUT]

    # 2. calibrate the band from rolling origins at this horizon
    day_ratio, week_ratio, total_ratio = [], [], []
    origins = list(range(T - horizon, MIN_TRAIN - 1, -ORIGIN_STEP))
    for o in origins:
        f = _point(y.iloc[:o], horizon)
        a = y.iloc[o:o + horizon].to_numpy()
        day_ratio.extend((a / f).tolist())
        total_ratio.append(a.sum() / f.sum())
        for i in range(0, horizon - 6, 7):
            week_ratio.append(a[i:i + 7].sum() / f[i:i + 7].sum())

    def q(arr):
        return float(np.percentile(arr, 10)), float(np.percentile(arr, 90))

    d_lo, d_hi = q(day_ratio)
    w_lo, w_hi = q(week_ratio) if week_ratio else (d_lo, d_hi)
    t_lo, t_hi = q(total_ratio)
    coverage = float(np.mean([(r >= d_lo) and (r <= d_hi) for r in day_ratio]))

    # 3. the forecast itself, from all of the data
    mean = _point(y, horizon)
    last = y.index[-1].date()
    dates = [last + timedelta(days=i + 1) for i in range(horizon)]
    weekly = []
    for i in range(0, horizon, 7):
        blk = mean[i:i + 7]
        lo, hi = (w_lo, w_hi) if len(blk) == 7 else (t_lo, t_hi)
        weekly.append((dates[i], dates[min(i + 6, horizon - 1)], float(blk.sum()), float(blk.sum() * lo), float(blk.sum() * hi)))
    return Forecast(dates, mean.tolist(), (mean * d_lo).tolist(), (mean * d_hi).tolist(),
                    mape(test.to_numpy(), pred), mape(test.to_numpy(), naive), len(origins), coverage,
                    float(mean.sum()), float(mean.sum() * t_lo), float(mean.sum() * t_hi), weekly)
