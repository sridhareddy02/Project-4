"""Rule-based planner: question text -> validated QueryPlan, or a clarification / refusal.

The planner only ever selects from the semantic layer's vocabulary, so there is no path from the text of a
question to arbitrary SQL. Anything it cannot map is reported back rather than guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import AS_OF, START, TODAY
from ..plan import Clarification, DateRange, Filter, QueryPlan, Retrieved
from ..semantic import Catalog
from .retrieval import Retriever
from .timeparse import TimeExpr, TimeParser, describe

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
DEFAULT_ANOMALY_METRICS = ["revenue", "orders", "spend", "cvr", "roas"]

# Ambiguous bare words only count as a channel in a context that makes the meaning clear.
_CONTEXTUAL = {
    "display": r"(?:\b(?:for|in|on|of|within|from|to|vs|versus|and|than|compare|only|just)\s+display\b(?!\s+(?:the|all|me|a|an)\b)|\bdisplay\s+(?:channel|campaigns?|ads?|spend|performance|roas|cac|cpc|cpm|ctr|only)\b)",
    "social": r"(?:\b(?:for|in|on|of|within|from|to|vs|versus|and|than|compare|only|just)\s+social\b|\bsocial\s+(?:channel|campaigns?|ads?|spend|performance|roas|cac|cpc|cpm|ctr|only)\b)",
    "organic": r"(?:\b(?:for|in|on|of|within|from|to|vs|versus|and|than|compare|only|just)\s+organic\b|\borganic\s+(?:channel|traffic|performance|sessions|orders|revenue)\b)",
    "email": r"\bemails?\b",
    "south": r"(?:\b(?:for|in|on|of|within|from|to|vs|versus|and|than|compare|only|just)\s+(?:the\s+)?south\b|\bsouth(?:ern)?\s+region\b)",
    "west": r"(?:\b(?:for|in|on|of|within|from|to|vs|versus|and|than|compare|only|just)\s+(?:the\s+)?west\b|\bwest(?:ern)?\s+region\b)",
    "paid": r"\bpaid(?:\s+(?:channels?|media|only))\b|\b(?:only|just)\s+paid\b",
    "owned": r"\bowned(?:\s+(?:channels?|media|only))?\b",
}
_SQLISH = re.compile(
    r"(;|--|/\*|\b(?:drop|delete|insert|update|alter|truncate|attach|pragma|create)\s+(?:table|database|from|into|view|index)\b|\bunion\s+select\b|"
    r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\b|\bsystem prompt\b|\byou are now\b|\bdisregard\b|\bjailbreak\b)", re.I)


# Platforms and brands people name that the warehouse does not break out (it holds channel-level totals).
PLATFORMS = ("facebook", "instagram", "tiktok", "google", "youtube", "linkedin", "pinterest", "snapchat", "twitter", "amazon", "bing", "meta", "reddit", "whatsapp")
_BENIGN_AFTER_BY = {"the", "a", "an", "end", "now", "day", "days", "week", "weeks", "month", "months", "quarter", "year", "date", "monday", "tuesday", "wednesday",
                    "thursday", "friday", "saturday", "sunday", "yesterday", "today", "next", "last", "this", "how", "much", "more", "less", "about", "over", "at", "default",
                    "all", "everything", "far", "then", "mid", "early", "late", "hand", "itself", "any", "each", "one", "two", "three", "percent", "paid", "owned"}


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'")
    t = re.sub(r"\s*[-–—]\s*", " ", t)          # "Win-back" and "Search - Brand Core" become words
    t = re.sub(r"[^a-z0-9$%./' ,]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class PlannerResult:
    plan: QueryPlan | None = None
    clarification: Clarification | None = None
    notes: list[str] = field(default_factory=list)       # warnings to surface (ignored text, unsupported side requests)
    retrieved: list[Retrieved] = field(default_factory=list)
    rationale: dict = field(default_factory=dict)         # what matched, for the inspector


class RulePlanner:
    name = "rules"

    def __init__(self, cat: Catalog, retriever: Retriever, today: date = TODAY, data_start: date = START, data_end: date = AS_OF):
        self.cat = cat
        self.retriever = retriever
        self.today, self.data_start, self.data_end = today, data_start, data_end
        self.times = TimeParser(today, data_start, data_end)
        self._metric_lex = self._build_metric_lexicon()
        self._value_lex = self._build_value_lexicon()
        self._unsupported_lex = self._build_unsupported_lexicon()

    # ---- lexicons -----------------------------------------------------------------------------------
    def _build_metric_lexicon(self) -> list[tuple[str, str]]:
        pairs = []
        for m in self.cat.metrics.values():
            for s in {m.key.replace("_", " "), m.label.lower(), *m.synonyms}:
                pairs.append((normalize(s), m.key))
        return sorted(set(pairs), key=lambda p: -len(p[0]))

    def _build_value_lexicon(self) -> list[tuple[str, str, str]]:
        out = []
        for dim in self.cat.dimensions.values():
            for value, (label, syns) in dim.values.items():
                for s in {label, value.replace("_", " "), *syns}:
                    out.append((normalize(s), dim.key, value))
        # more specific phrases first, so "paid search" beats "search" and a campaign name beats its channel
        return sorted(set(out), key=lambda p: -len(p[0]))

    def _build_unsupported_lexicon(self) -> list[tuple[str, str]]:
        return sorted(((normalize(t), k) for k, v in self.cat.unsupported.items() for t in v["terms"]), key=lambda p: -len(p[0]))

    @staticmethod
    def _find(term: str, text: str) -> re.Match | None:
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)

    # ---- extraction ---------------------------------------------------------------------------------
    def extract_metrics(self, t: str) -> tuple[list[str], str]:
        masked, found = t, []
        for term, key in self._metric_lex:
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", masked):
                found.append((m.start(), key))
                masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
        found.sort()
        return list(dict.fromkeys(k for _, k in found)), masked

    def extract_values(self, t: str) -> list[tuple[str, str]]:
        masked, found = t, []
        for term, dim, value in self._value_lex:
            if term in _CONTEXTUAL:
                m = re.search(_CONTEXTUAL[term], masked)
                spans = [m] if m else []
            else:
                spans = list(re.finditer(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", masked))
            for m in spans:
                found.append((m.start(), dim, value))
                masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
        found.sort()
        seen, out = set(), []
        for _, dim, value in found:
            if (dim, value) not in seen:
                seen.add((dim, value))
                out.append((dim, value))
        return out

    def extract_group_by(self, t: str) -> list[str]:
        dims = {"channel": "channel", "channels": "channel", "campaign": "campaign", "campaigns": "campaign", "region": "region",
                "regions": "region", "objective": "objective", "objectives": "objective", "channel group": "channel_group", "channel groups": "channel_group"}
        names = "|".join(sorted(dims, key=len, reverse=True))
        out: list[str] = []
        for m in re.finditer(rf"\b(?:by|per|across|for each|each|broken down by|breakdown by|split by|segmented by|grouped by)\s+((?:{names})(?:\s*(?:,|and|then|&)\s*(?:{names}))*)\b", t):
            for tok in re.findall(names, m[1]):
                out.append(dims[tok])
        if not out:
            m = re.search(rf"\b(?:which|what|rank|compare|top|best|worst|bottom|biggest|largest|highest|lowest)\b[^?]*?\b({names})\b", t)
            if m:
                out.append(dims[m[1]])
        return list(dict.fromkeys(out))[:2]

    def detect_grain(self, t: str) -> str | None:
        if re.search(r"\b(?:daily|by day|per day|day by day|each day|day over day)\b", t):
            return "day"
        if re.search(r"\b(?:weekly|by week|per week|week by week|each week)\b", t):
            return "week"
        if re.search(r"\b(?:monthly|by month|per month|month by month|each month)\b", t):
            return "month"
        return None

    def detect_top_n(self, t: str) -> int | None:
        m = re.search(r"\b(?:top|best|worst|bottom|lowest|highest)\s+(\d+|" + "|".join(_NUM_WORDS) + r")\b", t)
        if m:
            tok = m[1]
            return int(tok) if tok.isdigit() else _NUM_WORDS[tok]
        return None

    # ---- intent -------------------------------------------------------------------------------------
    def detect_intent(self, t: str, n_times: int, has_group: bool, metric_keys: list[str]) -> tuple[str, bool]:
        """Returns (intent, explicit cue found)."""
        why = re.search(r"\b(?:why|what (?:caused|drove|changed|happened|is driving|explains?)|reasons?|drivers?|root cause|behind the|explain (?:the |this |why )?(?:drop|fall|decline|rise|increase|change|jump|dip|decrease|spike))\b", t)
        if why and not re.search(r"\banomal\w*|\boutliers?\b", t):
            return "explain_change", True
        if re.search(r"\b(?:anomal\w*|unusual|abnormal|outliers?|spikes?|unexpected|strange|weird|something (?:wrong|off|broke)|data issues?|tracking (?:break|issue|problem)|irregular\w*|fluctuat\w*|odd)\b", t):
            return "anomaly", True
        if re.search(r"\b(?:forecast\w*|predict\w*|projection|project(?:ed)?|expect(?:ed)? to|going to be|will be|outlook)\b", t) or re.search(r"\bnext (?:\d+|" + "|".join(_NUM_WORDS) + r")?\s*(?:day|week|month)s?\b", t):
            return "forecast", True
        if re.search(r"\b(?:why|what (?:caused|drove|changed|happened|is driving|explains?)|reasons?|drivers?|root cause|behind the|explain (?:the |this |why )?(?:drop|fall|decline|rise|increase|change|jump|dip|decrease|spike))\b", t):
            return "explain_change", True
        if re.search(r"\b(?:top|best|worst|bottom|rank\w*|highest|lowest|most|least|leaderboard|biggest|largest|smallest|fewest|better|worse|outperform\w*|underperform\w*)\b", t) and has_group:
            return "rank", True
        if re.search(r"\b(?:compare\w*|versus|vs\.?|against|than|difference|change[sd]?|growth|grew|up or down|wow|mom|week over week|month over month|week on week|month on month|increase[sd]?|decrease[sd]?|dropped|fell|rose)\b", t) or n_times >= 2:
            return "compare", True
        if re.search(r"\b(?:trend\w*|over time|time series|timeline|daily|weekly|monthly|by day|by week|by month|per day|per week|history|evolv\w*|trajectory)\b", t):
            return "trend", True
        if re.search(r"\b(?:top|best|worst|bottom|rank\w*|highest|lowest|leaderboard)\b", t):
            return "rank", True
        return "summary", False

    def _is_definition(self, t: str, metric_keys: list[str], n_times: int) -> bool:
        if n_times or re.search(r"\b(?:our|my|we|us|current|latest)\b", t):
            return False
        if re.search(r"\b(?:how (?:is|are|do|does|did|should|can)\b.*\b(?:calculat\w*|comput\w*|defin\w*|measur\w*|work\w*|found|detect\w*|decid\w*|determin\w*)|formula|definition of|what does\b.*\b(?:mean|stand for)|stand for|meaning of)\b", t):
            return True
        if metric_keys and re.match(r"^(?:what(?: is|'s| are)|define|explain|tell me about|describe)\s+(?:the |a |an )?(?:metric )?[a-z ]+\??$", t) and len(metric_keys) == 1:
            return True
        return False

    def _unknown_entity(self, t: str, masked_values: str) -> Clarification | None:
        """Name something the warehouse does not have, and the question would otherwise be answered about everything."""
        plat = next((p for p in PLATFORMS if re.search(rf"(?<![a-z0-9]){p}(?![a-z0-9])", t)), None)
        if plat:
            return Clarification(kind="clarify", message=f"This warehouse holds channel-level totals, not individual platforms such as {plat.title()}. Channels are: "
                                 + ", ".join(self.cat.dim_label("channel", v) for v in self.cat.dim_values("channel")) + ".",
                                 suggestions=["What was ROAS by channel last week?", "Compare Paid Social and Paid Search ROAS in Q3"])
        known_words = {w for term, *_ in self._value_lex for w in term.split()}
        for kind in ("region", "channel"):
            # only a prepositional reference ("for the Antarctica region") counts as naming an entity
            for m in re.finditer(rf"\b(?:in|for|of|on|within|from|to|across|only)\s+(?:the\s+)?([a-z][a-z]+)\s+{kind}s?\b", t):
                w = m.group(1)
                if w in known_words or w in {"the", "each", "every", "all", "any", "which", "what", "that", "this", "per", "by", "a", "an", "our", "my", "same", "other", "paid", "owned", "single", "one", "top", "best", "worst", "main", "biggest", "highest", "lowest"}:
                    continue
                opts = ", ".join(self.cat.dim_label(kind, v) for v in self.cat.dim_values(kind))
                return Clarification(kind="clarify", message=f"I don't have a {kind} called “{w}”. Available {kind}s: {opts}.", suggestions=[f"What was revenue by {kind} last week?"])
        m = re.search(r"\b(?:by|for each|broken down by|split by) ([a-z]+)", t)   # one space: blanks left by masked metrics do not match
        if m:
            w = m.group(1).rstrip("s")
            known = {"channel", "campaign", "region", "objective", "channel", "day", "week", "month", "quarter", "year"}
            if w not in known and m.group(1) not in _BENIGN_AFTER_BY and w not in _BENIGN_AFTER_BY:
                dims = ", ".join(d.label.lower() for d in self.cat.dimensions.values())
                return Clarification(kind="clarify", message=f"I can't break results down by “{m.group(1)}”. Available breakdowns: {dims}, and time (day, week, month).", suggestions=["What was revenue by channel last week?"])
        yrs = {int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", t)}
        out = sorted(y for y in yrs if not (self.data_start.year <= y <= self.data_end.year))
        if out:
            return Clarification(kind="refuse", message=f"No data is loaded for {out[0]}. The warehouse covers {describe(self.data_start, self.data_end)}.", suggestions=self._starter_suggestions())
        return None

    # ---- main ---------------------------------------------------------------------------------------
    def plan(self, question: str) -> PlannerResult:
        raw = question.strip()
        res = PlannerResult()
        if not raw:
            res.clarification = Clarification(kind="clarify", message="Ask a question about marketing performance, for example “What was ROAS by channel last week?”", suggestions=self._starter_suggestions())
            return res
        notes: list[str] = []
        if _SQLISH.search(raw):
            notes.append("Part of the question looked like SQL or an instruction to the system and was ignored. Queries are generated only from the semantic layer.")
        t = normalize(_SQLISH.sub(" ", raw))

        # unsupported phrases are matched first and masked, so "net revenue" is not mistaken for "revenue"
        unsupported_keys: list[str] = []
        masked_u = t
        for term, key in self._unsupported_lex:
            for m in re.finditer(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", masked_u):
                unsupported_keys.append(key)
                masked_u = masked_u[: m.start()] + " " * (m.end() - m.start()) + masked_u[m.end():]
        unsupported = [(k, self.cat.unsupported[k]) for k in dict.fromkeys(unsupported_keys)]

        metric_keys, masked = self.extract_metrics(masked_u)
        values = self.extract_values(masked_u)
        res.retrieved = self.retriever.search(t, k=3)
        res.rationale = {"normalized": t, "metrics": metric_keys, "values": [f"{d}={v}" for d, v in values]}

        times = self.times.find(t)
        group_by = self.extract_group_by(t)

        definition = self._is_definition(t, metric_keys, len(times))
        if definition:
            return self._definition(t, metric_keys, res, notes)


        # Unsupported-only questions are refused with a reason rather than answered with something nearby.
        if unsupported and not metric_keys:
            k, info = unsupported[0]
            sugg = [f"What was {self.cat.label(s)} last week?" for s in info.get("suggest", [])][:3]
            res.clarification = Clarification(kind="refuse", message=f"I can't answer that: {info['why']}.", suggestions=sugg or self._starter_suggestions())
            res.notes = notes
            return res
        guard = self._unknown_entity(masked, "")
        if guard is not None:
            res.clarification, res.notes = guard, notes
            return res
        for k, info in unsupported:
            notes.append(f"I can't compute {info['terms'][0]}: {info['why']}. Answering the rest of the question.")

        intent, explicit = self.detect_intent(t, len(times), bool(group_by), metric_keys)

        if not metric_keys and not times and re.match(r"^(?:what(?:'s| is| are| does)|define|explain|describe|tell me about|how (?:is|are|do|does|did|should|can)|are|is|can)\b", t) and not re.search(r"\b(?:our|my|we|us)\b", t):
            hits = self.retriever.search(t, k=3, kind="glossary")
            if hits and hits[0].score >= 0.12:
                res.plan = QueryPlan(intent="definition", subject=hits[0].id, confidence=round(min(0.5 + hits[0].score, 0.9), 2))
                res.retrieved, res.notes = hits, notes
                return res

        if not metric_keys:
            if intent in ("anomaly",):
                metric_keys = list(DEFAULT_ANOMALY_METRICS)
                res.rationale["defaulted_metrics"] = True
            elif intent == "forecast":
                metric_keys = ["revenue"]
                res.rationale["defaulted_metrics"] = True
            else:
                res.clarification = self._no_metric(t, res.retrieved)
                res.notes = notes
                return res

        assumptions: list[str] = []
        if res.rationale.get("defaulted_metrics"):
            assumptions.append("No metric was named, so " + ("revenue was used." if intent == "forecast" else "revenue, orders, spend, conversion rate and ROAS were scanned."))
        if re.search(r"\bconversions?\b", t) and "orders" in metric_keys and not re.search(r"conversion rate", t):
            assumptions.append("Conversions are counted as orders in this warehouse.")

        # filters from named channels, regions, campaigns and groups
        filters: dict[str, list[str]] = {}
        for dim, value in values:
            filters.setdefault(dim, []).append(value)
        # a campaign named in full already pins its channel; drop the redundant channel filter
        if "campaign" in filters and "channel" in filters:
            camp_channels = {self._campaign_channel(c) for c in filters["campaign"]}
            filters["channel"] = [c for c in filters["channel"] if c not in camp_channels] or []
            if not filters["channel"]:
                del filters["channel"]
        # a group-by on a dimension that is also filtered to one value is redundant noise
        group_by = [g for g in group_by if not (g in filters and len(filters[g]) == 1)]
        # an explicit "owned" request on a spend metric needs a clear answer, not a silent empty table
        flt = [Filter(dimension=d, values=v) for d, v in filters.items()]

        # time handling -------------------------------------------------------------------------------
        grain = self.detect_grain(t)
        period, compare_to, t_assump = self._periods(times, intent, t, grain)
        assumptions += t_assump

        top_n = self.detect_top_n(t)
        order = None
        if intent == "rank":
            if not group_by:
                group_by = ["campaign"] if re.search(r"\bcampaign", t) else (["region"] if re.search(r"\bregion", t) else ["channel"])
            order = self._rank_order(t, self.cat.metrics[metric_keys[0]])
            if top_n is None and group_by == ["campaign"]:
                top_n = 5
        if intent == "trend":
            grain = grain or self._default_grain(period)
        if intent == "forecast":
            horizon, h_assump = self._horizon(t)
            assumptions += h_assump
        else:
            horizon = None
        if intent in ("compare", "explain_change") and len(metric_keys) > 1 and intent == "explain_change":
            metric_keys = metric_keys[:1]
            assumptions.append(f"Explained the first metric named ({self.cat.label(metric_keys[0])}).")

        if intent == "compare" and len(times) <= 1 and not group_by:
            multi = next((f for f in flt if f.dimension in ("channel", "region", "campaign", "objective") and len(f.values) >= 2), None)
            if multi is not None:
                intent, group_by, compare_to = "summary", [multi.dimension], None
                assumptions.append(f"Compared the named {multi.dimension}s side by side over the same period.")
                period = period if period is not None else None

        conf = 0.5 + (0.2 if not res.rationale.get("defaulted_metrics") else 0) + (0.15 if times else 0.0) + (0.1 if explicit else 0.0)
        plan = QueryPlan(intent=intent, metrics=metric_keys, group_by=group_by, filters=flt, period=period, compare_to=compare_to,
                         grain=grain, top_n=top_n, order=order, horizon_days=horizon, confidence=round(min(conf, 1.0), 2),
                         assumptions=assumptions)
        res.plan, res.notes = plan, notes
        return res

    # ---- pieces -------------------------------------------------------------------------------------
    def _campaign_channel(self, campaign: str) -> str | None:
        return {"Search": "paid_search", "Social": "paid_social", "Display": "display", "Email": "email", "Organic": "organic_search"}.get(campaign.split(" - ")[0])

    def _definition(self, t: str, metric_keys: list[str], res: PlannerResult, notes: list[str]) -> PlannerResult:
        if metric_keys:
            res.plan = QueryPlan(intent="definition", metrics=metric_keys[:1], subject=metric_keys[0], confidence=0.95)
        else:
            hits = self.retriever.search(t, k=3, kind="glossary")
            if not hits or hits[0].score < 0.12:
                res.clarification = Clarification(kind="clarify", message="Which metric or method would you like explained?", suggestions=["What is ROAS?", "How does anomaly detection work?", "How are comparisons computed?"])
                return res
            res.plan = QueryPlan(intent="definition", subject=hits[0].id, confidence=round(min(0.5 + hits[0].score, 0.9), 2))
            res.retrieved = hits
        res.notes = notes
        return res

    def _no_metric(self, t: str, hits: list[Retrieved]) -> Clarification:
        metric_hits = [h for h in self.retriever.search(t, k=3, kind="metric") if h.score >= 0.12]
        if metric_hits:
            labels = ", ".join(h.title for h in metric_hits)
            return Clarification(kind="clarify", message=f"Which metric do you mean? The closest matches are {labels}.",
                                 suggestions=[f"What was {h.title} last week?" for h in metric_hits])
        if not self._looks_marketing(t):
            return Clarification(kind="refuse", message="I can only answer questions about the marketing metrics in this warehouse (spend, revenue, orders, ROAS, CAC and similar).", suggestions=self._starter_suggestions())
        return Clarification(kind="clarify", message="Which metric should I look at? For example ROAS, CAC, revenue, orders or conversion rate.", suggestions=self._starter_suggestions())

    def _looks_marketing(self, t: str) -> bool:
        return bool(re.search(r"\b(?:marketing|campaign\w*|channel\w*|ads?|performance|efficien\w*|funnel|customers?|traffic|region\w*|budget|results?|doing|going)\b", t))

    def _starter_suggestions(self) -> list[str]:
        return ["What was ROAS by channel last week?", "Why did paid social ROAS change in June?", "Any anomalies in orders over the last 90 days?"]

    def _rank_order(self, t: str, metric) -> str:
        good_desc = metric.good == "up"
        if re.search(r"\b(?:most|more|best|top) (?:efficient|effective)\b|\befficient\b|\bcheapest\b", t):
            return "desc" if good_desc else "asc"
        if re.search(r"\b(?:least|less|worst) (?:efficient|effective)\b", t):
            return "asc" if good_desc else "desc"
        if re.search(r"\b(?:lowest|least|fewest|smallest|bottom)\b", t):
            return "asc"
        if re.search(r"\b(?:highest|most|largest|biggest|priciest|most expensive)\b", t):
            return "desc"
        if re.search(r"\b(?:worst|weakest|underperform\w*|worse)\b", t):
            return "asc" if good_desc else "desc"
        return "desc" if good_desc else "asc"          # best / top / better

    def _default_grain(self, period: DateRange | None) -> str:
        if period is None:
            return "week"
        n = period.days
        return "day" if n <= 62 else ("week" if n <= 200 else "month")

    def _horizon(self, t: str) -> tuple[int, list[str]]:
        m = re.search(r"\bnext (\d+|" + "|".join(_NUM_WORDS) + r")?\s*(day|week|month)s?\b", t)
        if not m:
            return 28, ["No horizon was given, so the next 4 weeks were forecast."]
        n = int(m[1]) if (m[1] and m[1].isdigit()) else _NUM_WORDS.get(m[1] or "", 1)
        days = n * {"day": 1, "week": 7, "month": 30}[m[2]]
        if days > 56:
            return 56, [f"Forecasts are limited to 8 weeks, so the horizon was shortened from {days} days."]
        return days, []

    def _periods(self, times: list[TimeExpr], intent: str, t: str, grain: str | None = None) -> tuple[DateRange | None, DateRange | None, list[str]]:
        notes: list[str] = []
        if intent == "definition":
            return None, None, notes

        def to_range(e: TimeExpr) -> DateRange | None:
            s, en, warn = self.times.clip(e)
            if warn and warn.startswith("No data"):
                notes.append(warn)
                return None
            if warn:
                notes.append(warn)
            if e.note:
                notes.append(e.note)
            return DateRange(start=s, end=en, label=e.label)

        period = compare_to = None
        if len(times) >= 2:
            a, b = to_range(times[0]), to_range(times[1])
            if a and b:
                period, compare_to = (a, b) if a.start >= b.start else (b, a)
                if a.start < b.start:
                    notes.append("Treated the later period as the current one and the earlier as the baseline.")
        elif len(times) == 1:
            period = to_range(times[0])

        last = self.data_end
        if period is None:
            if intent in ("compare", "explain_change"):
                # last full week against the one before it
                ws = self.today - timedelta(days=self.today.weekday())
                period = DateRange(start=ws - timedelta(days=7), end=ws - timedelta(days=1), label="last week")
                notes.append(f"No period was given, so last week ({describe(period.start, period.end)}) was compared with the week before.")
            elif intent == "forecast":
                period = DateRange(start=self.data_start, end=last, label="the full history")
            elif intent == "anomaly":
                period = DateRange(start=last - timedelta(days=89), end=last, label="last 90 days")
                notes.append("No period was given, so the last 90 days were scanned.")
            elif intent == "trend":
                if grain == "month":
                    period = DateRange(start=self.data_start, end=last, label="the full history")
                    notes.append("No period was given, so the full history was used.")
                elif grain == "day":
                    period = DateRange(start=last - timedelta(days=27), end=last, label="last 28 days")
                    notes.append("No period was given, so the last 28 days were used.")
                else:
                    period = DateRange(start=last - timedelta(days=83), end=last, label="last 12 weeks")
                    notes.append("No period was given, so the last 12 weeks were used.")
            else:
                period = DateRange(start=last - timedelta(days=27), end=last, label="last 28 days")
                notes.append("No period was given, so the last 28 days were used.")
        if intent in ("compare", "explain_change") and compare_to is None and period is not None:
            n = period.days
            e = period.start - timedelta(days=1)
            s = e - timedelta(days=n - 1)
            if s < self.data_start:
                s = self.data_start
            if e >= s:
                compare_to = DateRange(start=s, end=e, label=f"the {n} days before" if n > 1 else "the day before")
                notes.append(f"Compared with the previous period of the same length ({describe(s, e)}).")
            else:
                notes.append("There is no earlier data to compare with.")
        return period, compare_to, notes
