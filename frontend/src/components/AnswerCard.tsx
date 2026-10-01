import { useState } from "react";
import type { Answer } from "../types";
import { ChartView } from "./Charts";
import { DataTable } from "./DataTable";

type Tab = "plan" | "sql" | "retrieval" | "checks";

function Grounding({ a }: { a: Answer }) {
  const g = a.grounding as Answer["grounding"] & { checked?: number };
  if (a.status !== "ok" || !("checked" in g)) return null;
  if (g.note) return <span className="chip accent" title="Definitions are curated text from the semantic layer.">Curated definition</span>;
  if (g.passed) {
    return <span className="chip good" title="Every number in the written answer was matched to a value computed from the warehouse.">✓ {g.matched} of {g.checked} numbers traced to the data</span>;
  }
  return <span className="chip bad" title={`Unmatched: ${g.unmatched.join(", ")} ${g.hedging_flags.join(", ")}`}>Check this answer: {g.unmatched.length} number(s) not traced{g.hedging_flags.length ? ", causal wording" : ""}</span>;
}

function Inspector({ a }: { a: Answer }) {
  const [tab, setTab] = useState<Tab>("plan");
  const tabs: [Tab, string][] = [["plan", "Query plan"], ["sql", `SQL (${a.sql.length})`], ["retrieval", "Retrieval"], ["checks", "Checks and timing"]];
  return (
    <details className="inspect">
      <summary>How this was computed</summary>
      <div className="inspect-body">
        <div className="itabs" role="tablist" aria-label="Inspector">
          {tabs.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} className="itab" type="button" onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>
        {tab === "plan" && (
          <>
            <p className="hint" style={{ marginTop: 0 }}>The planner ({a.planner}) turned the question into this validated plan. Only names from the semantic layer are allowed; the plan never contains SQL.</p>
            <pre>{a.plan ? JSON.stringify(a.plan, null, 2) : "No plan: the question was refused or needed clarification before planning finished."}</pre>
          </>
        )}
        {tab === "sql" && (
          <>
            <p className="hint" style={{ marginTop: 0 }}>Generated from the semantic layer and run on a read-only connection. Values are bound parameters; they are shown inline here for reading only.</p>
            <pre>{a.sql.length ? a.sql.join("\n\n") : "No query was run for this answer."}</pre>
          </>
        )}
        {tab === "retrieval" && (
          <>
            <p className="hint" style={{ marginTop: 0 }}>Definitions and glossary entries retrieved for this question (TF-IDF similarity).</p>
            <pre>{a.retrieved.length ? a.retrieved.map((r) => `${r.score.toFixed(3)}  ${r.title}  [${r.id}]`).join("\n") : "Nothing relevant retrieved."}</pre>
          </>
        )}
        {tab === "checks" && (
          <pre>{JSON.stringify({ grounding: a.grounding, timings_ms: a.timings_ms, confidence: a.plan?.confidence }, null, 2)}</pre>
        )}
      </div>
    </details>
  );
}

export function AnswerCard({ a, onAsk, busy }: { a: Answer; onAsk: (q: string) => void; busy: boolean }) {
  const label = { ok: "Answer", clarify: "Needs detail", refused: "Can't answer", error: "Error" }[a.status];
  const tone = { ok: "lime", clarify: "warn", refused: "bad", error: "bad" }[a.status];
  const next = a.status === "ok" ? a.followups : a.suggestions;
  return (
    <article className="card answer" aria-label={`Answer to: ${a.question}`}>
      <div className="answer-head">
        <p className="q">“{a.question}”</p>
        <span className={`chip ${tone}`}>{label}</span>
        <Grounding a={a} />
        {a.plan && <span className="chip">{a.plan.intent.replace("_", " ")}</span>}
        <span className="chip" title="Which planner turned the question into a plan">{a.planner} planner</span>
      </div>
      <h3>{a.headline}</h3>
      <div className="narr">{a.narrative.map((p, i) => <p key={i}>{p}</p>)}</div>

      {a.warnings.map((w, i) => <div className="callout warn" key={i} role="note"><b>Heads up: </b>{w}</div>)}
      {a.assumptions.length > 0 && (
        <ul className="assume" aria-label="Assumptions">
          {a.assumptions.map((x, i) => <li key={i}>{x}</li>)}
        </ul>
      )}

      {a.charts.length > 0 && <div className="viz">{a.charts.map((c, i) => <ChartView chart={c} key={i} />)}</div>}
      {a.tables.length > 0 && <div className="tables">{a.tables.map((t, i) => <DataTable table={t} key={i} />)}</div>}
      {a.caveats.map((c, i) => <div className="callout caveat" key={i}>{c}</div>)}

      {next.length > 0 && (
        <div className="follow">
          <p className="hint" style={{ margin: "0 0 6px" }}>{a.status === "ok" ? "Try next" : "Try one of these"}</p>
          <div className="chips" style={{ marginTop: 0 }}>
            {next.map((q) => <button key={q} className="q-chip" type="button" disabled={busy} onClick={() => onAsk(q)}>{q}</button>)}
          </div>
        </div>
      )}
      <Inspector a={a} />
    </article>
  );
}
