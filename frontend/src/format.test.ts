import { describe, expect, it } from "vitest";
import { formatBy, niceTicks, shortDate, signedPercent, tickLabel } from "./format";

describe("formatBy mirrors the Python formatter", () => {
  it("formats money, counts and ratios", () => {
    expect(formatBy("currency", 1843310.25)).toBe("$1.84M");
    expect(formatBy("currency", 72803.4)).toBe("$72.8K");
    expect(formatBy("currency", 950)).toBe("$950");
    expect(formatBy("currency2", 48.7)).toBe("$48.70");
    expect(formatBy("count", 21846)).toBe("21.8K");
    expect(formatBy("percent", 0.04731)).toBe("4.7%");
    expect(formatBy("multiple", 3.1234)).toBe("3.12x");
    expect(formatBy("signed_percent", -6.237)).toBe("-6.2%");
    expect(formatBy("signed_percent", 6.237)).toBe("+6.2%");
  });
  it("renders missing and non-finite values as n/a, and passes text through", () => {
    expect(formatBy("currency", null)).toBe("n/a");
    expect(formatBy("currency", undefined)).toBe("n/a");
    expect(formatBy("count", Number.NaN)).toBe("n/a");
    expect(formatBy("percent", "Paid Search")).toBe("Paid Search");
  });
  it("signed percent keeps a minus sign for zero-rounded negatives only when negative", () => {
    expect(signedPercent(0)).toBe("+0.0%");
  });
});

describe("axes", () => {
  it("niceTicks covers the data range with round steps", () => {
    const t = niceTicks(59000, 73000);
    expect(t[0]).toBeLessThanOrEqual(59000);
    expect(t[t.length - 1]).toBeGreaterThanOrEqual(73000);
    const steps = new Set(t.slice(1).map((v, i) => +(v - t[i]).toFixed(6)));
    expect(steps.size).toBe(1);
  });
  it("niceTicks copes with a flat series", () => {
    const t = niceTicks(5, 5);
    expect(t.length).toBeGreaterThan(1);
    expect(t[0]).toBeLessThan(5);
  });
  it("tick labels are compact", () => {
    expect(tickLabel("currency", 65000)).toBe("$65K");
    expect(tickLabel("percent", 0.047)).toBe("4.7%");
    expect(tickLabel("multiple", 2.5)).toBe("2.5x");
  });
  it("short dates", () => {
    expect(shortDate("2026-09-21")).toBe("Sep 21");
    expect(shortDate("garbage")).toBe("garbage");
  });
});
