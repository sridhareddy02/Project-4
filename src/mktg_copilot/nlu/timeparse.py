"""Turns phrases like "last week", "Q2", "since March 3" or "between Aug 1 and Aug 15" into date ranges.

Weeks run Monday to Sunday. "Today" is the day after the last loaded day, so "yesterday" is the latest data.
Everything ambiguous is resolved by a stated rule and reported back as an assumption.
"""
from __future__ import annotations

import calendar as pycal
import re
from dataclasses import dataclass
from datetime import date, timedelta

from ..calendar import NAMED_PERIODS

MONTHS = {m.lower(): i for i, m in enumerate(pycal.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(pycal.month_abbr) if m})
MONTHS["sept"] = 9
_MON = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = (rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|{_MON}\.? \d{{1,2}}(?!\d)(?:st|nd|rd|th)?(?:,? \d{{4}})?|\d{{1,2}}(?:st|nd|rd|th)? {_MON}(?: \d{{4}})?|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?)")
_PREP = r"(?:in|during|for|of|since|from|to|and|vs\.?|versus|against|than|with|between|through|until|by|over)"
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "twelve": 12, "fourteen": 14, "thirty": 30, "sixty": 60, "ninety": 90}
_N = rf"(\d+|{'|'.join(_NUM_WORDS)})"


@dataclass
class TimeExpr:
    start: date
    end: date
    label: str
    span: tuple[int, int]
    kind: str                  # relative | calendar | range | day | event | all
    note: str | None = None


def _n(tok: str) -> int:
    return int(tok) if tok.isdigit() else _NUM_WORDS[tok]


def _fmt(d: date) -> str:
    return d.strftime("%b ") + str(d.day)


def describe(start: date, end: date) -> str:
    if start == end:
        return f"{_fmt(start)}, {start.year}"
    if start.year == end.year:
        return f"{_fmt(start)} to {_fmt(end)}, {end.year}"
    return f"{_fmt(start)}, {start.year} to {_fmt(end)}, {end.year}"


def month_bounds(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, pycal.monthrange(y, m)[1])


def add_months(y: int, m: int, delta: int) -> tuple[int, int]:
    i = y * 12 + (m - 1) + delta
    return i // 12, i % 12 + 1


