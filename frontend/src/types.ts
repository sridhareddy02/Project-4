// Mirrors mktg_copilot.plan (the API contract).
export type Status = "ok" | "clarify" | "refused" | "error";

export interface DateRange { start: string; end: string; label: string }
export interface Filter { dimension: string; values: string[] }
export interface QueryPlan {
  intent: string;
  metrics: string[];
  group_by: string[];
  filters: Filter[];
  period: DateRange | null;
  compare_to: DateRange | null;
  grain: string | null;
  top_n: number | null;
  order: string | null;
  horizon_days: number | null;
  subject: string | null;
  confidence: number;
  assumptions: string[];
}

export type Cell = string | number | null | undefined;
export interface Column { key: string; label: string; format: string }
export interface Table { title: string; columns: Column[]; rows: Record<string, Cell>[] }

export interface Series { name: string; values: (number | null)[]; kind: "actual" | "forecast" | "expected" | "compare" }
export interface Span { x0: string; x1: string; label: string; kind: string }
export interface Marker { x: string; label: string }
export interface Chart {
  type: "line" | "bar" | "diverging";
  title: string;
  x: string[];
  series: Series[];
  y_format: string;
  band_lower: (number | null)[] | null;
  band_upper: (number | null)[] | null;
  spans: Span[];
  markers: Marker[];
  horizontal: boolean;
}

export interface Grounding {
  checked: number;
  matched: number;
  unmatched: string[];
  hedging_flags: string[];
  passed: boolean;
  note?: string;
}
export interface Retrieved { id: string; title: string; score: number }

export interface Answer {
  status: Status;
  question: string;
  as_of: string;
  planner: string;
  headline: string;
  narrative: string[];
  plan: QueryPlan | null;
  tables: Table[];
  charts: Chart[];
  sql: string[];
  assumptions: string[];
  caveats: string[];
  warnings: string[];
  retrieved: Retrieved[];
  grounding: Grounding | Record<string, never>;
  suggestions: string[];
  followups: string[];
  timings_ms: Record<string, number>;
}

export interface KpiCard {
  key: string; label: string; format: string; value: number | null; previous: number | null;
  change: number | null; good: "up" | "down"; clear: boolean; scope: string; spark: (number | null)[];
}
export interface Overview { period: { start: string; end: string }; as_of: string; cards: KpiCard[] }

export interface Incident {
  metric: string; _metric: string; dates: string; direction: "up" | "down"; observed: number; expected: number;
  deviation: number; peak_z: number; where: string; note: string;
}

export interface CatalogMetric { key: string; label: string; format: string; kind: string; formula: string; good: string; requires_spend: boolean; synonyms: string[] }
export interface Catalog {
  metrics: CatalogMetric[];
  dimensions: { key: string; label: string; values: string[] }[];
  unsupported: { key: string; terms: string[]; why: string }[];
  glossary: { id: string; title: string }[];
  questions: string[];
}

export interface EvalReport {
  generated: string;
  data: { start: string; end: string; rows: number };
  plans: { dev: PlanScore; heldout: PlanScore };
  numbers: { checked: number; correct: number; accuracy: number };
  adversarial: { n: number; accuracy: number; warehouse_intact: boolean };
  grounding: { answers: number; answers_passed: number; pass_rate: number; numbers_checked: number; numbers_traced: number };
  events: { event: string; kind: string; found: boolean; detail: string; question: string }[];
  false_alarms: { series: number; incidents: number; per_series_year: number };
  latency_ms: { p50: number; p95: number; max: number };
  gates: Record<string, boolean>;
}
export interface PlanScore { n: number; intent_accuracy: number; plan_accuracy: number }

export interface Health { status: string; version: string; as_of: string; data_start: string; llm_planner: boolean }
