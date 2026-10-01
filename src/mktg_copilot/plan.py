"""Typed contracts: the query plan the planner must produce, and the answer the engine returns.

A plan is *data*, validated against the semantic layer before anything touches the warehouse. This is the
structured-output contract: a rule-based planner or an LLM may propose a plan, but neither can run SQL.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Intent = Literal["summary", "compare", "explain_change", "trend", "rank", "anomaly", "forecast", "definition"]
Grain = Literal["day", "week", "month"]


class DateRange(BaseModel):
    start: date
    end: date
    label: str = ""

    @model_validator(mode="after")
    def _ordered(self) -> "DateRange":
        if self.end < self.start:
            raise ValueError("end before start")
        return self

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


class Filter(BaseModel):
    dimension: str
    values: list[str] = Field(min_length=1)


class QueryPlan(BaseModel):
    intent: Intent
    metrics: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    period: DateRange | None = None
    compare_to: DateRange | None = None
    grain: Grain | None = None
    top_n: int | None = Field(default=None, ge=1, le=50)
    order: Literal["asc", "desc"] | None = None
    horizon_days: int | None = Field(default=None, ge=1, le=56)
    subject: str | None = None            # for definition questions
    confidence: float = Field(default=1.0, ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("metrics", "group_by")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class Clarification(BaseModel):
    kind: Literal["clarify", "refuse"]
    message: str
    suggestions: list[str] = Field(default_factory=list)


class Column(BaseModel):
    key: str
    label: str
    format: str = "text"      # text | count | currency | currency2 | percent | multiple | date | signed_percent | number


class Table(BaseModel):
    title: str
    columns: list[Column]
    rows: list[dict[str, Any]]


class Series(BaseModel):
    name: str
    values: list[float | None]
    kind: Literal["actual", "forecast", "expected", "compare"] = "actual"


class Span(BaseModel):
    x0: str
    x1: str
    label: str = ""
    kind: Literal["period", "baseline", "incident", "event"] = "period"


class Chart(BaseModel):
    type: Literal["line", "bar", "diverging"]
    title: str
    x: list[str]
    series: list[Series]
    y_format: str = "number"
    band_lower: list[float | None] | None = None
    band_upper: list[float | None] | None = None
    spans: list[Span] = Field(default_factory=list)
    markers: list[dict[str, Any]] = Field(default_factory=list)   # {x, label}
    horizontal: bool = False


class Grounding(BaseModel):
    checked: int = 0
    matched: int = 0
    unmatched: list[str] = Field(default_factory=list)
    hedging_flags: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.checked == self.matched and not self.hedging_flags


class Retrieved(BaseModel):
    id: str
    title: str
    score: float


class Answer(BaseModel):
    status: Literal["ok", "clarify", "refused", "error"] = "ok"
    question: str
    as_of: date
    planner: str = "rules"
    headline: str = ""
    narrative: list[str] = Field(default_factory=list)        # paragraphs
    plan: QueryPlan | None = None
    tables: list[Table] = Field(default_factory=list)
    charts: list[Chart] = Field(default_factory=list)
    sql: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    retrieved: list[Retrieved] = Field(default_factory=list)
    grounding: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    facts: list[float] = Field(default_factory=list, exclude=True)   # numbers the narrator relied on (not sent to clients)
