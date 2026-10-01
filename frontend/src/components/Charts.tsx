import { useEffect, useMemo, useRef, useState } from "react";
import type { Chart } from "../types";
import { formatBy, niceTicks, shortDate, tickLabel } from "../format";

/** Width of an element, tracked with a ResizeObserver, so text stays at its real pixel size on phones. */
function useWidth(ref: React.RefObject<HTMLElement>, fallback = 640): number {
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e.contentRect.width))));
    ro.observe(el);
    setW(Math.max(280, Math.round(el.getBoundingClientRect().width || fallback)));
    return () => ro.disconnect();
  }, [ref, fallback]);
  return w;
}

const COLORS: Record<string, string> = { actual: "var(--s1)", forecast: "var(--s3)", expected: "var(--axis)", compare: "var(--s2)" };
const seriesColor = (kind: string, i: number) => (kind === "actual" ? ["var(--s1)", "var(--s2)", "var(--s3)"][i % 3] : COLORS[kind] ?? "var(--s1)");

function Legend({ chart }: { chart: Chart }) {
  if (chart.series.length < 2) return null;
  return (
    <div className="legend" aria-hidden="true">
      {chart.series.map((s, i) => (
        <span key={s.name}>
          <i style={{ borderColor: seriesColor(s.kind, i), borderTopStyle: s.kind === "expected" ? "dashed" : "solid" }} />
          {s.name}
        </span>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------------------
   Line chart: crosshair that snaps to the nearest x, one tooltip listing every series, incident spans,
   forecast band, end labels, and keyboard control (arrow keys, Home, End).
   ------------------------------------------------------------------------------------------------------- */
function LineChart({ chart }: { chart: Chart }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const W = useWidth(wrapRef);
  const multi = chart.series.length >= 2;
  const m = { l: 52, r: multi ? 64 : 14, t: 10, b: 26 };
  const H = 230;
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const n = chart.x.length;
  const [hover, setHover] = useState<number | null>(null);

  const { y0, y1, ticks } = useMemo(() => {
    const vals: number[] = [];
    chart.series.forEach((s) => s.values.forEach((v) => v != null && vals.push(v)));
    chart.band_lower?.forEach((v) => v != null && vals.push(v));
    chart.band_upper?.forEach((v) => v != null && vals.push(v));
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const t = niceTicks(lo, hi, 5);
    return { y0: t[0], y1: t[t.length - 1], ticks: t };
  }, [chart]);

  const X = (i: number) => m.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = (v: number) => m.t + ih - ((v - y0) / (y1 - y0 || 1)) * ih;
  const xIndex = (x: string) => {
    const i = chart.x.indexOf(x);
    if (i >= 0) return i;
    const nearest = chart.x.findIndex((d) => d >= x);
    return nearest < 0 ? n - 1 : nearest;
  };

  const paths = chart.series.map((s) => {
    const segs: string[] = [];
    let cur = "";
    s.values.forEach((v, i) => {
      if (v == null) { if (cur) segs.push(cur); cur = ""; return; }
      cur += `${cur ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
    });
    if (cur) segs.push(cur);
    return segs;
  });

  const band = useMemo(() => {
    if (!chart.band_lower || !chart.band_upper) return null;
    const idx = chart.band_lower.map((v, i) => (v != null && chart.band_upper![i] != null ? i : -1)).filter((i) => i >= 0);
    if (!idx.length) return null;
    const up = idx.map((i) => `${X(i).toFixed(1)},${Y(chart.band_upper![i] as number).toFixed(1)}`);
    const dn = [...idx].reverse().map((i) => `${X(i).toFixed(1)},${Y(chart.band_lower![i] as number).toFixed(1)}`);
    return `M${up.join("L")}L${dn.join("L")}Z`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chart, W]);

  const xTickIdx = useMemo(() => {
    const want = Math.max(2, Math.min(7, Math.floor(iw / 80)));
    return Array.from({ length: want }, (_, k) => Math.round((k / (want - 1)) * (n - 1)));
  }, [iw, n]);

  const move = (clientX: number) => {
    const el = wrapRef.current;
    if (!el || n === 0) return;
    const rect = el.getBoundingClientRect();
    const px = clientX - rect.left - m.l;
    setHover(Math.max(0, Math.min(n - 1, Math.round((px / iw) * (n - 1)))));
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowRight") setHover((h) => Math.min(n - 1, (h ?? -1) + 1));
    else if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? n) - 1));
    else if (e.key === "Home") setHover(0);
    else if (e.key === "End") setHover(n - 1);
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };

  // end labels, nudged apart so they never overlap
  const ends = multi
    ? chart.series
        .map((s, i) => {
          let li = -1;
          s.values.forEach((v, k) => v != null && (li = k));
          return li < 0 ? null : { name: s.name, y: Y(s.values[li] as number), color: seriesColor(s.kind, i) };
        })
        .filter((e): e is { name: string; y: number; color: string } => e !== null)
        .sort((a, b) => a.y - b.y)
    : [];
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;

  const hx = hover != null ? X(hover) : null;
  const active = hover != null ? chart.spans.filter((s) => hover >= xIndex(s.x0) && hover <= xIndex(s.x1)) : [];
  const mk = hover != null ? chart.markers.filter((k) => xIndex(k.x) === hover) : [];
  const label = chart.x.length ? `${chart.title}. ${n} points from ${shortDate(chart.x[0])} to ${shortDate(chart.x[n - 1])}. Use the arrow keys to read values.` : chart.title;

  return (
    <div className="chart">
      <h4>{chart.title}</h4>
      <Legend chart={chart} />
      <div
        className="chart-wrap" ref={wrapRef} tabIndex={0} role="group" aria-label={label}
        onPointerMove={(e) => move(e.clientX)} onPointerLeave={() => setHover(null)} onKeyDown={onKey} onBlur={() => setHover(null)}
      >
        <svg width={W} height={H} role="img" aria-hidden="true">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={m.l} x2={W - m.r} y1={Y(t)} y2={Y(t)} stroke="var(--grid)" strokeWidth={1} />
              <text x={m.l - 8} y={Y(t) + 4} textAnchor="end">{tickLabel(chart.y_format, t)}</text>
            </g>
          ))}
          {xTickIdx.map((i) => (
            <text key={i} x={X(i)} y={H - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"}>{shortDate(chart.x[i])}</text>
          ))}
          {chart.spans.map((s, i) => {
            const a = X(xIndex(s.x0)), b = X(xIndex(s.x1));
            return <rect key={i} x={a - 3} width={Math.max(6, b - a + 6)} y={m.t} height={ih} fill="var(--span)" />;
          })}
          {band && <path d={band} fill="var(--s3)" opacity={0.16} />}
          {chart.series.map((s, si) =>
            paths[si].map((d, k) => (
              <path key={`${si}-${k}`} d={d} fill="none" stroke={seriesColor(s.kind, si)} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                    strokeDasharray={s.kind === "expected" ? "4 4" : s.kind === "forecast" ? "6 3" : undefined} />
            )),
          )}
          {chart.markers.map((k, i) => {
            const idx = xIndex(k.x);
            return <path key={i} d={`M${X(idx) - 5},${m.t + 2} L${X(idx) + 5},${m.t + 2} L${X(idx)},${m.t + 11}Z`} fill="var(--span-line)" />;
          })}
          {ends.map((e) => (
            <text key={e.name} x={W - m.r + 8} y={e.y + 4} style={{ fill: "var(--text)", fontWeight: 600 }}>{e.name.length > 9 ? e.name.slice(0, 8) + "…" : e.name}</text>
          ))}
          {hx != null && (
            <g>
              <line x1={hx} x2={hx} y1={m.t} y2={m.t + ih} stroke="var(--axis)" strokeWidth={1} />
              {chart.series.map((s, si) => {
                const v = s.values[hover as number];
                return v == null ? null : <circle key={si} cx={hx} cy={Y(v)} r={4} fill={seriesColor(s.kind, si)} stroke="var(--panel-2)" strokeWidth={2} />;
              })}
            </g>
          )}
        </svg>
        {hover != null && hx != null && (
          <div className="tt" style={{ left: Math.min(Math.max(8, hx + 12), Math.max(8, W - 176)), top: 8 }} aria-live="polite">
            <div className="d">{shortDate(chart.x[hover])}</div>
            {chart.series.map((s, si) => {
              const v = s.values[hover];
              if (v == null) return null;
              return (
                <div className="r" key={si}>
                  <span><span className="k" style={{ borderColor: seriesColor(s.kind, si) }} />{s.name}</span>
                  <b>{formatBy(chart.y_format, v)}</b>
                </div>
              );
            })}
            {chart.band_lower && chart.band_upper && chart.band_lower[hover] != null && (
              <div className="r"><span>80% range</span><b>{formatBy(chart.y_format, chart.band_lower[hover])} to {formatBy(chart.y_format, chart.band_upper[hover])}</b></div>
            )}
            {[...active, ...mk].map((s, i) => <div className="n" key={i}>{s.label}</div>)}
          </div>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------------------------------------
   Horizontal bars (magnitude) and diverging bars (change). Each bar is its own hover and focus target.
   ------------------------------------------------------------------------------------------------------- */
function Bars({ chart, diverging }: { chart: Chart; diverging: boolean }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const W = useWidth(wrapRef);
  const vals = chart.series[0]?.values ?? [];
  const rowH = 26;
  const labelW = Math.min(190, Math.max(96, Math.round(W * 0.34)));
  const m = { l: labelW, r: 64, t: 4, b: 4 };
  const H = m.t + m.b + chart.x.length * rowH;
  const iw = W - m.l - m.r;
  const nums = vals.filter((v): v is number => v != null);
  const hi = Math.max(0, ...nums), lo = Math.min(0, ...nums);
  const span = diverging ? Math.max(Math.abs(hi), Math.abs(lo)) || 1 : hi || 1;
  const zero = diverging ? m.l + iw / 2 : m.l;
  const scale = diverging ? Math.max(10, iw / 2 - 58) / span : iw / span;   // leave room for the value label beside each bar
  const [hover, setHover] = useState<number | null>(null);
  const trunc = (s: string) => (s.length > Math.floor(labelW / 6.4) ? s.slice(0, Math.floor(labelW / 6.4) - 1) + "…" : s);

  return (
    <div className="chart">
      <h4>{chart.title}</h4>
      {diverging && <div className="legend" aria-hidden="true"><span><i style={{ borderColor: "var(--s1)" }} />Increase</span><span><i style={{ borderColor: "var(--s2)" }} />Decrease</span></div>}
      <div className="chart-wrap" ref={wrapRef}>
        <svg width={W} height={H} role="img" aria-label={`${chart.title}: ${chart.x.map((x, i) => `${x} ${formatBy(chart.y_format, vals[i])}`).join("; ")}`}>
          {diverging && <line x1={zero} x2={zero} y1={0} y2={H} stroke="var(--axis)" strokeWidth={1} />}
          {chart.x.map((label, i) => {
            const v = vals[i];
            if (v == null) return null;
            const w = Math.max(2, Math.abs(v) * scale);
            const x = v >= 0 ? zero : zero - w;
            const y = m.t + i * rowH + 5;
            const color = diverging ? (v >= 0 ? "var(--s1)" : "var(--s2)") : "var(--s1)";
            const textX = v >= 0 ? zero + w + 6 : zero - w - 6;
            return (
              <g key={label} tabIndex={0} onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} style={{ outline: "none" }}>
                <rect x={0} y={m.t + i * rowH} width={W} height={rowH} fill="transparent" />
                <text x={diverging ? 0 : 0} y={y + 11} style={{ fill: hover === i ? "var(--text)" : undefined }}>{trunc(label)}<title>{label}</title></text>
                <rect x={x} y={y} width={w} height={14} rx={4} fill={color} opacity={hover == null || hover === i ? 1 : 0.55} />
                <text x={textX} y={y + 11} textAnchor={v >= 0 ? "start" : "end"} style={{ fill: "var(--text)", fontWeight: 600 }}>{formatBy(chart.y_format, v)}</text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

export function ChartView({ chart }: { chart: Chart }) {
  if (chart.type === "line") return <LineChart chart={chart} />;
  return <Bars chart={chart} diverging={chart.type === "diverging"} />;
}
