import { DEMO } from "../api";
import { formatBy } from "../format";
import type { Incident } from "../types";

const FMT: Record<string, string> = { Revenue: "currency", Orders: "count", Spend: "currency", "Conversion rate": "percent", ROAS: "multiple" };

export function AnomalyFeed({ incidents, onAsk }: { incidents: Incident[] | null; onAsk: (q: string) => void }) {
  if (!incidents) return <p className="hint" aria-busy="true">Scanning daily series…</p>;
  return (
    <div>
      <h2 className="section-h">Unusual days in the last 12 months</h2>
      <p className="lead">Each daily series is split into trend, weekly pattern and residual. A day is flagged only when it is far outside normal <em>and</em> at least 15% off expected, a rule calibrated on event-free data. “Where” localizes the deviation to the channel or campaign that carries it.</p>
      <div className="table-wrap card" style={{ marginTop: 16 }}>
        <table>
          <thead><tr><th>Metric</th><th>When</th><th className="num">Deviation</th><th className="num">Observed</th><th className="num">Expected</th><th>Where</th><th>Context</th>{!DEMO && <th />}</tr></thead>
          <tbody>
            {incidents.map((r, i) => {
              const f = FMT[r.metric] ?? "number";
              const verb = r.direction === "down" ? "drop" : "spike";
              return (
                <tr key={i}>
                  <td><b>{r.metric}</b></td>
                  <td>{r.dates}</td>
                  <td className={`num ${r.deviation < 0 ? "neg" : "pos"}`}>{r.deviation > 0 ? "+" : ""}{r.deviation.toFixed(0)}%</td>
                  <td className="num">{formatBy(f, r.observed)}</td>
                  <td className="num">{formatBy(f, r.expected)}</td>
                  <td className="wrap-cell">{r.where}</td>
                  <td className="wrap-cell">{r.note || "none"}</td>
                  {!DEMO && <td><button className="btn small" type="button" onClick={() => onAsk(`Explain the ${verb} in ${r.metric.toLowerCase()} from ${r.dates.replace(/, \d{4}$/, "")}`)}>Ask why</button></td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="hint">These are statistical outliers, not diagnoses. A tracking break, a pacing bug and a data gap all look like a drop until the source is checked, which is why gaps are called out separately.</p>
    </div>
  );
}
