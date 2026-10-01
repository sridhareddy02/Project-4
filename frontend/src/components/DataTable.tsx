import { useContext } from "react";
import { FormatContext } from "../context";
import { formatBy } from "../format";
import type { Cell, Table } from "../types";

const NUMERIC = new Set(["count", "currency", "currency2", "percent", "multiple", "signed_percent", "number", "auto"]);

function cellText(fmt: string, value: Cell, row: Record<string, Cell>, formats: Record<string, string>): string {
  if (fmt === "auto") {
    const f = (row._fmt as string | undefined) ?? formats[String(row._metric ?? "")] ?? "number";
    return formatBy(f, value);
  }
  return formatBy(fmt, value);
}

export function DataTable({ table }: { table: Table }) {
  const formats = useContext(FormatContext);
  return (
    <div>
      <p className="table-title">{table.title}</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>{table.columns.map((c) => <th key={c.key} scope="col" className={NUMERIC.has(c.format) ? "num" : ""}>{c.label}</th>)}</tr>
          </thead>
          <tbody>
            {table.rows.map((row, i) => (
              <tr key={i}>
                {table.columns.map((c) => {
                  const v = row[c.key];
                  const num = NUMERIC.has(c.format);
                  if (c.key === "verdict" && typeof v === "string") {
                    return <td key={c.key}><span className={`verdict ${v === "clear change" ? "clear" : "noise"}`}>{v}</span></td>;
                  }
                  const cls = [num ? "num" : "", c.format === "signed_percent" && typeof v === "number" ? (v < 0 ? "neg" : "pos") : "",
                    typeof v === "string" && v.length > 36 ? "wrap-cell" : ""].filter(Boolean).join(" ");
                  return <td key={c.key} className={cls}>{cellText(c.format, v, row, formats)}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
