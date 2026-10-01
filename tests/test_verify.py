import pytest

from mktg_copilot.verify import extract_numbers, verify


def ok(text, evidence, counts=()):
    g = verify([text], evidence, counts)
    return g.checked == g.matched and not g.hedging_flags


def test_accepts_rounded_and_scaled_renderings():
    assert ok("ROAS was 3.12x.", [3.1234])
    assert ok("Revenue was $1.84M.", [1_843_310.25])
    assert ok("Revenue was $72.8K.", [72_803.4])
    assert ok("The rate was 4.7%.", [0.04731])
    assert ok("It rose 6.2%.", [6.237])
    assert ok("It fell 6.2%.", [-6.237])                      # sign-insensitive: the verb carries the direction
    assert ok("Orders were 21,846.", [21846.0])


def test_catches_altered_numbers():
    assert not ok("ROAS was 3.52x.", [3.1234])
    assert not ok("Revenue was $1.94M.", [1_843_310.25])
    assert not ok("The rate was 5.7%.", [0.04731])
    g = verify(["ROAS was 3.52x and CAC was $48.73."], [3.1234, 48.73])
    assert g.unmatched == ["3.52x"] and g.matched == 1


def test_dates_quarters_and_years_are_not_numbers():
    assert extract_numbers("For Sep 21 to Sep 27, 2026 and Q3 2026, on 2025-11-28 and June 2026.") == []


def test_plan_counts_and_policy_constants():
    assert ok("Over the last 28 days.", [], counts=[28])
    assert not ok("Over the last 29 days.", [], counts=[28])
    assert ok("The 95% range excludes zero.", [])             # a documented confidence level, not data


def test_flags_causal_language():
    g = verify(["Revenue fell because of the tracking change."], [])
    assert g.hedging_flags == ["because of"] and not g.passed
    assert verify(["Revenue fell; it is associated with the change."], []).passed


def test_precision_follows_the_digits_shown():
    assert ok("ROAS was 3.1x.", [3.1234])          # one decimal: 3.1234 rounds to 3.1
    assert not ok("ROAS was 3.2x.", [3.1234])
    assert not ok("ROAS was 3.13x.", [])           # no evidence at all
