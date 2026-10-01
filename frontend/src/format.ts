// Number formatting. Mirrors mktg_copilot/fmt.py so a number reads the same in prose and in tables.
import type { Cell } from "./types";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function money(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${Math.round(v).toLocaleString("en-US")}`;
}
export const money2 = (v: number): string => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export function count(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(1)}K`;
  return Math.round(v).toLocaleString("en-US");
}
export const percent = (ratio: number, d = 1): string => `${(ratio * 100).toFixed(d)}%`;
export const multiple = (v: number): string => `${v.toFixed(2)}x`;
export const signedPercent = (p: number, d = 1): string => `${p >= 0 ? "+" : "-"}${Math.abs(p).toFixed(d)}%`;
export const plain = (v: number): string => v.toLocaleString("en-US", { maximumFractionDigits: 2, minimumFractionDigits: v !== 0 && Math.abs(v) < 100 ? 2 : 0 });

export function formatBy(fmt: string, v: Cell): string {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return "n/a";
  if (typeof v === "string") return v;
  switch (fmt) {
    case "currency": return money(v);
    case "currency2": return money2(v);
    case "count": return count(v);
    case "percent": return percent(v);
    case "multiple": return multiple(v);
    case "signed_percent": return signedPercent(v);
    case "number": return plain(v);
    default: return String(v);
  }
}

/** Axis tick labels: compact, no cents, so ticks stay short. */
export function tickLabel(fmt: string, v: number): string {
  switch (fmt) {
    case "currency": case "currency2": {
      const a = Math.abs(v);
      return a >= 1e6 ? `$${+(v / 1e6).toFixed(1)}M` : a >= 1e3 ? `$${+(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K` : `$${+v.toFixed(a < 10 ? 2 : 0)}`;
    }
    case "count": { const a = Math.abs(v); return a >= 1e6 ? `${+(v / 1e6).toFixed(1)}M` : a >= 1e3 ? `${+(v / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K` : String(Math.round(v)); }
    case "percent": return `${+(v * 100).toFixed(Math.abs(v) < 0.1 ? 1 : 0)}%`;
    case "multiple": return `${+v.toFixed(1)}x`;
    case "signed_percent": return `${v > 0 ? "+" : v < 0 ? "-" : ""}${Math.abs(+v.toFixed(1))}%`;
    default: return String(+v.toFixed(2));
  }
}

/** "Nice" axis ticks between min and max. */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!isNum(min) || !isNum(max)) return [0, 1];
  if (min === max) { const pad = Math.abs(min) * 0.1 || 1; min -= pad; max += pad; }
  const span = max - min;
  const raw = span / Math.max(1, target - 1);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(+v.toFixed(10));
  return out;
}

/** Short date for an axis: "Sep 21". Accepts YYYY-MM-DD. */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
