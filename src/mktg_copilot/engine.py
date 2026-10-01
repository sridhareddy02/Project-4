"""The copilot engine: question -> plan -> governed SQL -> analysis -> narrative -> grounding check.

Flow: a planner proposes a QueryPlan (rules by default, an LLM optionally); the plan is validated against the
semantic layer; SQL is compiled from the layer and run on a read-only connection; analysis modules explain
the numbers; a narrator writes prose from computed values; the verifier traces every number in the prose back
to the data.
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import fmt
from .analysis import anomaly as an
from .analysis.decompose import eval_metric, factor_decomposition, segment_decomposition
from .analysis.forecast import MAX_HORIZON, forecast_series
from .analysis.stats import bootstrap_delta
from .calendar import holiday_in_window
from .data.spec import campaign_aliases
from .data.warehouse import connect_readonly, read_sql
from .nlu.planner import PlannerResult, RulePlanner
from .nlu.retrieval import Retriever
from .nlu.timeparse import describe
from .plan import Answer, Chart, Clarification, Column, DateRange, Filter, QueryPlan, Series, Span, Table
from .semantic import Catalog, CompileError, compile_query, inline_params, paid_only_filters
from .validate import validate_plan
from .verify import verify

BASE = ["impressions", "clicks", "spend", "sessions", "orders", "revenue", "new_customers"]
INTENTS = ("summary", "compare", "explain_change", "trend", "rank", "anomaly", "forecast", "definition")


def _num(v: Any) -> float | None:
    return fmt.clean(v)


class Engine:
    def __init__(self, db_path: Path | None = None, llm_planner=None):
        self.con: sqlite3.Connection = connect_readonly(db_path)
        self.cat = Catalog.load(self.con, campaign_aliases())
        self.retriever = Retriever(self.cat)
        lo, hi = self.con.execute("SELECT MIN(date), MAX(date) FROM fact_daily").fetchone()
        self.start, self.end = date.fromisoformat(lo), date.fromisoformat(hi)
        self.rules = RulePlanner(self.cat, self.retriever, today=self.end + timedelta(days=1), data_start=self.start, data_end=self.end)
        self.llm = llm_planner
        if self.llm is None:  # configured only when COPILOT_LLM_API_KEY is set
            from .nlu.llm import LLMPlanner
            self.llm = LLMPlanner.from_env(self.cat, self.retriever, self.rules, self.start, self.end)
        self.camp = read_sql(self.con, "SELECT * FROM dim_campaign")
        self._gaps = self._load_gaps()
        self._series_cache: dict[tuple, pd.Series] = {}

    # ==================================================================================================
    # entry point
    # ==================================================================================================
    def ask(self, question: str, planner: str = "rules") -> Answer:
        t0 = time.perf_counter()
        ans = Answer(question=question, as_of=self.end, planner=planner)
        engine = self.llm if (planner == "llm" and self.llm is not None) else self.rules
        if planner == "llm" and self.llm is None:
            ans.warnings.append("The LLM planner is not configured, so the rule-based planner was used.")
        else:
            ans.planner = engine.name
        pr: PlannerResult = engine.plan(question)
        ans.retrieved = pr.retrieved
        ans.warnings += pr.notes
        ans.timings_ms["plan"] = round((time.perf_counter() - t0) * 1000, 1)

        if pr.clarification is not None:
            return self._clarify(ans, pr.clarification)
        plan = pr.plan
        assert plan is not None
        problems = self.validate(plan)
        if problems:
            ans.status, ans.plan = "error", plan
            ans.headline = "I could not turn that into a valid query."
            ans.narrative = problems
            return ans
        ans.plan = plan
        ans.assumptions += plan.assumptions

        t1 = time.perf_counter()
        try:
            getattr(self, f"_do_{plan.intent}")(plan, ans)
        except CompileError as e:  # defence in depth: validation should have caught this
            ans.status, ans.headline, ans.narrative = "error", "The query was rejected by the semantic layer.", [str(e)]
            return ans
        except ValueError as e:
            ans.status, ans.headline, ans.narrative = "refused", "That analysis is not possible with this data.", [str(e)]
            ans.suggestions = ["What was revenue last week?", "Why did ROAS change last month?"]
            return ans
        ans.timings_ms["analysis"] = round((time.perf_counter() - t1) * 1000, 1)

        t2 = time.perf_counter()
        self._ground(ans)
        ans.timings_ms["verify"] = round((time.perf_counter() - t2) * 1000, 1)
        ans.timings_ms["total"] = round((time.perf_counter() - t0) * 1000, 1)
        return ans

    def _clarify(self, ans: Answer, c: Clarification) -> Answer:
        ans.status = "refused" if c.kind == "refuse" else "clarify"
        ans.headline = "I can't answer that" if c.kind == "refuse" else "I need a little more detail"
        ans.narrative = [c.message]
        ans.suggestions = c.suggestions
        ans.grounding = {"checked": 0, "matched": 0, "unmatched": [], "hedging_flags": [], "passed": True}
        return ans

    # ==================================================================================================
    # validation: the allowlist between a proposed plan and the warehouse
    # ==================================================================================================
    def validate(self, plan: QueryPlan) -> list[str]:
        return validate_plan(plan, self.cat, self.start, self.end)

    # ==================================================================================================
    # query helpers
    # ==================================================================================================
    def _run(self, ans: Answer, metrics: list[str], period: DateRange, filters: list[Filter], group_by: list[str] | None = None,
             grain: str | None = None, order_by: str | None = None, desc: bool = True, limit: int | None = None,
             label: str | None = None, show: bool = True) -> pd.DataFrame:
        sql, params = compile_query(self.cat, metrics, period, filters, group_by, grain, order_by, desc, limit)
        df = read_sql(self.con, sql, params)
        if show:
            text = inline_params(sql, params)
            ans.sql.append(f"-- {label}\n{text}" if label else text)
        return df

    def _measures(self, ans: Answer, period: DateRange, filters: list[Filter], group_by: list[str] | None = None,
                  grain: str | None = None, label: str | None = None) -> pd.DataFrame:
        return self._run(ans, BASE, period, filters, group_by, grain, label=label, show=bool(label))

    def _scope(self, plan: QueryPlan, ans: Answer) -> list[Filter]:
        filters, note = paid_only_filters(self.cat, plan.metrics, plan.filters)
        spend_metrics = [self.cat.label(m) for m in plan.metrics if self.cat.metrics[m].requires_spend]
        if spend_metrics and not note:
            # the question named channels or campaigns explicitly: keep only the ones that carry media spend
            chans = self._scope_channels(filters)
            owned = [c for c in chans if c not in ("paid_search", "paid_social", "display")]
            if owned and len(owned) == len(chans):
                raise ValueError(f"{', '.join(spend_metrics)} is not defined for {', '.join(self.cat.dim_label('channel', c) for c in owned)}: it needs media spend, and that channel has none.")
            if owned:
                chan = next((f for f in filters if f.dimension == "channel"), None)
                if chan is not None:
                    keep = [v for v in chan.values if v not in owned]
                    filters = [f for f in filters if f.dimension != "channel"] + [Filter(dimension="channel", values=keep)]
                else:
                    filters = filters + [Filter(dimension="channel_group", values=["paid"])]
                ans.assumptions.append(f"{', '.join(self.cat.dim_label('channel', c) for c in owned)} has no media spend, so it was left out of {', '.join(spend_metrics)}.")
        if note:
            mixed = [m for m in plan.metrics if not self.cat.metrics[m].requires_spend]
            if mixed:  # one scope has to serve every metric in the question, so say so plainly
                note = ("Because " + ", ".join(self.cat.label(m) for m in plan.metrics if self.cat.metrics[m].requires_spend)
                        + (" exists" if sum(self.cat.metrics[m].requires_spend for m in plan.metrics) == 1 else " exist") + " only for paid channels, every metric in this answer is limited to paid channels. Ask separately for all-channel figures.")
            if note not in ans.assumptions:
                ans.assumptions.append(note)
        return filters

    def scope_label(self, filters: list[Filter]) -> str:
        bits = []
        for f in filters:
            labs = [self.cat.dim_label(f.dimension, v) for v in f.values]
            if f.dimension == "channel_group":
                bits.append("paid channels" if f.values == ["paid"] else "owned channels")
            elif f.dimension == "region":
                bits.append(" and ".join(labs) + " region")
            else:
                bits.append(" and ".join(labs))
        return ", ".join(bits)

    @staticmethod
    def when(r: DateRange) -> str:
        return r.label if re.search(r"\d", r.label) else f"{r.label} ({describe(r.start, r.end)})"

    def _label_col(self, df: pd.DataFrame, dim: str) -> pd.Series:
        return df[dim].map(lambda v: self.cat.dim_label(dim, v))

    def _sums(self, df: pd.DataFrame) -> dict[str, float]:
        return {c: float(df[c].sum()) for c in BASE}

    def _mval(self, key: str, sums: dict[str, float]) -> float:
        return eval_metric(self.cat, key, sums)

    _PLURAL = {"orders", "sessions", "clicks", "impressions", "new_customers"}

    def _be(self, key: str) -> str:
        return "were" if key in self._PLURAL else "was"

    @staticmethod
    def _lc(label: str) -> str:
        """Lower-case the first letter only, so acronyms such as CPM and CTR survive."""
        return label[:1].lower() + label[1:]

    def _fv(self, key: str, v: float | None) -> str:
        return fmt.by_format(self.cat.metrics[key].format, v)

    def _col(self, key: str, label: str | None = None) -> Column:
        return Column(key=key, label=label or self.cat.label(key), format=self.cat.metrics[key].format)

    def _scope_channels(self, filters: list[Filter]) -> list[str]:
        df = self.camp
        for f in filters:
            if f.dimension in ("channel", "channel_group", "objective", "campaign"):
                df = df[df[f.dimension].isin(f.values)]
        return sorted(df["channel"].unique())

    # ---- data gaps ----------------------------------------------------------------------------------
    def _load_gaps(self) -> pd.DataFrame:
        cov = read_sql(self.con, "SELECT date, channel, COUNT(*) AS n FROM marketing_daily GROUP BY date, channel")
        regions = read_sql(self.con, "SELECT COUNT(DISTINCT region) AS r FROM fact_daily")["r"].iloc[0]
        expected = (self.camp.groupby("channel").size() * regions).to_dict()
        days = pd.date_range(self.start, self.end).strftime("%Y-%m-%d")
        grid = pd.MultiIndex.from_product([days, sorted(expected)], names=["date", "channel"]).to_frame(index=False)
        grid = grid.merge(cov, how="left", on=["date", "channel"]).fillna({"n": 0})
        grid["expected"] = grid["channel"].map(expected)
        return grid[grid["n"] < grid["expected"]][["date", "channel"]]

    def _gap_warnings(self, periods: list[DateRange], filters: list[Filter]) -> list[str]:
        if self._gaps.empty:
            return []
        chans = set(self._scope_channels(filters))
        out: list[str] = []
        for ch, grp in self._gaps.groupby("channel"):
            if ch not in chans:
                continue
            ds = sorted(date.fromisoformat(d) for d in grp["date"])
            runs: list[list[date]] = []
            for d in ds:
                if runs and (d - runs[-1][-1]).days == 1:
                    runs[-1].append(d)
                else:
                    runs.append([d])
            for run in runs:
                if any(p.start <= run[-1] and p.end >= run[0] for p in periods):
                    out.append(f"{self.cat.dim_label('channel', ch)} has no data for {describe(run[0], run[-1])}, so totals for that window are understated. This is a data gap, not a drop in performance.")
        return out

    # ---- daily series -------------------------------------------------------------------------------
    def series(self, ans: Answer | None, metric: str, filters: list[Filter]) -> pd.Series:
        """Daily series over the whole loaded history for a metric and scope."""
        key = (metric, tuple((f.dimension, tuple(f.values)) for f in filters))
        if key in self._series_cache:
            return self._series_cache[key]
        full = DateRange(start=self.start, end=self.end, label="full history")
        df = self._run(ans or Answer(question="", as_of=self.end), [metric], full, filters, grain="day", show=False)
        s = pd.Series(df[metric].to_numpy(float), index=pd.to_datetime(df["period"]))
        self._series_cache[key] = s
        return s

    # ==================================================================================================
    # intents
    # ==================================================================================================
    def _do_definition(self, plan: QueryPlan, ans: Answer) -> None:
        subject = plan.subject or plan.metrics[0]
        if subject in self.cat.metrics:
            m = self.cat.metrics[subject]
            d = self.cat.definitions.get(subject, {})
            ans.headline = f"{m.label}: {d.get('meaning', m.label)}"
            formula = (f"{m.label} = total {self.cat.label(m.numerator).lower()} divided by total {self.cat.label(m.denominator).lower()}" + (f", times {m.scale:g}" if m.scale != 1 else "")
                       if m.kind == "ratio" else f"{m.label} = the sum of {m.key} over the rows in scope")
            ans.narrative = [formula + ". Ratios are always computed from summed numerators and denominators, never as an average of daily ratios.",
                             f"How to read it: {d.get('read_as', '')}", f"Watch out: {d.get('watch_out', '')}"]
            if m.requires_spend:
                ans.narrative.append("It is only defined for paid channels, because owned channels carry no media spend in this data.")
            if subject in self.cat.drivers:
                ans.narrative.append("Driver analysis splits it into: " + "; ".join(c[2] for c in self.cat.drivers[subject]) + ".")
            ans.sql.append(f"-- governed definition (semantic layer)\n{self._definition_sql(subject)}")
            related = [k for k, mm in self.cat.metrics.items() if k != subject and (mm.numerator == subject or mm.denominator == subject or (m.kind == 'ratio' and subject in (m.numerator, m.denominator) and k in (m.numerator, m.denominator)))][:3]
            ans.followups = [f"What was {m.label} last week?"] + [f"What is {self.cat.label(r)}?" for r in related][:2]
        else:
            doc = next((g for g in self.cat.glossary if g["id"] == subject), None)
            if doc is None:
                raise ValueError("I could not find a definition for that.")
            ans.headline = doc["title"]
            ans.narrative = [re.sub(r"\s+", " ", doc["text"]).strip()]
            ans.followups = ["Why did ROAS change last month?", "Any anomalies in orders over the last 90 days?"]

    def _definition_sql(self, key: str) -> str:
        from .semantic import metric_sql
        return f"SELECT {metric_sql(self.cat, key)} AS {key}\nFROM marketing_daily\nWHERE date BETWEEN :start AND :end"

    # ---- summary ------------------------------------------------------------------------------------
    def _do_summary(self, plan: QueryPlan, ans: Answer) -> None:
        filters = self._scope(plan, ans)
        period = plan.period
        metrics = plan.metrics
        first = self.cat.metrics[metrics[0]]
        scope = self.scope_label(filters)
        suffix = f" across {scope}" if scope else ""
        ans.warnings += self._gap_warnings([period], filters)

        if plan.group_by:
            desc = first.good == "up"
            df = self._run(ans, metrics, period, filters, plan.group_by, order_by=metrics[0], desc=desc, label="primary result")
            df = df.dropna(subset=[metrics[0]]) if first.requires_spend else df
            if df.empty:
                raise ValueError(f"{first.label} is not defined for this selection: it needs media spend, and the channels asked about have none.")
            g = plan.group_by[0]
            name = self.cat.dimensions[g].label
            label_cols = {gg: self._label_col(df, gg) for gg in plan.group_by}
            title = f"{', '.join(self.cat.label(m) for m in metrics)} by {' and '.join(self.cat.dimensions[x].label.lower() for x in plan.group_by)}"
            cols = [Column(key=gg, label=self.cat.dimensions[gg].label, format="text") for gg in plan.group_by] + [self._col(m) for m in metrics]
            rows = []
            totals = self._mval_total(plan.metrics, filters, period, ans) if first.additive else None
            for i, r in df.iterrows():
                row: dict[str, Any] = {gg: label_cols[gg][i] for gg in plan.group_by}
                for m in metrics:
                    row[m] = _num(r[m])
                rows.append(row)
            if first.additive and totals and totals.get(metrics[0]):
                cols.append(Column(key="share", label=f"Share of {first.label.lower()}", format="percent"))
                for row in rows:
                    row["share"] = _num(row[metrics[0]] / totals[metrics[0]]) if row[metrics[0]] is not None else None
            ans.tables.append(Table(title=title, columns=cols, rows=rows))
            vals = [(rows[i][g], rows[i][metrics[0]]) for i in range(len(rows)) if rows[i][metrics[0]] is not None]
            top, bot = vals[0], vals[-1]
            ans.headline = f"For {self.when(period)}, {first.label} by {name.lower()}{suffix}: {top[0]} {'led' if desc else 'was lowest'} at {self._fv(metrics[0], top[1])}."
            ans.facts += [top[1], bot[1]]
            para = []
            if len(vals) > 1:
                para.append(f"{top[0]} {'had the highest' if desc else 'had the lowest'} {first.label} ({self._fv(metrics[0], top[1])}) and {bot[0]} the {'lowest' if desc else 'highest'} ({self._fv(metrics[0], bot[1])}).")
            if first.additive and totals and totals.get(metrics[0]) and len(vals) > 1:
                sh = rows[0].get("share")
                if sh is not None:
                    para.append(f"{top[0]} accounted for {fmt.pct(sh)} of {first.label.lower()}.")
                    ans.facts.append(sh)
            if len(plan.group_by) > 1:
                para.append("Rows are shown for each combination, ordered by the first metric.")
            ans.narrative = para
            show = vals[:12]
            ans.charts.append(Chart(type="bar", title=f"{first.label} by {name.lower()}", x=[v[0] for v in show],
                                    series=[Series(name=first.label, values=[v[1] for v in show])], y_format=first.format, horizontal=True))
            ans.followups = [f"Compare {first.label} by {name.lower()} to the period before", f"Why did {first.label} change last month?", f"Show {first.label} trend by week"]
            return

        # no grouping: totals, with a comparison to the previous period of the same length
        df = self._run(ans, metrics, period, filters, label="primary result")
        row = df.iloc[0]
        values = {m: _num(row[m]) for m in metrics}
        if all(v is None for v in values.values()):
            raise ValueError(f"{first.label} is not defined for this selection: it needs media spend, and {scope or 'the channels asked about'} has none.")
        prev = self._previous(period)
        table_rows, notes = [], []
        prev_df = prev_daily = cur_daily = None
        if prev is not None:
            prev_df = self._run(ans, metrics, prev, filters, label="previous period, for context")
            cur_daily = self._measures(ans, period, filters, grain="day")
            prev_daily = self._measures(ans, prev, filters, grain="day")
        for m in metrics:
            v = values[m]
            r: dict[str, Any] = {"metric": self.cat.label(m), "value": v}
            if prev_df is not None and v is not None and _num(prev_df.iloc[0][m]):
                p = _num(prev_df.iloc[0][m])
                r["previous"] = p
                r["change"] = (v / p - 1.0) * 100.0
                ci = bootstrap_delta(self.cat, m, prev_daily.drop(columns=["period"]), cur_daily.drop(columns=["period"]), per_day=self.cat.metrics[m].additive and period.days != prev.days)
                r["verdict"] = "clear change" if ci and ci.clear else "within normal noise"
            table_rows.append(r)
        fmt_of = {self.cat.label(m): m for m in metrics}
        # values in one column mix formats, so render each row with its own metric's format on the client
        ans.tables.append(Table(title="Result", columns=[Column(key="metric", label="Metric"), Column(key="value", label=self.when(period), format="auto"),
                                                         Column(key="previous", label=f"Previous period" if prev else "", format="auto"),
                                                         Column(key="change", label="Change", format="signed_percent"), Column(key="verdict", label="Versus normal noise")],
                                rows=[{**r, "_metric": fmt_of[r["metric"]]} for r in table_rows]))
        if len(metrics) == 1:
            m = metrics[0]
            ans.headline = f"For {self.when(period)}, {first.label} {self._be(m)} {self._fv(m, values[m])}{suffix}."
            ans.facts.append(values[m])
        else:
            parts = [f"{self.cat.label(m)} {self._fv(m, values[m])}" for m in metrics if values[m] is not None]
            ans.headline = f"For {self.when(period)}{suffix}: " + ", ".join(parts) + "."
            ans.facts += [v for v in values.values() if v is not None]
        para = []
        for r, m in zip(table_rows, metrics):
            if "change" in r and r["change"] is not None:
                direction = ("rose" if r["change"] > 0 else "fell") if self.cat.metrics[m].additive else ("improved" if (r["change"] > 0) == (self.cat.metrics[m].good == "up") else "worsened")
                verdict = "a clear change" if r["verdict"] == "clear change" else "within normal day-to-day variation"
                para.append(f"{self.cat.label(m)} {direction} {fmt.plain_pct(r['change'])} against the previous period ({self._fv(m, r['previous'])}); that is {verdict}.")
                ans.facts += [r["change"], r["previous"]]
        ans.narrative = para
        if period.days >= 7:
            grain = "day" if period.days <= 62 else "week"
            ts = self._run(ans, [metrics[0]], period, filters, grain=grain, show=False)
            ans.charts.append(Chart(type="line", title=f"{first.label} by {grain}", x=ts["period"].tolist(),
                                    series=[Series(name=first.label, values=[_num(v) for v in ts[metrics[0]]])], y_format=first.format))
        ans.followups = [f"Why did {first.label} change vs the previous period?", f"{first.label} by channel for the same period", f"Any anomalies in {first.label} over the last 90 days?"]

    def _mval_total(self, metrics: list[str], filters: list[Filter], period: DateRange, ans: Answer) -> dict[str, float]:
        df = self._run(ans, metrics, period, filters, show=False)
        return {m: float(df.iloc[0][m]) if df.iloc[0][m] is not None and not pd.isna(df.iloc[0][m]) else 0.0 for m in metrics}

    def _previous(self, period: DateRange) -> DateRange | None:
        n = period.days
        e = period.start - timedelta(days=1)
        s = e - timedelta(days=n - 1)
        if s < self.start:
            return None
        return DateRange(start=s, end=e, label=f"the {n} days before")

    # ---- rank ---------------------------------------------------------------------------------------
    def _do_rank(self, plan: QueryPlan, ans: Answer) -> None:
        filters = self._scope(plan, ans)
        m = plan.metrics[0]
        met = self.cat.metrics[m]
        dim = plan.group_by[0]
        desc = plan.order != "asc"
        ans.warnings += self._gap_warnings([plan.period], filters)
        df = self._run(ans, [m], plan.period, filters, [dim], order_by=m, desc=desc, label="primary result")
        df = df.dropna(subset=[m])
        if df.empty:
            raise ValueError(f"{met.label} is not defined for this selection: it needs media spend, and these channels have none.")
        df["label"] = self._label_col(df, dim)
        n = min(plan.top_n or len(df), len(df))
        top = df.head(n)
        dname = self.cat.dimensions[dim].label
        ans.tables.append(Table(title=f"{met.label} by {dname.lower()}, {'highest' if desc else 'lowest'} first", columns=[Column(key="rank", label="#"), Column(key="name", label=dname), self._col(m)],
                                rows=[{"rank": i + 1, "name": r["label"], m: _num(r[m])} for i, (_, r) in enumerate(top.iterrows())]))
        better = "best" if (met.good == "up") == desc else "weakest"
        ans.headline = f"For {self.when(plan.period)}, {top.iloc[0]['label']} ranked first on {met.label} at {self._fv(m, top.iloc[0][m])}."
        ans.facts.append(float(top.iloc[0][m]))
        names = ", ".join(f"{r['label']} ({self._fv(m, r[m])})" for _, r in top.head(3).iterrows())
        ans.facts += [float(v) for v in top.head(3)[m]]
        ans.narrative = [f"Ordered {'highest' if desc else 'lowest'} first, the top {min(3, n)} by {met.label} are {names}."]
        if len(df) > n:
            last = df.iloc[-1]
            ans.facts.append(float(len(df)))
            ans.narrative.append(f"Across all {len(df)} {dname.lower()}s, the {'lowest' if desc else 'highest'} was {last['label']} at {self._fv(m, last[m])}.")
            ans.facts.append(float(last[m]))
        if met.good == "down":
            ans.caveats.append(f"A lower {met.label} is better, so “best” means the lowest value.")
        ans.caveats.append("Rankings compare totals for the period; a small campaign can rank high or low on a few days of data. Check spend and orders before acting.")
        ans.charts.append(Chart(type="bar", title=f"{met.label} by {dname.lower()}", x=top["label"].tolist(), series=[Series(name=met.label, values=[_num(v) for v in top[m]])], y_format=met.format, horizontal=True))
        ans.followups = [f"Why did {met.label} change for {top.iloc[0]['label']}?", f"{met.label} trend for {top.iloc[0]['label']}", f"Compare {met.label} by {dname.lower()} with the previous period"]

    # ---- trend --------------------------------------------------------------------------------------
    def _do_trend(self, plan: QueryPlan, ans: Answer) -> None:
        filters = self._scope(plan, ans)
        period, grain = plan.period, plan.grain or "week"
        metrics = plan.metrics[:3]
        if len(plan.metrics) > 3:
            ans.assumptions.append("Showing the first three metrics.")
        ans.warnings += self._gap_warnings([period], filters)
        df = self._run(ans, metrics, period, filters, grain=grain, label="primary result")
        if df.empty:
            raise ValueError("There is no data for that period.")
        scope = self.scope_label(filters)
        suffix = f" across {scope}" if scope else ""
        cols = [Column(key="period", label=grain.capitalize(), format="date")] + [self._col(m) for m in metrics]
        ans.tables.append(Table(title=f"{', '.join(self.cat.label(m) for m in metrics)} by {grain}", columns=cols,
                                rows=[{"period": r["period"], **{m: _num(r[m]) for m in metrics}} for _, r in df.iterrows()]))
        paras = []
        for m in metrics:
            met = self.cat.metrics[m]
            s = df[["period", m]].dropna()
            if s.empty:
                continue
            hi, lo = s.loc[s[m].idxmax()], s.loc[s[m].idxmin()]
            whole = float(self._mval_total([m], filters, period, ans)[m])
            last = float(s[m].iloc[-1])
            if met.additive:
                lead = f"{met.label} totalled {self._fv(m, whole)} over the window."
            else:
                lead = f"For the whole window {met.label} {self._be(m)} {self._fv(m, whole)}."
            paras.append(f"{lead} The highest {grain} was {self._pname(hi['period'], grain)} ({self._fv(m, hi[m])}), the lowest was {self._pname(lo['period'], grain)} ({self._fv(m, lo[m])}), and the latest {grain} was {self._fv(m, last)}.")
            ans.facts += [float(hi[m]), float(lo[m]), last, whole]
            chart_x = s["period"].tolist()
            markers: list[dict] = []
            if grain == "day":
                try:
                    full = self.series(ans, m, filters)
                    scored, incidents = an.detect(m, full)
                    for inc in incidents:
                        if inc.start <= period.end and inc.end >= period.start:
                            markers.append({"x": inc.start.isoformat(), "label": f"{inc.direction} {fmt.plain_pct(inc.deviation_pct, 0)}"})
                    if markers:
                        paras.append(f"{len(markers)} flagged incident{'s' if len(markers) != 1 else ''} fall in this window for {met.label} (marked on the chart).")
                        ans.facts.append(float(len(markers)))
                except ValueError:
                    pass
            ans.charts.append(Chart(type="line", title=f"{met.label} by {grain}", x=chart_x, series=[Series(name=met.label, values=[_num(v) for v in s[m]])], y_format=met.format, markers=markers))
        ans.headline = f"{', '.join(self.cat.label(m) for m in metrics)} by {grain}, {self.when(period)}{suffix}."
        ans.narrative = paras
        ans.followups = [f"Why did {self.cat.label(metrics[0])} change over this period?", f"Any anomalies in {self.cat.label(metrics[0])}?", f"Forecast {self.cat.label(metrics[0])} for the next 4 weeks"] if self.cat.metrics[metrics[0]].additive else [f"Why did {self.cat.label(metrics[0])} change over this period?", f"Any anomalies in {self.cat.label(metrics[0])}?"]

    @staticmethod
    def _pname(period: str, grain: str) -> str:
        d = date.fromisoformat(period)
        if grain == "week":
            return f"the one starting {describe(d, d)}"
        if grain == "month":
            return d.strftime("%B %Y")
        return describe(d, d)

    # ---- compare ------------------------------------------------------------------------------------
    def _do_compare(self, plan: QueryPlan, ans: Answer) -> None:
        filters = self._scope(plan, ans)
        p1, p0 = plan.period, plan.compare_to
        ans.warnings += self._gap_warnings([p1, p0], filters)
        per_day = p1.days != p0.days
        if per_day and any(self.cat.metrics[m].additive for m in plan.metrics):
            ans.assumptions.append(f"The periods differ in length ({p1.days} and {p0.days} days), so additive measures such as revenue are compared per day.")
        scope = self.scope_label(filters)
        suffix = f" across {scope}" if scope else ""
        group = plan.group_by[:1]
        metrics = plan.metrics if not group else plan.metrics[:1]
        if group and len(plan.metrics) > 1:
            ans.assumptions.append("With a breakdown, only the first metric is compared.")

        cur = self._run(ans, metrics, p1, filters, group, label=f"current period: {p1.label}")
        base = self._run(ans, metrics, p0, filters, group, label=f"baseline period: {p0.label}")
        d1 = self._measures(ans, p1, filters, group, grain="day")
        d0 = self._measures(ans, p0, filters, group, grain="day")
        rows: list[dict[str, Any]] = []
        entities = [None] if not group else sorted(set(cur[group[0]]) | set(base[group[0]]), key=lambda x: str(x))
        for ent in entities:
            for m in metrics:
                met = self.cat.metrics[m]
                a = self._pick(cur, group, ent, m)
                b = self._pick(base, group, ent, m)
                if met.additive and per_day and a is not None and b is not None:
                    a, b = a / p1.days, b / p0.days
                if a is None or b is None or b == 0:
                    rows.append({"name": self.cat.dim_label(group[0], ent) if group else met.label, "_metric": m, "current": a, "baseline": b, "delta": None, "change": None, "ci": None, "verdict": "n/a"})
                    continue
                x1 = d1 if not group else d1[d1[group[0]] == ent]
                x0 = d0 if not group else d0[d0[group[0]] == ent]
                ci = bootstrap_delta(self.cat, m, x0[BASE], x1[BASE], per_day=met.additive and per_day)
                rows.append({"name": self.cat.dim_label(group[0], ent) if group else met.label, "_metric": m, "current": a, "baseline": b, "delta": a - b,
                             "change": (a / b - 1.0) * 100.0,
                             "ci": (f"{fmt.signed_pct(ci.low)} to {fmt.signed_pct(ci.high)}" if ci else None),
                             "verdict": ("clear change" if ci and ci.clear else "within normal noise") if ci else "n/a"})
        unit = " per day" if per_day and any(self.cat.metrics[m].additive for m in metrics) else ""
        name_label = self.cat.dimensions[group[0]].label if group else "Metric"
        ans.tables.append(Table(title=f"{p1.label} versus {p0.label}{unit}", columns=[
            Column(key="name", label=name_label), Column(key="current", label=p1.label + unit, format="auto"), Column(key="baseline", label=p0.label + unit, format="auto"),
            Column(key="change", label="Change", format="signed_percent"), Column(key="ci", label="95% range"), Column(key="verdict", label="Versus normal noise")], rows=rows))
        valid = [r for r in rows if r["change"] is not None]
        if not valid:
            raise ValueError("The baseline period has no value to compare against.")
        main = max(valid, key=lambda r: abs(r["change"])) if group else valid[0]
        m0 = main["_metric"]
        met0 = self.cat.metrics[m0]
        verb = ("rose" if main["change"] > 0 else "fell") if met0.additive else ("improved" if (main["change"] > 0) == (met0.good == "up") else "worsened")
        if group:
            ans.headline = f"{met0.label}{suffix}: the biggest move was {main['name']}, which {verb} {fmt.plain_pct(main['change'])} ({self._fv(m0, main['baseline'])} to {self._fv(m0, main['current'])}){unit}."
        else:
            ans.headline = f"{met0.label}{suffix} {verb} {fmt.plain_pct(main['change'])}{unit}: {self._fv(m0, main['baseline'])} in {p0.label} to {self._fv(m0, main['current'])} in {p1.label}."
        ans.facts += [main["change"], main["baseline"], main["current"]]
        paras = []
        for r in (valid if not group else sorted(valid, key=lambda r: -abs(r["change"]))[:3]):
            mm = self.cat.metrics[r["_metric"]]
            vb = "rose" if r["change"] > 0 else "fell"
            tail = "a clear change" if r["verdict"] == "clear change" else "within normal day-to-day variation"
            who = f"{r['name']} " if group else ""
            paras.append(f"{who}{mm.label} {vb} {fmt.plain_pct(r['change'])}, from {self._fv(r['_metric'], r['baseline'])} to {self._fv(r['_metric'], r['current'])}{unit}. That is {tail}" + (f" (95% range {r['ci']})." if r["ci"] else "."))
            ans.facts += [r["change"], r["baseline"], r["current"]]
            if r["ci"]:
                ans.facts += [float(x) for x in re.findall(r"[-+]?\d+\.\d+", r["ci"])]
        ans.narrative = paras
        ans.caveats.append("“Within normal noise” means the change is no bigger than ordinary day-to-day variation in this data. A clear change shows that something moved, not why.")
        ordered = sorted(valid, key=lambda r: r["change"])
        ans.charts.append(Chart(type="diverging", title="Change versus baseline (%)", x=[r["name"] for r in ordered], series=[Series(name="Change", values=[r["change"] for r in ordered])], y_format="signed_percent", horizontal=True))
        ans.followups = [f"Why did {met0.label} change between these periods?", f"{met0.label} trend over the last 12 weeks"]

    @staticmethod
    def _pick(df: pd.DataFrame, group: list[str], ent: Any, m: str) -> float | None:
        if not group:
            return _num(df.iloc[0][m]) if len(df) else None
        sub = df[df[group[0]] == ent]
        return _num(sub.iloc[0][m]) if len(sub) else None

    # ---- explain_change -----------------------------------------------------------------------------
    def _do_explain_change(self, plan: QueryPlan, ans: Answer) -> None:
        filters = self._scope(plan, ans)
        m = plan.metrics[0]
        met = self.cat.metrics[m]
        p1, p0 = plan.period, plan.compare_to
        ans.warnings += self._gap_warnings([p1, p0], filters)
        per_day = met.additive and p1.days != p0.days
        k1, k0 = (1.0 / p1.days, 1.0 / p0.days) if per_day else (1.0, 1.0)
        scope = self.scope_label(filters)
        suffix = f" across {scope}" if scope else ""

        d1 = self._measures(ans, p1, filters, grain="day", label=f"supporting: daily measures, {p1.label}")
        d0 = self._measures(ans, p0, filters, grain="day", label=f"supporting: daily measures, {p0.label}")
        s1 = {c: float(d1[c].sum()) for c in BASE}
        s0 = {c: float(d0[c].sum()) for c in BASE}
        v1, v0 = self._mval(m, s1), self._mval(m, s0)
        if per_day:
            v1, v0 = v1 * k1, v0 * k0
        if math.isnan(v1) or math.isnan(v0) or v0 == 0:
            raise ValueError(f"{met.label} has no usable value in one of the two periods, so the change cannot be explained.")
        change = (v1 / v0 - 1.0) * 100.0
        ci = bootstrap_delta(self.cat, m, d0[BASE], d1[BASE], per_day=per_day)
        verb = ("rose" if change > 0 else "fell") if met.additive else ("improved" if (change > 0) == (met.good == "up") else "worsened")
        unit = " per day" if per_day else ""
        ans.headline = f"{met.label}{suffix} {verb} {fmt.plain_pct(change)}{unit}: {self._fv(m, v0)} in {p0.label} to {self._fv(m, v1)} in {p1.label}."
        ans.facts += [change, v0, v1]
        paras: list[str] = []
        if ci is not None:
            ans.facts += [ci.low, ci.high]
            if ci.clear:
                paras.append(f"This is a clear change: resampling days gives a 95% range of {fmt.signed_pct(ci.low)} to {fmt.signed_pct(ci.high)}, which excludes zero.")
            else:
                paras.append(f"This change is within normal day-to-day variation (95% range {fmt.signed_pct(ci.low)} to {fmt.signed_pct(ci.high)}), so treat the explanation below with caution.")
        if per_day:
            ans.assumptions.append(f"The periods differ in length ({p1.days} and {p0.days} days), so {met.label} is compared per day.")

        # 1. funnel factors
        factors = factor_decomposition(self.cat, m, s0, s1)
        if factors:
            ranked = sorted(factors, key=lambda f: -abs(f.log_share))
            ans.tables.append(Table(title=f"Funnel steps behind the change in {met.label}", columns=[
                Column(key="factor", label="Step"), Column(key="before", label=p0.label, format="auto"), Column(key="after", label=p1.label, format="auto"),
                Column(key="pct", label="Change", format="signed_percent"), Column(key="share", label="Share of the move", format="percent")],
                rows=[{"factor": f.label, "before": f.before * f.display_scale, "after": f.after * f.display_scale, "pct": f.pct_change * 100.0, "share": f.log_share, "_fmt": self._factor_format(f)} for f in factors]))
            top = ranked[0]
            ans.facts += [top.pct_change * 100.0, top.log_share, top.before, top.after]
            sent = f"By funnel step, the biggest mover was {self._lc(top.label)}, which {'rose' if top.pct_change > 0 else 'fell'} {fmt.plain_pct(top.pct_change * 100)} and accounts for {fmt.pct(abs(top.log_share), 0)} of the total move."
            if len(ranked) > 1 and abs(ranked[1].log_share) > 0.1:
                sec = ranked[1]
                sent += f" Next was {self._lc(sec.label)} ({fmt.signed_pct(sec.pct_change * 100)})."
                ans.facts += [sec.pct_change * 100.0]
            paras.append(sent)
            ans.charts.append(Chart(type="diverging", title=f"Change in each funnel step (%)", x=[f.label for f in factors], series=[Series(name="Change", values=[f.pct_change * 100.0 for f in factors])], y_format="signed_percent", horizontal=True))

        # 2. segments
        seg_dim = plan.group_by[0] if plan.group_by else self._seg_dim(filters)
        seg_note = None
        if seg_dim:
            g1 = self._measures(ans, p1, filters, [seg_dim], label=f"supporting: by {seg_dim}, {p1.label}").set_index(seg_dim)
            g0 = self._measures(ans, p0, filters, [seg_dim], label=f"supporting: by {seg_dim}, {p0.label}").set_index(seg_dim)
            if per_day:
                g1, g0 = g1 * k1, g0 * k0
            effects = segment_decomposition(self.cat, m, g0, g1)
            tot_delta = v1 - v0
            if effects and abs(tot_delta) > 0:
                effects.sort(key=lambda e: -abs(e.contribution))
                dname = self.cat.dimensions[seg_dim].label
                rows = []
                for e in effects[:10]:
                    rows.append({"name": self.cat.dim_label(seg_dim, e.segment), "_metric": m, "before": e.before, "after": e.after, "contribution": e.contribution, "mix": e.mix, "rate": e.rate, "share": e.contribution / tot_delta})
                ans.tables.append(Table(title=f"Where the change came from, by {dname.lower()}", columns=[
                    Column(key="name", label=dname), Column(key="before", label=p0.label, format="auto"), Column(key="after", label=p1.label, format="auto"),
                    Column(key="contribution", label=f"Contribution to change", format="auto"), Column(key="rate", label="of which: performance", format="auto"),
                    Column(key="mix", label="of which: mix", format="auto"), Column(key="share", label="Share of total change", format="percent")], rows=rows))
                lead = effects[0]
                lead_name = self.cat.dim_label(seg_dim, lead.segment)
                share = lead.contribution / tot_delta
                ans.facts += [share, lead.rate, lead.mix, lead.contribution]
                if met.kind == "ratio":
                    kind = "its own performance" if abs(lead.rate) >= abs(lead.mix) else "a shift in spend mix toward or away from it"
                    paras.append(f"By {dname.lower()}, {lead_name} contributed {fmt.pct(abs(share), 0)} of the change, mainly through {kind}.")
                else:
                    paras.append(f"By {dname.lower()}, {lead_name} accounted for {fmt.pct(abs(share), 0)} of the change ({self._fv(m, lead.contribution)}).")
                ans.charts.append(Chart(type="diverging", title=f"Contribution to the change by {dname.lower()}", x=[self.cat.dim_label(seg_dim, e.segment) for e in effects[:8]], series=[Series(name="Contribution", values=[e.contribution for e in effects[:8]])], y_format=met.format, horizontal=True))
                # 3. drill into the leading segment
                self._drill(ans, plan, filters, m, seg_dim, lead.segment, p1, p0, paras, k1, k0)
        paras.append("This shows where the change came from, not why it happened. Check what changed in the leading segment (creative, audience, bids, tracking, pricing) and confirm with a test before moving budget.")
        ans.narrative = paras
        ans.caveats.append("Steps and segments add up exactly to the total change (log-scale for steps, midpoint mix and rate for segments), but a step moving does not prove it was the cause.")
        ans.followups = [f"{met.label} trend over the last 12 weeks", f"Any anomalies in {met.label}?", f"Compare {met.label} by campaign for the same periods"]

    def _seg_dim(self, filters: list[Filter]) -> str | None:
        ch = next((f for f in filters if f.dimension == "channel"), None)
        if ch and len(ch.values) == 1:
            return "campaign"
        camp = next((f for f in filters if f.dimension == "campaign"), None)
        if camp and len(camp.values) == 1:
            return "region"
        return "channel"

    def _drill(self, ans, plan, filters, m, seg_dim, seg, p1, p0, paras, k1, k0) -> None:
        met = self.cat.metrics[m]
        sub_filters = [f for f in filters if f.dimension != seg_dim] + [Filter(dimension=seg_dim, values=[seg])]
        seg_label = self.cat.dim_label(seg_dim, seg)
        a1 = self._measures(ans, p1, sub_filters, grain="day")
        a0 = self._measures(ans, p0, sub_filters, grain="day")
        s1 = {c: float(a1[c].sum()) for c in BASE}
        s0 = {c: float(a0[c].sum()) for c in BASE}
        facs = factor_decomposition(self.cat, m, s0, s1)
        if facs:
            top = max(facs, key=lambda f: abs(f.log_share))
            ans.facts += [top.pct_change * 100.0]
            paras.append(f"Inside {seg_label}, the largest step was {self._lc(top.label)} ({fmt.signed_pct(top.pct_change * 100)}).")
        nxt = {"channel": "campaign", "campaign": "region", "region": None}.get(seg_dim)
        if nxt:
            g1 = self._measures(ans, p1, sub_filters, [nxt]).set_index(nxt)
            g0 = self._measures(ans, p0, sub_filters, [nxt]).set_index(nxt)
            if met.additive and p1.days != p0.days:
                g1, g0 = g1 * k1, g0 * k0
            eff = segment_decomposition(self.cat, m, g0, g1)
            tot = (self._mval(m, s1) * (k1 if met.additive else 1)) - (self._mval(m, s0) * (k0 if met.additive else 1))
            if eff and abs(tot) > 0:
                eff.sort(key=lambda e: -abs(e.contribution))
                e = eff[0]
                nm = self.cat.dim_label(nxt, e.segment)
                ans.tables.append(Table(title=f"Inside {seg_label}, by {nxt}", columns=[
                    Column(key="name", label=nxt.capitalize()), Column(key="before", label=p0.label, format="auto"), Column(key="after", label=p1.label, format="auto"),
                    Column(key="contribution", label="Contribution to change", format="auto"), Column(key="share", label="Share of change inside", format="percent")],
                    rows=[{"name": self.cat.dim_label(nxt, x.segment), "_metric": m, "before": x.before, "after": x.after, "contribution": x.contribution, "share": x.contribution / tot} for x in eff[:8]]))
                ans.facts += [e.contribution / tot]
                paras.append(f"Within {seg_label}, {nm} contributed {fmt.pct(abs(e.contribution / tot), 0)} of that segment's change.")

    @staticmethod
    def _factor_format(f) -> str:
        pair = (f.numerator, f.denominator)
        if f.denominator == "":
            return "count"
        if pair in {("clicks", "impressions"), ("orders", "sessions"), ("sessions", "clicks"), ("new_customers", "orders")}:
            return "percent"
        if pair in {("revenue", "orders"), ("spend", "impressions")}:
            return "currency2"
        return "number"

    # ---- anomaly ------------------------------------------------------------------------------------
    def _do_anomaly(self, plan: QueryPlan, ans: Answer) -> None:
        period = plan.period
        filters = plan.filters
        notes = [paid_only_filters(self.cat, [m], plan.filters)[1] for m in plan.metrics]
        if any(notes):
            ans.assumptions.append("Spend-based metrics (" + ", ".join(self.cat.label(m) for m in plan.metrics if self.cat.metrics[m].requires_spend)
                                   + ") were scanned over paid channels only; the other metrics cover every channel.")
        ans.warnings += self._gap_warnings([period], filters)
        incidents: list[an.Incident] = []
        for m in plan.metrics:
            filters = paid_only_filters(self.cat, [m], plan.filters)[0]
            try:
                scored, incs = an.detect(m, self.series(ans, m, filters))
            except ValueError:
                continue
            for inc in incs:
                if inc.start <= period.end and inc.end >= period.start:
                    inc.where = self._localize(ans, m, filters, inc)
                    gap = self._gap_on(filters, inc)
                    if gap:
                        inc.context = (inc.context + "; " if inc.context else "") + gap
                    incidents.append(inc)
            if m == plan.metrics[0]:
                first_scored = scored
        incidents.sort(key=lambda i: -i.peak_z)
        scope = self.scope_label(plan.filters)
        suffix = f" across {scope}" if scope else ""
        names = ", ".join(self.cat.label(m) for m in plan.metrics)
        filters = paid_only_filters(self.cat, plan.metrics[:1], plan.filters)[0]
        ans.sql.append(f"-- daily series scanned for each metric (full history, so the weekly pattern can be learned)\n" + inline_params(*compile_query(self.cat, plan.metrics[:1], DateRange(start=self.start, end=self.end, label=''), filters, grain='day')))
        if not incidents:
            ans.headline = f"No unusual days found in {names}{suffix} for {self.when(period)}."
            ans.narrative = [f"Each daily series was checked against its own weekly pattern. A day is flagged only when it is far outside normal (8 robust standard deviations) and at least 15% away from expected. Nothing met that bar in {self.when(period)}."]
            ans.followups = ["Any anomalies in orders over the last 12 months?", "Why did ROAS change last month?"]
            ans.tables.append(Table(title="Incidents", columns=[Column(key="note", label="Result")], rows=[{"note": "No incidents in this window."}]))
            return
        rows = []
        for inc in incidents[:12]:
            rows.append({"metric": self.cat.label(inc.metric), "_metric": inc.metric, "dates": describe(inc.start, inc.end), "direction": inc.direction, "observed": inc.observed, "expected": inc.expected,
                         "deviation": inc.deviation_pct, "peak_z": inc.peak_z, "where": "; ".join(f"{w} ({fmt.pct(min(s, 1.0), 0)})" for w, s in inc.where[:2]) or "all segments", "note": inc.context or ""})
        ans.tables.append(Table(title="Incidents, most severe first", columns=[
            Column(key="metric", label="Metric"), Column(key="dates", label="When"), Column(key="direction", label="Direction"), Column(key="observed", label="Observed", format="auto"),
            Column(key="expected", label="Expected", format="auto"), Column(key="deviation", label="Deviation", format="signed_percent"), Column(key="where", label="Where"), Column(key="note", label="Context")], rows=rows))
        top = incidents[0]
        tm = self.cat.metrics[top.metric]
        ans.headline = f"{len(incidents)} unusual incident{'s' if len(incidents) != 1 else ''} in {names}{suffix}, {self.when(period)}. The largest: {tm.label} {fmt.plain_pct(top.deviation_pct, 0)} {'above' if top.direction == 'up' else 'below'} expected on {describe(top.start, top.end)}."
        ans.facts += [top.deviation_pct, float(len(incidents))]
        paras = []
        for inc in incidents[:3]:
            mm = self.cat.metrics[inc.metric]
            sent = f"{mm.label} {self._be(inc.metric)} {fmt.plain_pct(inc.deviation_pct, 0)} {'above' if inc.direction == 'up' else 'below'} expected on {describe(inc.start, inc.end)} ({self._fv(inc.metric, inc.observed)} against {self._fv(inc.metric, inc.expected)} expected per day)"
            ans.facts += [inc.deviation_pct, inc.observed, inc.expected]
            if inc.where:
                w, s = inc.where[0]
                if s >= 0.95:
                    sent += f", almost entirely in {w}"
                else:
                    sent += f", with {w} accounting for about {fmt.pct(s, 0)} of the deviation"
                    ans.facts.append(s)
            sent += "."
            if inc.context:
                sent += f" Note: it {inc.context}."
            paras.append(sent)
        paras.append("These are statistical outliers, not diagnoses. A data gap or tracking problem looks the same as a real drop until the source is checked.")
        ans.narrative = paras
        # chart for the first metric
        m0 = plan.metrics[0]
        met0 = self.cat.metrics[m0]
        sc = first_scored if 'first_scored' in locals() else None
        if sc is not None:
            win = sc.loc[pd.Timestamp(period.start):pd.Timestamp(period.end)]
            spans = [Span(x0=i.start.isoformat(), x1=i.end.isoformat(), label=f"{i.direction} {fmt.plain_pct(i.deviation_pct, 0)}", kind="incident") for i in incidents if i.metric == m0]
            ans.charts.append(Chart(type="line", title=f"{met0.label}: actual and expected", x=[d.date().isoformat() for d in win.index],
                                    series=[Series(name="Actual", values=[_num(v) for v in win["actual"]]), Series(name="Expected", values=[_num(v) for v in win["expected"]], kind="expected")],
                                    y_format=met0.format, spans=spans))
        ans.followups = [f"Why did {self.cat.label(top.metric)} change around {describe(top.start, top.end)}?", f"{self.cat.label(top.metric)} by channel for {describe(top.start, top.end)}"]

    def _gap_on(self, filters: list[Filter], inc: an.Incident) -> str | None:
        if self._gaps.empty:
            return None
        chans = set(self._scope_channels(filters))
        hit = self._gaps[self._gaps["channel"].isin(chans) & self._gaps["date"].between(inc.start.isoformat(), inc.end.isoformat())]
        if hit.empty:
            return None
        names = ", ".join(sorted({self.cat.dim_label("channel", c) for c in hit["channel"]}))
        return f"coincides with missing data for {names}, so this is probably a data gap"

    def _localize(self, ans: Answer, metric: str, filters: list[Filter], inc: an.Incident, dim: str | None = None, drill: bool = True) -> list[tuple[str, float]]:
        dim = dim or self._seg_dim(filters) or "channel"
        win = DateRange(start=inc.start, end=inc.end, label="incident")
        b0, b1 = an.window_baseline(inc.start)
        base = DateRange(start=max(b0, self.start), end=b1, label="baseline") if b1 >= self.start else None
        if base is None or base.days < 7:
            return []
        w = self._measures(ans, win, filters, [dim]).set_index(dim)
        b = self._measures(ans, base, filters, [dim]).set_index(dim)
        allseg = sorted(set(w.index) | set(b.index))
        met = self.cat.metrics[metric]
        scores: dict[str, float] = {}
        if met.additive:
            for s in allseg:
                obs = float(w.loc[s, metric]) if s in w.index else 0.0
                exp = float(b.loc[s, metric]) / base.days * win.days if s in b.index else 0.0
                scores[s] = obs - exp
            total = sum(scores.values())
        else:
            N = sum(float(w.loc[s, met.numerator]) for s in w.index)
            D = sum(float(w.loc[s, met.denominator]) for s in w.index)
            if D == 0:
                return []
            robs = met.scale * N / D
            Dbase = float(b[met.denominator].sum())
            rbase = met.scale * float(b[met.numerator].sum()) / Dbase if Dbase else float("nan")
            for s in w.index:
                Ns, Ds = float(w.loc[s, met.numerator]), float(w.loc[s, met.denominator])
                if s in b.index and float(b.loc[s, met.denominator]) > 0 and Ds > 0:
                    rs = met.scale * float(b.loc[s, met.numerator]) / float(b.loc[s, met.denominator])
                    n_cf = (N - Ns + rs * Ds / met.scale)
                    scores[s] = robs - met.scale * n_cf / D
            total = robs - rbase
        if not scores or total == 0 or math.isnan(total):
            return []
        ranked = sorted(((s, sc / total) for s, sc in scores.items() if sc / total > 0.1), key=lambda x: -x[1])[:3]
        out = [(self.cat.dim_label(dim, s), share) for s, share in ranked]
        # when one channel carries most of it, name the campaign inside that channel too
        if drill and dim == "channel" and ranked and ranked[0][1] >= 0.6:
            inner_filters = [f for f in filters if f.dimension != "channel"] + [Filter(dimension="channel", values=[ranked[0][0]])]
            inner = self._localize(ans, metric, inner_filters, inc, dim="campaign", drill=False)
            if inner and inner[0][1] >= 0.6:
                out[0] = (f"{out[0][0]}: {inner[0][0]}", out[0][1])
        return out

    # ---- forecast -----------------------------------------------------------------------------------
    def _do_forecast(self, plan: QueryPlan, ans: Answer) -> None:
        m = plan.metrics[0]
        met = self.cat.metrics[m]
        if not met.additive:
            raise ValueError(f"Forecasts are available for totals such as revenue, orders, spend and sessions. {met.label} is a ratio; forecast its parts and divide, or ask about revenue.")
        filters = self._scope(plan, ans)
        horizon = plan.horizon_days or 28
        ser = self.series(ans, m, filters)
        fc = forecast_series(ser, horizon)
        scope = self.scope_label(filters)
        suffix = f" across {scope}" if scope else ""
        start, end = fc.dates[0], fc.dates[-1]
        ans.sql.append("-- history used for the forecast\n" + inline_params(*compile_query(self.cat, [m], DateRange(start=self.start, end=self.end, label=''), filters, grain='day')))
        ans.headline = f"{met.label}{suffix} is forecast at {self._fv(m, fc.total_mean)} over the next {horizon} days ({describe(start, end)}), with an 80% range of {self._fv(m, fc.total_lower)} to {self._fv(m, fc.total_upper)}."
        ans.facts += [fc.total_mean, fc.total_lower, fc.total_upper, fc.backtest_mape, fc.naive_mape, fc.interval_coverage]
        ans.narrative = [
            f"On the most recent 28 days, forecast from the data before them, this method was off by {fc.backtest_mape:.1f}% on average, against {fc.naive_mape:.1f}% for repeating the previous week.",
            f"The range comes from {fc.origins} past forecasts at the same horizon; {fmt.pct(fc.interval_coverage, 0)} of their daily outcomes fell inside the band by construction, so it describes how far off forecasts have typically been, not a guarantee.",
        ]
        ans.facts += [float(fc.origins)]
        window_holiday = holiday_in_window(start, end)
        if window_holiday:
            ans.caveats.append(f"The forecast window includes {window_holiday}. With only one year of history the model cannot learn annual seasonality, so a holiday peak is not in this forecast.")
        ans.caveats.append("The calibration uses overlapping past forecasts from a single year of data, so treat the range as a rough guide.")
        ans.tables.append(Table(title="Forecast by week", columns=[Column(key="week", label="Week"), Column(key="mean", label="Forecast", format="auto"), Column(key="low", label="Low (80%)", format="auto"), Column(key="high", label="High (80%)", format="auto")],
                                rows=[{"week": describe(a, b), "_metric": m, "mean": mean, "low": lo, "high": hi} for a, b, mean, lo, hi in fc.weekly]))
        recent = ser.iloc[-56:]
        xs = [d.date().isoformat() for d in recent.index] + [d.isoformat() for d in fc.dates]
        pad = [None] * len(recent)
        ans.charts.append(Chart(type="line", title=f"{met.label} per day: last 8 weeks and forecast", x=xs, y_format=met.format,
                                series=[Series(name="Actual", values=[_num(v) for v in recent] + [None] * len(fc.dates)),
                                        Series(name="Forecast", kind="forecast", values=[None] * (len(recent) - 1) + [_num(recent.iloc[-1])] + [_num(v) for v in fc.mean])],
                                band_lower=pad + [_num(v) for v in fc.lower], band_upper=pad + [_num(v) for v in fc.upper]))
        ans.followups = [f"Any anomalies in {met.label}?", f"{met.label} trend over the last 12 weeks"]

    # ==================================================================================================
    # grounding
    # ==================================================================================================
    def _ground(self, ans: Answer) -> None:
        if ans.plan and ans.plan.intent == "definition":
            # curated text from the semantic layer, not computed from data
            ans.grounding = {"checked": 0, "matched": 0, "unmatched": [], "hedging_flags": [], "passed": True, "note": "curated definition"}
            return
        evidence: list[float] = list(ans.facts)
        for t in ans.tables:
            for row in t.rows:
                for v in row.values():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        evidence.append(float(v))
        counts: list[int] = []
        if ans.plan:
            for r in (ans.plan.period, ans.plan.compare_to):
                if r:
                    counts.append(r.days)
                    counts += [int(x) for x in re.findall(r"\d+", r.label)]       # "last 4 weeks", "Q3 2026", ...
            if ans.plan.horizon_days:
                counts.append(ans.plan.horizon_days)
            if ans.plan.top_n:
                counts.append(ans.plan.top_n)
        counts += [len(t.rows) for t in ans.tables] + [1, 2, 3, 8, 12, 28]
        g = verify([ans.headline] + ans.narrative, evidence, counts)
        ans.grounding = {**g.model_dump(), "passed": g.passed}
        ans.facts = []

    # ==================================================================================================
    # supporting views for the app
    # ==================================================================================================
    def overview(self) -> dict[str, Any]:
        period = DateRange(start=self.end - timedelta(days=27), end=self.end, label="last 28 days")
        prev = self._previous(period)
        a = Answer(question="", as_of=self.end)
        paid = [Filter(dimension="channel_group", values=["paid"])]
        cards = []
        for key, filters in (("revenue", []), ("orders", []), ("roas", paid), ("cac", paid), ("cvr", []), ("spend", paid)):
            cur = self._run(a, [key], period, filters, show=False).iloc[0][key]
            old = self._run(a, [key], prev, filters, show=False).iloc[0][key]
            d1 = self._measures(a, period, filters, grain="day")
            d0 = self._measures(a, prev, filters, grain="day")
            ci = bootstrap_delta(self.cat, key, d0[BASE], d1[BASE])
            spark = self._run(a, [key], DateRange(start=self.end - timedelta(days=55), end=self.end, label=""), filters, grain="week", show=False)
            met = self.cat.metrics[key]
            cards.append({"key": key, "label": met.label, "format": met.format, "value": _num(cur), "previous": _num(old),
                          "change": _num((cur / old - 1) * 100) if old else None, "good": met.good, "clear": bool(ci and ci.clear),
                          "scope": "paid channels" if filters else "all channels", "spark": [_num(v) for v in spark[key]]})
        return {"period": {"start": period.start.isoformat(), "end": period.end.isoformat()}, "as_of": self.end.isoformat(), "cards": cards}

    def anomaly_feed(self, days: int = 365, limit: int = 12) -> list[dict[str, Any]]:
        a = Answer(question="", as_of=self.end)
        period = DateRange(start=max(self.start, self.end - timedelta(days=days - 1)), end=self.end, label="")
        plan = QueryPlan(intent="anomaly", metrics=["revenue", "orders", "spend", "cvr", "roas"], period=period)
        self._do_anomaly(plan, a)
        t = a.tables[0]
        return [r for r in t.rows if "metric" in r][:limit]

    def sample_questions(self) -> list[str]:
        return [
            "What was ROAS by channel last week?",
            "Why did paid social ROAS drop in June?",
            "Any anomalies in orders over the last 12 months?",
            "Which campaigns have the best CAC this quarter?",
            "Compare revenue this month to last month",
            "Why did display CAC go up in August?",
            "Forecast revenue for the next 4 weeks",
            "What is CAC?",
        ]