class TimeParser:
    def __init__(self, today: date, data_start: date, data_end: date):
        self.today = today
        self.data_start = data_start
        self.data_end = data_end

    # ---- helpers ------------------------------------------------------------------------------------
    def _latest_year(self, month: int, day: int | None = None) -> int:
        """Most recent year in which the month (or month/day) has begun on or before the last data day."""
        for y in (self.data_end.year, self.data_end.year - 1, self.data_end.year - 2):
            try:
                d = date(y, month, day or 1)
            except ValueError:
                continue
            if d <= self.data_end:
                return y
        return self.data_end.year

    def parse_date(self, tok: str) -> date | None:
        tok = tok.strip().lower().replace(",", "")
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", tok):
            mon, day, yr = int(m[2]), int(m[3]), m[1]
        elif m := re.fullmatch(rf"({_MON})\.? (\d{{1,2}})(?:st|nd|rd|th)?(?: (\d{{4}}))?", tok):
            mon, day, yr = MONTHS[m[1]], int(m[2]), m[3]
        elif m := re.fullmatch(rf"(\d{{1,2}})(?:st|nd|rd|th)? ({_MON})(?: (\d{{4}}))?", tok):
            mon, day, yr = MONTHS[m[2]], int(m[1]), m[3]
        elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", tok):
            mon, day, yr = int(m[1]), int(m[2]), m[3]
            if yr and len(yr) == 2:
                yr = "20" + yr
        else:
            return None
        try:
            year = int(yr) if yr else self._latest_year(mon, day)
            return date(year, mon, day)
        except ValueError:
            return None

    def _week_start(self, d: date) -> date:
        return d - timedelta(days=d.weekday())

    def _quarter(self, q: int, year: int | None) -> tuple[date, date, int]:
        if year is None:
            year = self.data_end.year
            if date(year, 3 * (q - 1) + 1, 1) > self.data_end:
                year -= 1
        s = date(year, 3 * (q - 1) + 1, 1)
        e = month_bounds(year, 3 * q)[1]
        return s, e, year

    # ---- main entry ---------------------------------------------------------------------------------
    def find(self, text: str) -> list[TimeExpr]:
        t = text.lower()
        found: list[TimeExpr] = []
        taken: list[tuple[int, int]] = []

        def free(span: tuple[int, int]) -> bool:
            return not any(span[0] < b and a < span[1] for a, b in taken)

        def add(expr: TimeExpr) -> None:
            if free(expr.span):
                found.append(expr)
                taken.append(expr.span)

        today, last = self.today, self.data_end

        # 1. explicit ranges: between X and Y / from X to Y (dates)
        for m in re.finditer(rf"\b(?:between|from) ({_DATE}) (?:and|to|through|until|-) ({_DATE})", t):
            a, b = self.parse_date(m[1]), self.parse_date(m[2])
            if a and b:
                if b < a:
                    a, b = b, a
                add(TimeExpr(a, b, describe(a, b), m.span(), "range"))
        # month ranges: from march to may / between june and august
        for m in re.finditer(rf"\b(?:between|from) ({_MON}) (?:and|to|through|until) ({_MON})(?: (\d{{4}}))?\b", t):
            m1, m2 = MONTHS[m[1]], MONTHS[m[2]]
            y2 = int(m[3]) if m[3] else self._latest_year(m2)
            y1 = y2 if m1 <= m2 else y2 - 1
            a, b = month_bounds(y1, m1)[0], month_bounds(y2, m2)[1]
            add(TimeExpr(a, b, describe(a, min(b, last)), m.span(), "range"))

        # 2. named retail periods
        for name in sorted(NAMED_PERIODS, key=len, reverse=True):
            for m in re.finditer(rf"\b{name}(?: weekend)?\b", t):
                a, b = NAMED_PERIODS[name]
                add(TimeExpr(a, b, name.title(), m.span(), "event"))

        # 3. since X
        for m in re.finditer(rf"\bsince ({_DATE})", t):
            a = self.parse_date(m[1])
            if a:
                add(TimeExpr(a, last, f"since {_fmt(a)}, {a.year}", m.span(), "range"))
        for m in re.finditer(rf"\bsince ({_MON})(?: (\d{{4}}))?\b", t):
            y = int(m[2]) if m[2] else self._latest_year(MONTHS[m[1]])
            a = date(y, MONTHS[m[1]], 1)
            add(TimeExpr(a, last, f"since {a.strftime('%B %Y')}", m.span(), "range"))

        # 4. rolling windows: last/past N days|weeks|months
        for m in re.finditer(rf"\b(?:last|past|previous|prior|trailing|recent) {_N}[- ](day|week|month)s?\b", t):
            n, unit = _n(m[1]), m[2]
            if unit == "day":
                a, b = last - timedelta(days=n - 1), last
                add(TimeExpr(a, b, f"last {n} days", m.span(), "relative"))
            elif unit == "week":
                a, b = last - timedelta(days=7 * n - 1), last
                add(TimeExpr(a, b, f"last {n} weeks", m.span(), "relative"))
            else:
                if n in (12,):
                    add(TimeExpr(self.data_start, last, "the last 12 months", m.span(), "all", "Used the full loaded history (one year)."))
                else:
                    cy, cm = today.year, today.month
                    sy, sm = add_months(cy, cm, -n)
                    ey, em = add_months(cy, cm, -1)
                    a, b = month_bounds(sy, sm)[0], month_bounds(ey, em)[1]
                    add(TimeExpr(a, b, f"the last {n} full months", m.span(), "relative",
                                 f"Read 'last {n} months' as the {n} most recent complete calendar months."))
        for m in re.finditer(r"\b(\d+)[- ]day\b", t):
            n = int(m[1])
            if 1 <= n <= 366:
                add(TimeExpr(last - timedelta(days=n - 1), last, f"last {n} days", m.span(), "relative"))

        # 5. year / to-date forms
        for m in re.finditer(r"\b(?:year to date|ytd|so far this year|this year)\b", t):
            a = date(today.year, 1, 1)
            add(TimeExpr(a, last, f"year to date ({describe(a, last)})", m.span(), "relative"))
        for m in re.finditer(r"\b(?:month to date|mtd|so far this month)\b", t):
            a = date(today.year, today.month, 1)
            add(TimeExpr(a, last, f"month to date ({describe(a, last)})", m.span(), "relative"))
        for m in re.finditer(r"\b(?:the )?(?:last|past) (?:year|twelve months|12 months)\b|\btrailing twelve months\b|\bpast year\b", t):
            add(TimeExpr(self.data_start, last, "the last 12 months", m.span(), "all", "Used the full loaded history (one year)."))
        for m in re.finditer(r"\b(?:all time|to date|full history|entire period|whole period|the whole year|full year|across the year)\b", t):
            add(TimeExpr(self.data_start, last, "the full period", m.span(), "all"))

        # 6. last / this week | month | quarter
        for m in re.finditer(r"\blast week\b|\bprevious week\b|\bprior week\b", t):
            ws = self._week_start(today) - timedelta(days=7)
            add(TimeExpr(ws, ws + timedelta(days=6), "last week", m.span(), "relative",
                         f"Read 'last week' as the last complete Monday to Sunday week ({describe(ws, ws + timedelta(days=6))})."))
        for m in re.finditer(r"\bthis week\b", t):
            ws = self._week_start(today)
            if ws > last:
                a = last - timedelta(days=6)
                add(TimeExpr(a, last, "the last 7 days", m.span(), "relative",
                             "This week has no data yet (it starts today), so the last 7 days were used."))
            else:
                add(TimeExpr(ws, last, "this week so far", m.span(), "relative"))
        for m in re.finditer(r"\blast month\b|\bprevious month\b|\bprior month\b", t):
            y, mo = add_months(today.year, today.month, -1)
            a, b = month_bounds(y, mo)
            add(TimeExpr(a, b, a.strftime("%B %Y"), m.span(), "relative", f"Read 'last month' as {a.strftime('%B %Y')}."))
        for m in re.finditer(r"\bthis month\b", t):
            a = date(today.year, today.month, 1)
            add(TimeExpr(a, last, f"{a.strftime('%B %Y')} so far", m.span(), "relative",
                         f"Read 'this month' as {a.strftime('%B')} to date ({describe(a, last)})."))
        for m in re.finditer(r"\blast quarter\b|\bprevious quarter\b|\bprior quarter\b", t):
            q = (today.month - 1) // 3 + 1
            y = today.year
            q -= 1
            if q == 0:
                q, y = 4, y - 1
            s, e, _ = self._quarter(q, y)
            add(TimeExpr(s, e, f"Q{q} {y}", m.span(), "relative", f"Read 'last quarter' as Q{q} {y}."))
        for m in re.finditer(r"\bthis quarter\b|\bquarter to date\b|\bqtd\b", t):
            q = (today.month - 1) // 3 + 1
            s, _, y = self._quarter(q, today.year)
            add(TimeExpr(s, last, f"Q{q} {y} so far", m.span(), "relative"))
        for m in re.finditer(r"\byesterday\b", t):
            add(TimeExpr(last, last, "yesterday", m.span(), "day"))
        for m in re.finditer(r"\btoday\b", t):
            add(TimeExpr(last, last, "yesterday", m.span(), "day", "Today's data is not loaded yet, so the latest day (yesterday) was used."))

        # 7. quarters: Q3, Q3 2026, third quarter
        ordinal = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
        for m in re.finditer(r"\bq([1-4])(?: ?(\d{4}))?\b", t):
            q, y = int(m[1]), int(m[2]) if m[2] else None
            s, e, y = self._quarter(q, y)
            add(TimeExpr(s, e, f"Q{q} {y}", m.span(), "calendar"))
        for m in re.finditer(r"\b(first|second|third|fourth|1st|2nd|3rd|4th) quarter(?: (?:of )?(\d{4}))?\b", t):
            q = ordinal[m[1]]
            s, e, y = self._quarter(q, int(m[2]) if m[2] else None)
            add(TimeExpr(s, e, f"Q{q} {y}", m.span(), "calendar"))

        for m in re.finditer(rf"\bweek of ({_DATE})", t):
            d = self.parse_date(m[1])
            if d:
                ws = self._week_start(d)
                add(TimeExpr(ws, ws + timedelta(days=6), f"the week of {_fmt(ws)}, {ws.year}", m.span(), "calendar"))

        # 8. single dates (with optional "on"), before bare months so "march 3" is a day
        for m in re.finditer(rf"\b(?:on )?({_DATE})", t):
            d = self.parse_date(m[1])
            if d:
                add(TimeExpr(d, d, describe(d, d), m.span(1), "day"))

        # 9. bare months (and "march 2026")
        for m in re.finditer(rf"\b({_MON})(?: (\d{{4}}))?\b", t):
            tok = m[1]
            before = t[: m.start()].rstrip()
            has_prep = bool(re.search(rf"\b{_PREP}$", before)) or bool(re.search(r"\b(?:the )?month of$", before))
            after = t[m.end():]
            adjacent = bool(re.match(rf"\s*(?:vs\.?|versus|and|to|through|compared (?:to|with)|against)\s+{_MON}\b", after))
            if tok in ("may", "mar") and not (has_prep or m[2] or adjacent):
                continue
            if not (has_prep or m[2] or adjacent or m.start() == 0 or re.search(r"\b(?:compare|show|what|how|and|then|the)$", before)):
                continue
            mo = MONTHS[tok]
            y = int(m[2]) if m[2] else self._latest_year(mo)
            a, b = month_bounds(y, mo)
            note = None
            if b > last:
                note = f"{a.strftime('%B %Y')} is still in progress; data runs to {_fmt(last)}."
            add(TimeExpr(a, b, a.strftime("%B %Y"), m.span(), "calendar", note))

        # 10. a bare year: "in 2026", "2025"
        for m in re.finditer(r"\b(20\d{2})\b", t):
            y = int(m[1])
            a, b = date(y, 1, 1), date(y, 12, 31)
            if b >= self.data_start and a <= self.data_end:
                add(TimeExpr(a, b, str(y), m.span(), "calendar", f"{y} is only partly loaded; data runs {describe(max(a, self.data_start), min(b, self.data_end))}."))

        found.sort(key=lambda e: e.span[0])
        return found

    # ---- shared clipping ----------------------------------------------------------------------------
    def clip(self, e: TimeExpr) -> tuple[date, date, str | None]:
        """Clip to the loaded data. Returns (start, end, warning)."""
        s, en = e.start, e.end
        warn = None
        if en < self.data_start or s > self.data_end:
            return s, en, f"No data is loaded for {e.label}. The warehouse covers {describe(self.data_start, self.data_end)}."
        if s < self.data_start or en > self.data_end:
            s, en = max(s, self.data_start), min(en, self.data_end)
            warn = f"Clipped to the loaded data ({describe(s, en)})."
        return s, en, warn
