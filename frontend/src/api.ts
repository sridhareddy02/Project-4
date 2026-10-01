// One client, two back ends. Live: the FastAPI service. Demo: a snapshot of answers recorded from the same
// engine, so the UI can be hosted as static files. Free-text questions need the live API.
import type { Answer, Catalog, EvalReport, Health, Incident, Overview } from "./types";

export const DEMO = import.meta.env.VITE_DEMO === "1";
const BASE = import.meta.env.BASE_URL;

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

const norm = (q: string) => q.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

let snapshot: Promise<Record<string, Answer>> | null = null;
function loadSnapshot(): Promise<Record<string, Answer>> {
  snapshot ??= getJson<Answer[]>(`${BASE}demo/answers.json`).then((list) => Object.fromEntries(list.map((a) => [norm(a.question), a])));
  return snapshot;
}

export async function recordedQuestions(): Promise<string[]> {
  return Object.values(await loadSnapshot()).map((a) => a.question);
}

export async function ask(question: string): Promise<Answer> {
  if (DEMO) {
    const a = (await loadSnapshot())[norm(question)];
    if (!a) throw new Error("This recorded demo only answers the listed questions. Run the API locally to ask anything.");
    return a;
  }
  const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
  if (r.status === 422) throw new Error("Please enter a question of up to 500 characters.");
  if (!r.ok) throw new Error("The copilot could not answer that question.");
  return r.json() as Promise<Answer>;
}

const path = (name: string) => (DEMO ? `${BASE}demo/${name}.json` : `/api/${name}`);
export const getHealth = () => getJson<Health>(path("health"));
export const getCatalog = () => getJson<Catalog>(path("catalog"));
export const getOverview = () => getJson<Overview>(path("overview"));
export const getAnomalies = () => getJson<{ incidents: Incident[] }>(path("anomalies")).then((r) => r.incidents);
export const getEval = () => getJson<EvalReport>(DEMO ? `${BASE}demo/eval.json` : "/api/eval");
