from datetime import date

import pytest

from mktg_copilot.config import AS_OF, START, TODAY
from mktg_copilot.nlu.timeparse import TimeParser

tp = TimeParser(TODAY, START, AS_OF)

CASES = [
    ("roas last week", (date(2026, 9, 21), date(2026, 9, 27))),
    ("revenue last month", (date(2026, 8, 1), date(2026, 8, 31))),
    ("last 30 days", (date(2026, 8, 29), date(2026, 9, 27))),
    ("past 2 weeks", (date(2026, 9, 14), date(2026, 9, 27))),
    ("last three months", (date(2026, 6, 1), date(2026, 8, 31))),
    ("yesterday", (date(2026, 9, 27), date(2026, 9, 27))),
    ("Q2", (date(2026, 4, 1), date(2026, 6, 30))),
    ("Q4", (date(2025, 10, 1), date(2025, 12, 31))),
    ("in march 2026", (date(2026, 3, 1), date(2026, 3, 31))),
    ("in november", (date(2025, 11, 1), date(2025, 11, 30))),
    ("since august", (date(2026, 8, 1), date(2026, 9, 27))),
    ("between feb 1 and feb 14", (date(2026, 2, 1), date(2026, 2, 14))),
    ("from march to may", (date(2026, 3, 1), date(2026, 5, 31))),
    ("on black friday", (date(2025, 11, 28), date(2025, 11, 28))),
    ("black friday weekend", (date(2025, 11, 28), date(2025, 12, 1))),
    ("week of mar 2", (date(2026, 3, 2), date(2026, 3, 8))),
    ("orders on 2026-02-10", (date(2026, 2, 10), date(2026, 2, 10))),
    ("3/17/2026", (date(2026, 3, 17), date(2026, 3, 17))),
    ("ytd", (date(2026, 1, 1), date(2026, 9, 27))),
    ("this month", (date(2026, 9, 1), date(2026, 9, 27))),
    ("this quarter", (date(2026, 7, 1), date(2026, 9, 27))),
    ("last quarter", (date(2026, 4, 1), date(2026, 6, 30))),
    ("the 28-day trend", (date(2026, 8, 31), date(2026, 9, 27))),
    ("mar 20 2026", (date(2026, 3, 20), date(2026, 3, 20))),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_single_expression(text, expected):
    found = tp.find(text)
    assert found, text
    assert (found[0].start, found[0].end) == expected


def test_modal_may_is_not_a_month():
    assert tp.find("revenue may be wrong") == []


def test_month_pair_without_prepositions():
    found = tp.find("orders jan vs feb")
    assert [(e.start.month, e.end.month) for e in found] == [(1, 1), (2, 2)]


def test_this_week_has_no_data_on_a_monday():
    e = tp.find("this week")[0]
    assert (e.start, e.end) == (date(2026, 9, 21), date(2026, 9, 27)) and e.note


def test_overall_is_not_a_time_range():
    assert [e.label for e in tp.find("overall roas last week")] == ["last week"]


def test_clip_to_loaded_data():
    e = tp.find("Q3")[0]
    s, en, warn = tp.clip(e)
    assert en == AS_OF and warn
    out = TimeParser(TODAY, START, AS_OF).find("in 2019")
    assert out == []        # a year outside the data is refused by the planner, not parsed here
