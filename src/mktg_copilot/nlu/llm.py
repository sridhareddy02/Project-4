"""Optional LLM planner: an OpenAI-compatible chat model proposes a QueryPlan as JSON.

The model never writes SQL and never sees data. It is shown the semantic layer's vocabulary plus retrieved
definitions, and must answer with a JSON plan. The plan is parsed by the same pydantic schema and checked by
the same allowlist as the rule-based planner; anything invalid is retried once with the error, then replaced by
the rule-based plan. Fields the schema does not know (for example a smuggled "sql" key) are dropped.

Not exercised against a live model in this repository: the tests use a stub HTTP transport.
"""
from __future__ import annotations

import json
import os
from datetime import date

import httpx
from pydantic import ValidationError

from ..plan import Clarification, QueryPlan
from ..semantic import Catalog
from ..validate import validate_plan
from .planner import PlannerResult, RulePlanner
from .retrieval import Retriever

SYSTEM = """You translate a marketing analytics question into a JSON query plan. You do not answer the question and you never write SQL.
Reply with one JSON object and nothing else, in one of these shapes:
  {{"kind": "plan", "plan": {{...}}}}
  {{"kind": "clarify", "message": "...", "suggestions": ["..."]}}
  {{"kind": "refuse", "message": "..."}}   (use when the question needs data or metrics that are not available)

A plan has: intent (one of summary, compare, explain_change, trend, rank, anomaly, forecast, definition),
metrics (keys from the catalog), group_by (dimension keys), filters ([{{"dimension": key, "values": [value keys]}}]),
period and compare_to ({{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "label": "..."}}), grain (day|week|month),
top_n, order (asc|desc), horizon_days, subject (for definitions), assumptions (strings).
Today is {today}. Data covers {start} to {end}. Weeks run Monday to Sunday. Use only keys that appear in the catalog.
Spend-based metrics (roas, cac, cpa, cpc, cpm) exist only for paid channels.

Catalog:
{catalog}

Relevant definitions:
{context}
"""


class LLMPlanner:
    name = "llm"

    def __init__(self, cat: Catalog, retriever: Retriever, fallback: RulePlanner, start: date, end: date,
                 base_url: str | None = None, api_key: str | None = None, model: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 20.0):
        self.cat, self.retriever, self.fallback = cat, retriever, fallback
        self.start, self.end = start, end
        self.base_url = (base_url or os.environ.get("COPILOT_LLM_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.environ.get("COPILOT_LLM_API_KEY", "")
        self.model = model or os.environ.get("COPILOT_LLM_MODEL", "gpt-4o-mini")
        self.client = httpx.Client(transport=transport, timeout=timeout)

    @classmethod
    def from_env(cls, cat, retriever, fallback, start, end) -> "LLMPlanner | None":
        if not os.environ.get("COPILOT_LLM_API_KEY"):
            return None
        return cls(cat, retriever, fallback, start, end)

    # ------------------------------------------------------------------------------------------------
    def _messages(self, question: str, hits) -> list[dict]:
        ctx = "\n".join(f"- {d.title}: {d.text[:400]}" for d in (self.retriever.get(h.id) for h in hits) if d)
        system = SYSTEM.format(today=self.end.isoformat(), start=self.start.isoformat(), end=self.end.isoformat(),
                               catalog=json.dumps(self.cat.catalog_json(), separators=(",", ":"))[:6000], context=ctx or "(none)")
        return [{"role": "system", "content": system}, {"role": "user", "content": question}]

    def _call(self, messages: list[dict]) -> str:
        r = self.client.post(f"{self.base_url}/chat/completions",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"model": self.model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _parse(self, text: str) -> tuple[QueryPlan | Clarification | None, str | None]:
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            return None, f"not valid JSON ({e.msg})"
        kind = obj.get("kind")
        if kind in ("clarify", "refuse"):
            return Clarification(kind=kind, message=str(obj.get("message", ""))[:500], suggestions=[str(x)[:200] for x in obj.get("suggestions", [])][:4]), None
        if kind != "plan" or not isinstance(obj.get("plan"), dict):
            return None, "expected kind 'plan', 'clarify' or 'refuse'"
        try:
            plan = QueryPlan.model_validate(obj["plan"])        # unknown keys such as "sql" are ignored
        except ValidationError as e:
            return None, "plan failed schema validation: " + "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:3])
        problems = validate_plan(plan, self.cat, self.start, self.end)
        if problems:
            return None, "plan failed the allowlist: " + " ".join(problems[:3])
        return plan, None

    def plan(self, question: str) -> PlannerResult:
        hits = self.retriever.search(question, k=3)
        messages = self._messages(question, hits)
        error = None
        for attempt in range(2):
            try:
                text = self._call(messages)
            except (httpx.HTTPError, KeyError, ValueError) as e:
                error = f"the model call failed ({type(e).__name__})"
                break
            parsed, error = self._parse(text)
            if parsed is not None:
                res = PlannerResult(retrieved=hits, rationale={"attempts": attempt + 1})
                if isinstance(parsed, Clarification):
                    res.clarification = parsed
                else:
                    res.plan = parsed
                return res
            messages = messages + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": f"That reply was rejected: {error}. Reply again with a single valid JSON object."}]
        res = self.fallback.plan(question)
        res.notes = [f"The LLM planner's reply was unusable ({error}), so the rule-based planner was used."] + res.notes
        return res
