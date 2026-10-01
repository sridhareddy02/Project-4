import type { Catalog, EvalReport } from "../types";

const pct = (v: number) => `${Math.round(v * 100)}%`;

export function TrustPanel({ report, catalog, error }: { report: EvalReport | null; catalog: Catalog | null; error: string | null }) {
  return (
    <div className="grid2" style={{ alignItems: "start" }}>
      <section className="card pad" aria-labelledby="eval-h">
        <h2 className="section-h" id="eval-h">Measured, not claimed</h2>
        <p className="lead" style={{ fontSize: ".92rem" }}>The evaluation runs on every change and fails the build if a gate regresses. All data is synthetic, so the injected events and the oracle's numbers are known exactly.</p>
        {report ? (
          <>
            <div className="score">
              <div className="s"><div className="v">{report.numbers.correct}/{report.numbers.checked}</div><div className="l">numbers match an independent pandas oracle</div></div>
              <div className="s"><div className="v">{report.grounding.numbers_traced}/{report.grounding.numbers_checked}</div><div className="l">numbers in answers traced to data</div></div>
              <div className="s"><div className="v">{report.events.filter((e) => e.found).length}/{report.events.length}</div><div className="l">injected events found unprompted</div></div>
              <div className="s"><div className="v">{pct(report.adversarial.accuracy)}</div><div className="l">refusals and injection tests ({report.adversarial.n})</div></div>
              <div className="s"><div className="v">{report.false_alarms.per_series_year}</div><div className="l">false incidents per series-year on clean data</div></div>
              <div className="s"><div className="v">{report.latency_ms.p50} ms</div><div className="l">median time to answer (p95 {report.latency_ms.p95} ms)</div></div>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Event injected into the data</th><th>Found</th><th>What the copilot reported</th></tr></thead>
                <tbody>
                  {report.events.map((e) => (
                    <tr key={e.event}>
                      <td className="wrap-cell"><b>{e.event}</b> {e.kind}</td>
                      <td><span className={`chip ${e.found ? "good" : "bad"}`}>{e.found ? "yes" : "no"}</span></td>
                      <td className="wrap-cell">{e.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="hint">Plan accuracy: {pct(report.plans.dev.plan_accuracy)} on {report.plans.dev.n} development questions and {pct(report.plans.heldout.plan_accuracy)} on {report.plans.heldout.n} held-out questions. The held-out set scored 82.5% on its first run; the failures were fixed afterwards, so it is no longer a blind test. See docs/evaluation.md.</p>
          </>
        ) : <p className="hint">{error ?? "Loading the evaluation report…"}</p>}
      </section>

      <section className="card pad" aria-labelledby="cat-h">
        <h2 className="section-h" id="cat-h">The semantic layer</h2>
        <p className="lead" style={{ fontSize: ".92rem" }}>Every KPI is defined once. The copilot can only query what is listed here, and ratios are always the ratio of sums.</p>
        {catalog ? (
          <>
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table>
                <thead><tr><th>Metric</th><th>Definition</th></tr></thead>
                <tbody>
                  {catalog.metrics.map((m) => (
                    <tr key={m.key}><td><b>{m.label}</b></td><td className="mono" style={{ fontSize: ".76rem" }}>{m.formula}{m.requires_spend ? "  (paid only)" : ""}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
            <h3 style={{ fontSize: "1rem", margin: "18px 0 4px" }}>What it will not answer</h3>
            <ul className="list">
              {catalog.unsupported.map((u) => <li key={u.key}><b>{u.terms[0]}</b>: {u.why}.</li>)}
            </ul>
          </>
        ) : <p className="hint">Loading…</p>}
      </section>
    </div>
  );
}
