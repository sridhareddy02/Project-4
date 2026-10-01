"""Grounding verifier: every number written in an answer must trace back to a value computed from the warehouse.

The narrator writes prose from computed values. This check runs independently afterwards: it extracts each
number from the prose and looks for an evidence value (a table cell, a derived figure, or a count taken from
the plan) that renders to the same text at the same precision. A number it cannot trace is reported, and a
test proves it catches altered figures. It also flags causal wording, because a dashboard comparison cannot
establish cause.
"""
from __future__ import annotations

import re
from typing import Iterable

from .plan import Grounding

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_DATEY = [
    r"\d{4}-\d{2}-\d{2}",
    rf"{_MONTH}\.? \d{{1,2}}(?!\d)(?:st|nd|rd|th)?(?:,? \d{{4}})?",
    rf"\d{{1,2}}(?!\d) {_MONTH}(?: \d{{4}})?",
    r"\bq[1-4]\b(?: \d{4})?",
    rf"\b{_MONTH} \d{{4}}\b",
    r"\b20\d{2}\b",
]
_NUM = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)\s?([KMB]|%|x)?(?![\w])", re.I)
# Numbers that are policy rather than data: the confidence levels and detection thresholds the product documents.
POLICY_PERCENTS = {80.0, 95.0, 15.0}
POLICY_COUNTS = {8}
_CAUSAL = re.compile(r"\b(?:caused|because of|due to|as a result of|resulted in|thanks to|led to|is responsible for|proves?|proved)\b", re.I)


def _strip_dates(text: str) -> str:
    for pat in _DATEY:
        text = re.sub(pat, " ", text, flags=re.I)
    return text


def extract_numbers(text: str) -> list[tuple[str, float, int, str]]:
    """Returns (token, value in display units, decimals, suffix) for every number in the text."""
    out = []
    for m in _NUM.finditer(_strip_dates(text)):
        raw = m.group(1).replace(",", "")
        dec = len(raw.split(".")[1]) if "." in raw else 0
        out.append((m.group(0).strip(), float(raw), dec, (m.group(2) or "").lower()))
    return out


def _candidates(v: float, suffix: str) -> list[float]:
    """Display-unit values that evidence value v could be rendered as, given the suffix in the text."""
    v = abs(v)
    if suffix == "%":
        return [v * 100.0, v]
    if suffix == "k":
        return [v / 1e3]
    if suffix == "m":
        return [v / 1e6]
    if suffix == "b":
        return [v / 1e9]
    return [v]


def _matches(shown: float, dec: int, suffix: str, evidence: Iterable[float]) -> bool:
    tol = 0.5 * 10 ** (-dec) + 1e-9
    shown = abs(shown)
    for e in evidence:
        for c in _candidates(e, suffix):
            if abs(round(c, dec) - shown) <= tol or abs(c - shown) <= tol:
                return True
    return False


def verify(prose: list[str], evidence: Iterable[float], allowed_counts: Iterable[int] = ()) -> Grounding:
    """prose: the paragraphs to check. evidence: every number the answer can legitimately cite."""
    ev = [float(e) for e in evidence if e is not None]
    counts = {int(c) for c in allowed_counts}
    checked, matched, unmatched = 0, 0, []
    text = " ".join(prose)
    for token, val, dec, suffix in extract_numbers(text):
        # a bare small integer that is a plan-derived count (days, top N, rows) needs no table value
        if not suffix and dec == 0 and (int(val) in counts or int(val) in POLICY_COUNTS):
            continue
        if suffix == "%" and val in POLICY_PERCENTS:
            continue
        checked += 1
        if _matches(val, dec, suffix, ev):
            matched += 1
        else:
            unmatched.append(token)
    flags = sorted({m.group(0).lower() for m in _CAUSAL.finditer(text)})
    return Grounding(checked=checked, matched=matched, unmatched=unmatched, hedging_flags=flags)
