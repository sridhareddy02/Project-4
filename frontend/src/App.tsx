import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import { AnomalyFeed } from "./components/AnomalyFeed";
import { AnswerCard } from "./components/AnswerCard";
import { AskBox } from "./components/AskBox";
import { KpiStrip } from "./components/KpiStrip";
import { TrustPanel } from "./components/TrustPanel";
import { FormatContext, metricFormats } from "./context";
import type { Answer, Catalog, EvalReport, Health, Incident, Overview } from "./types";

type Tab = "ask" | "feed" | "trust";

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(() => {
    try { const t = localStorage.getItem("theme"); return t === "light" || t === "dark" ? t : null; } catch { return null; }
  });
  const toggle = () => {
    const sysDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const next = (theme ?? (sysDark ? "dark" : "light")) === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("theme", next); } catch { /* storage can be unavailable */ }
  };
  return toggle;
}

const fmtDate = (iso: string) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export default function App() {
  const [tab, setTab] = useState<Tab>("ask");
  const [health, setHealth] = useState<Health | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[] | null>(null);
  const [report, setReport] = useState<EvalReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [recorded, setRecorded] = useState<string[]>([]);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const toggleTheme = useTheme();
  const topRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getHealth().then(setHealth).catch(() => setOffline(true));
    api.getCatalog().then(setCatalog).catch(() => setOffline(true));
    api.getOverview().then(setOverview).catch(() => setOffline(true));
    api.getAnomalies().then(setIncidents).catch(() => setIncidents([]));
    api.getEval().then(setReport).catch((e: Error) => setReportError(e.message));
    if (api.DEMO) api.recordedQuestions().then(setRecorded).catch(() => undefined);
  }, []);

  const ask = useCallback(async (q: string) => {
    setBusy(true);
    setError(null);
    setTab("ask");
    try {
      const a = await api.ask(q);
      setAnswers((prev) => [a, ...prev.filter((x) => x.question !== a.question)]);
      topRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, []);

  const formats = useMemo(() => metricFormats(catalog), [catalog]);
  const questions = catalog?.questions ?? [];

  return (
    <FormatContext.Provider value={formats}>
      <div className="bg-glow" aria-hidden="true" />
      <div className="wrap">
        <header className="top">
          <div className="brand"><span className="brand-mark" aria-hidden="true">MC</span>Marketing Copilot</div>
          <span className="chip lime">Synthetic data</span>
          {health && <span className="chip">data through {fmtDate(health.as_of)}</span>}
          <span className="chip accent" title={api.DEMO ? "Answers recorded from the engine" : "Answers computed live"}>{api.DEMO ? "Recorded demo" : "Live API"}</span>
          {health?.llm_planner && <span className="chip">LLM planner on</span>}
          <span className="spacer" />
          <button className="icon-btn" type="button" onClick={toggleTheme} aria-label="Switch between light and dark">◐</button>
        </header>

        <section className="hero" aria-labelledby="h1">
          <h1 id="h1">Ask in plain English. Get answers you can <em>check</em>.</h1>
          <p className="lead">A governed analytics copilot for marketing performance. Questions become validated query plans, never free-form SQL. Every number in an answer is traced back to the data, and every answer shows how it was computed.</p>
        </section>

        {offline && !api.DEMO && (
          <div className="callout warn" role="alert"><b>API not reachable. </b>Start it with <span className="mono">python -m mktg_copilot serve</span>, then reload.</div>
        )}

        <KpiStrip data={overview} />

        <div className="tabs" role="tablist" aria-label="Sections">
          {([["ask", "Ask"], ["feed", "Anomaly feed"], ["trust", "Trust and method"]] as [Tab, string][]).map(([k, label]) => (
            <button key={k} role="tab" className="tab" aria-selected={tab === k} onClick={() => setTab(k)} type="button">{label}</button>
          ))}
        </div>

        {tab === "ask" && (
          <>
            <AskBox onAsk={ask} busy={busy} questions={questions} recorded={recorded} />
            {error && <p className="error" role="alert">{error}</p>}
            <div ref={topRef} />
            <div className="thread" aria-live="polite" aria-busy={busy}>
              {answers.map((a) => <AnswerCard key={a.question} a={a} onAsk={ask} busy={busy} />)}
            </div>
            {answers.length === 0 && !busy && <p className="hint">Pick an example above to see an answer with its plan, SQL and checks.</p>}
          </>
        )}
        {tab === "feed" && <AnomalyFeed incidents={incidents} onAsk={ask} />}
        {tab === "trust" && <TrustPanel report={report} catalog={catalog} error={reportError} />}

        <p className="footer">All data in this app is synthetic, generated from a seeded model with known injected events so the results can be graded. Nothing here is employer or customer data. The copilot describes performance; it does not establish cause, and budget decisions should be validated with an experiment.</p>
      </div>
    </FormatContext.Provider>
  );
}
