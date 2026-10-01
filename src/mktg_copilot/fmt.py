"""Number formatting shared by narratives and tables. The React app mirrors these rules (frontend/src/format.ts)."""
from __future__ import annotations

import math
import numbers


def _bad(v) -> bool:
    return v is None or (isinstance(v, numbers.Real) and (math.isnan(float(v)) or math.isinf(float(v))))


def money(v: float) -> str:
    a = abs(v)
    if a >= 1e6:
        return f"${v / 1e6:.2f}M"
    if a >= 1e4:
        return f"${v / 1e3:.1f}K"
    return f"${v:,.0f}"


def money2(v: float) -> str:
    return f"${v:,.2f}"


def count(v: float) -> str:
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.2f}M"
    if a >= 1e4:
        return f"{v / 1e3:.1f}K"
    return f"{v:,.0f}"


def pct(ratio: float, d: int = 1) -> str:
    return f"{ratio * 100:.{d}f}%"


def mult(v: float) -> str:
    return f"{v:.2f}x"


def signed_pct(p: float, d: int = 1) -> str:
    """p is already in percent units."""
    return f"{p:+.{d}f}%"


def plain_pct(p: float, d: int = 1) -> str:
    return f"{abs(p):.{d}f}%"


def by_format(fmt: str, v) -> str:
    if _bad(v):
        return "n/a"
    return {
        "currency": money, "currency2": money2, "count": count, "percent": pct, "multiple": mult,
        "signed_percent": signed_pct, "number": lambda x: f"{x:,.2f}",
    }.get(fmt, str)(v)


def clean(v):
    """JSON-safe number: NaN and infinity become None."""
    if _bad(v):
        return None
    return float(v) if isinstance(v, numbers.Real) and not isinstance(v, bool) else v
