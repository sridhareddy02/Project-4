"""The semantic layer: loads metrics.yaml, exposes the allowlist, and compiles plans to SQL."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .plan import DateRange, Filter, QueryPlan

YAML_PATH = Path(__file__).with_name("metrics.yaml")
VIEW = "marketing_daily"
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    format: str
    synonyms: tuple[str, ...]
    kind: str                         # measure | ratio
    numerator: str | None = None
    denominator: str | None = None
    scale: float = 1.0
    good: str = "up"                  # which direction is an improvement
    requires_spend: bool = False

    @property
    def additive(self) -> bool:
        return self.kind == "measure"


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    column: str
    label_column: str | None
    values: dict[str, tuple[str, tuple[str, ...]]]   # value -> (label, synonyms)


@dataclass
class Catalog:
    metrics: dict[str, Metric]
    dimensions: dict[str, Dimension]
    drivers: dict[str, list[tuple]]
    unsupported: dict[str, dict[str, Any]]
    glossary: list[dict[str, str]]
    definitions: dict[str, dict[str, str]] = field(default_factory=dict)
    campaign_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # ---- construction -------------------------------------------------------------------------------
    @classmethod
    def load(cls, con: sqlite3.Connection | None = None, campaign_aliases: dict[str, tuple[str, ...]] | None = None) -> "Catalog":
        raw = yaml.safe_load(YAML_PATH.read_text())
        metrics: dict[str, Metric] = {}
        for k, v in raw["measures"].items():
            metrics[k] = Metric(k, v["label"], v["format"], tuple(v["synonyms"]), "measure")
        for k, v in raw["ratios"].items():
            metrics[k] = Metric(k, v["label"], v["format"], tuple(v["synonyms"]), "ratio", v["numerator"], v["denominator"],
                                float(v.get("scale", 1)), v.get("good", "up"), bool(v.get("requires_spend", False)))
        dims: dict[str, Dimension] = {}
        for k, v in raw["dimensions"].items():
            vals = {vk: (vv["label"], tuple(vv["synonyms"])) for vk, vv in (v.get("values") or {}).items()}
            dims[k] = Dimension(k, v["label"], v["column"], v.get("label_column"), vals)
        if con is not None:
            rows = con.execute("SELECT campaign FROM dim_campaign ORDER BY campaign_id").fetchall()
            aliases = campaign_aliases or {}
            dims["campaign"] = Dimension("campaign", "Campaign", "campaign", None,
                                         {r[0]: (r[0], tuple(aliases.get(r[0], ()))) for r in rows})
        drivers = {k: [tuple(x) + ((1,) if len(x) == 3 else ()) for x in v] for k, v in raw["drivers"].items()}
        cat = cls(metrics, dims, drivers, raw["unsupported"], raw["glossary"], raw.get("definitions", {}), campaign_aliases or {})
        cat._check()
        return cat

    def _check(self) -> None:
        for m in self.metrics.values():
            assert _IDENT.match(m.key)
            if m.kind == "ratio":
                assert m.numerator in self.metrics and m.denominator in self.metrics, m.key
                assert self.metrics[m.numerator].additive and self.metrics[m.denominator].additive
        for key, chain in self.drivers.items():
            m = self.metrics[key]
            # the chain must telescope: each factor's denominator is the next factor's numerator
            num = [c[0] for c in chain]
            den = [c[1] for c in chain]
            assert num[0] == key or num[0] == m.numerator, f"driver chain for {key} must start at the metric"
            assert num[1:] == den[:-1], f"driver chain for {key} does not telescope"
            if m.kind == "ratio":
                assert den[-1] == m.denominator, f"driver chain for {key} must end at its denominator"
            else:
                assert den[-1] is None, f"driver chain for additive metric {key} must end in a level factor"

    # ---- lookups ------------------------------------------------------------------------------------
    def metric(self, key: str) -> Metric:
        return self.metrics[key]

    def label(self, metric: str) -> str:
        return self.metrics[metric].label

    def dim_label(self, dim: str, value: str) -> str:
        return self.dimensions[dim].values.get(value, (value, ()))[0]

    def dim_values(self, dim: str) -> list[str]:
        return list(self.dimensions[dim].values)

    def catalog_json(self) -> dict[str, Any]:
        return {
            "metrics": [
                {"key": m.key, "label": m.label, "format": m.format, "kind": m.kind,
                 "formula": (f"SUM({m.numerator}) / SUM({m.denominator})" + (f" x {m.scale:g}" if m.scale != 1 else "")) if m.kind == "ratio" else f"SUM({m.key})",
                 "good": m.good, "requires_spend": m.requires_spend, "synonyms": list(m.synonyms)[:4]}
                for m in self.metrics.values()
            ],
            "dimensions": [
                {"key": d.key, "label": d.label, "values": [v[0] for v in d.values.values()]}
                for d in self.dimensions.values()
            ],
            "unsupported": [{"key": k, "terms": v["terms"], "why": v["why"]} for k, v in self.unsupported.items()],
        }


# ---------------------------------------------------------------------------------------------------------
# SQL compilation. Identifiers come only from the catalog; every value is a bound parameter.
# ---------------------------------------------------------------------------------------------------------
class CompileError(ValueError):
    pass


def metric_sql(cat: Catalog, key: str) -> str:
    if key not in cat.metrics:
        raise CompileError(f"unknown metric {key!r}")
    m = cat.metrics[key]
    if m.kind == "measure":
        return f"SUM({m.key})"
    num, den = f"SUM({m.numerator})", f"SUM({m.denominator})"
    scale = f" * {m.scale:g}" if m.scale != 1 else ""
    return f"CASE WHEN {den} = 0 THEN NULL ELSE {num} * 1.0{scale} / {den} END"


GRAIN_COLUMN = {"day": "date", "week": "week_start", "month": "month_start"}


def where_clause(cat: Catalog, period: DateRange, filters: list[Filter]) -> tuple[str, list[Any]]:
    parts = ["date BETWEEN ? AND ?"]
    params: list[Any] = [period.start.isoformat(), period.end.isoformat()]
    for f in filters:
        if f.dimension not in cat.dimensions:
            raise CompileError(f"unknown dimension {f.dimension!r}")
        dim = cat.dimensions[f.dimension]
        allowed = set(dim.values)
        bad = [v for v in f.values if v not in allowed]
        if bad:
            raise CompileError(f"unknown {f.dimension} value(s) {bad!r}")
        parts.append(f"{dim.column} IN ({', '.join('?' for _ in f.values)})")
        params.extend(f.values)
    return " AND ".join(parts), params


def compile_query(cat: Catalog, metrics: list[str], period: DateRange, filters: list[Filter] | None = None,
                  group_by: list[str] | None = None, grain: str | None = None,
                  order_by: str | None = None, descending: bool = True, limit: int | None = None) -> tuple[str, list[Any]]:
    """Compile a read-only aggregate query. Returns (sql, params)."""
    filters = filters or []
    group_by = group_by or []
    if not metrics:
        raise CompileError("at least one metric is required")
    select, group_cols = [], []
    if grain:
        if grain not in GRAIN_COLUMN:
            raise CompileError(f"unknown grain {grain!r}")
        select.append(f"{GRAIN_COLUMN[grain]} AS period")
        group_cols.append(GRAIN_COLUMN[grain])
    for g in group_by:
        if g not in cat.dimensions:
            raise CompileError(f"unknown dimension {g!r}")
        col = cat.dimensions[g].column
        select.append(f"{col} AS {g}")
        group_cols.append(col)
    select += [f"{metric_sql(cat, m)} AS {m}" for m in metrics]
    where, params = where_clause(cat, period, filters)
    sql = f"SELECT {', '.join(select)}\nFROM {VIEW}\nWHERE {where}"
    if group_cols:
        sql += f"\nGROUP BY {', '.join(group_cols)}"
    if order_by:
        if order_by not in metrics and order_by != "period" and order_by not in group_by:
            raise CompileError(f"cannot order by {order_by!r}")
        sql += f"\nORDER BY {order_by} {'DESC' if descending else 'ASC'}"
    elif grain:
        sql += "\nORDER BY period"
    if limit:
        sql += f"\nLIMIT {int(limit)}"
    return sql, params


def inline_params(sql: str, params: list[Any]) -> str:
    """Render the SQL with values inlined, for display only. Execution always uses bound parameters."""
    it = iter(params)

    def sub(_: re.Match) -> str:
        v = next(it)
        return f"'{v}'" if isinstance(v, str) else str(v)

    return re.sub(r"\?", sub, sql)


def paid_only_filters(cat: Catalog, plan_metrics: list[str], filters: list[Filter]) -> tuple[list[Filter], str | None]:
    """Spend-based metrics only exist for paid channels, so scope them to paid unless the question says otherwise.
    Returns the filters to use and a note for the user when a restriction was added."""
    if not any(cat.metrics[m].requires_spend for m in plan_metrics):
        return filters, None
    has_scope = any(f.dimension in ("channel", "channel_group", "campaign") for f in filters)
    if has_scope:
        return filters, None
    return filters + [Filter(dimension="channel_group", values=["paid"])], (
        "Spend-based metrics (" + ", ".join(cat.label(m) for m in plan_metrics if cat.metrics[m].requires_spend)
        + ") are limited to paid channels because owned channels have no media spend."
    )


@lru_cache(maxsize=1)
def default_catalog_path() -> Path:
    return YAML_PATH


def inside(period: DateRange, lo: date, hi: date) -> bool:
    return period.start >= lo and period.end <= hi
