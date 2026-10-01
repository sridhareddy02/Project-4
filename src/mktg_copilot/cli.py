"""Command line: build the warehouse, ask a question, run the evaluation, serve the API, export the demo."""
from __future__ import annotations

import argparse
import json
import sys

from . import config


def _print_answer(a, show_sql: bool = False) -> None:
    print(f"[{a.status}] {a.headline}")
    for p in a.narrative:
        print(f"  {p}")
    for t in a.tables[:2]:
        print(f"  -- {t.title}")
        for r in t.rows[:8]:
            print("    ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if not k.startswith("_")})
    if a.assumptions:
        print("  assumptions:", *a.assumptions, sep="\n    - ")
    if a.warnings:
        print("  warnings:", *a.warnings, sep="\n    - ")
    if a.caveats:
        print("  caveats:", *a.caveats, sep="\n    - ")
    g = a.grounding
    if g:
        print(f"  grounding: {g.get('matched')}/{g.get('checked')} numbers traced, unmatched={g.get('unmatched')}, flags={g.get('hedging_flags')}")
    if show_sql:
        for s in a.sql:
            print("  SQL>", s.replace("\n", "\n       "))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mktg_copilot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="generate the synthetic warehouse")
    a = sub.add_parser("ask", help="ask a question")
    a.add_argument("question")
    a.add_argument("--sql", action="store_true")
    a.add_argument("--json", action="store_true")
    e = sub.add_parser("eval", help="run the evaluation suites and write reports/")
    e.add_argument("--check", action="store_true", help="exit non-zero if a quality gate fails")
    s = sub.add_parser("serve", help="run the API (and the built frontend if present)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    sub.add_parser("catalog-doc", help="write docs/metrics.md from the semantic layer")
    d = sub.add_parser("export-demo", help="write a static snapshot of answers for the GitHub Pages demo")
    d.add_argument("--out", default=str(config.ROOT / "frontend" / "public" / "demo"))
    args = ap.parse_args(argv)

    if args.cmd == "build":
        from .data.warehouse import build_warehouse
        print("built", build_warehouse())
        return 0
    if args.cmd == "ask":
        from .engine import Engine
        ans = Engine().ask(args.question)
        print(ans.model_dump_json(indent=2) if args.json else "", end="")
        if not args.json:
            _print_answer(ans, args.sql)
        return 0
    if args.cmd == "eval":
        from .evals.runner import run_all
        return run_all(check=args.check)
    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("mktg_copilot.api.app:app", host=args.host, port=args.port)
        return 0
    if args.cmd == "catalog-doc":
        from .data.warehouse import connect_readonly
        from .data.spec import campaign_aliases
        from .semantic import Catalog
        cat = Catalog.load(connect_readonly(), campaign_aliases())
        lines = ["# Metric catalog", "", "Generated from `src/mktg_copilot/metrics.yaml` by `python -m mktg_copilot catalog-doc`. Do not edit by hand.", "",
                 "Ratios are always the ratio of summed numerators and denominators, never an average of daily ratios.", "",
                 "| Metric | Formula | Better when | Scope | Plain meaning |", "|---|---|---|---|---|"]
        for m in cat.metrics.values():
            formula = f"`SUM({m.numerator}) / SUM({m.denominator})" + (f" x {m.scale:g}`" if m.scale != 1 else "`") if m.kind == "ratio" else f"`SUM({m.key})`"
            lines.append(f"| {m.label} | {formula} | {('higher' if m.good == 'up' else 'lower') if m.kind == 'ratio' else 'n/a'} | {'paid channels only' if m.requires_spend else 'all channels'} | {cat.definitions.get(m.key, {}).get('meaning', '')} |")
        lines += ["", "## Funnel factors used by driver analysis", "", "Each chain multiplies back to the metric exactly.", ""]
        for k, chain in cat.drivers.items():
            lines.append(f"* **{cat.label(k)}** = " + " x ".join(c[2] for c in chain))
        lines += ["", "## Dimensions", ""] + [f"* **{d.label}**: " + ", ".join(v[0] for v in d.values.values()) for d in cat.dimensions.values()]
        lines += ["", "## Questions the copilot refuses, and why", ""] + [f"* **{', '.join(u['terms'][:3])}**: {u['why']}." for u in cat.unsupported.values()]
        (config.ROOT / "docs" / "metrics.md").write_text("\n".join(lines) + "\n")
        print("wrote docs/metrics.md")
        return 0
    if args.cmd == "export-demo":
        from .evals.demo import export_demo
        export_demo(args.out)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
