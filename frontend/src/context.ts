import { createContext } from "react";
import type { Catalog } from "./types";

/** Metric key -> display format, so mixed-format table columns ("auto") can render each row correctly. */
export const FormatContext = createContext<Record<string, string>>({});
export const metricFormats = (c: Catalog | null): Record<string, string> => Object.fromEntries((c?.metrics ?? []).map((m) => [m.key, m.format]));
