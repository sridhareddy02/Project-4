import type { KpiCard, Overview } from "../types";
import { formatBy, signedPercent } from "../format";

function Spark({ values }: { values: (number | null)[] }) {
  const v = values.filter((x): x is number => x != null);
  if (v.length < 2) return null;
  const lo = Math.min(...v), hi = Math.max(...v), w = 120, h = 28;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 3 - ((x - lo) / (hi - lo || 1)) * (h - 6)).toFixed(1)}`).join(" ");
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden="true">
      <polyline points={pts} fill="none" stroke="var(--s1)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Kpi({ k }: { k: KpiCard }) {
  const ch = k.change;
  const improved = ch == null ? null : (ch > 0) === (k.good === "up");
  const tone = ch == null || !k.clear ? "flat" : improved ? "good" : "bad";
  const word = ch == null ? "" : ch > 0 ? "up" : "down";
  return (
    <li className="card kpi">
      <span className="kpi-label">{k.label}</span>
      <span className="kpi-value">{formatBy(k.format, k.value)}</span>
      {ch != null && (
        <span className={`kpi-delta ${tone}`} aria-label={`${word} ${Math.abs(ch).toFixed(1)} percent versus the previous 28 days, ${k.clear ? "a clear change" : "within normal variation"}`}>
          <span aria-hidden="true">{ch > 0 ? "▲" : "▼"} {signedPercent(ch)}</span>
          <span className="kpi-sub" aria-hidden="true">{k.clear ? "clear change" : "within noise"}</span>
        </span>
      )}
      <span className="kpi-sub">{k.scope}, last 28 days</span>
      <Spark values={k.spark} />
    </li>
  );
}

export function KpiStrip({ data }: { data: Overview | null }) {
  if (!data) return <ul className="kpis" aria-busy="true">{Array.from({ length: 6 }, (_, i) => <li key={i} className="card kpi loading" style={{ minHeight: 120 }} />)}</ul>;
  return <ul className="kpis" aria-label="Key metrics, last 28 days against the 28 before">{data.cards.map((k) => <Kpi k={k} key={k.key} />)}</ul>;
}
