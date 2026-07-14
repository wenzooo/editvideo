import { describe, expect, it } from "vitest";
import { fmtSize, fmtTime, parseTime } from "./format";

describe("fmtTime", () => {
  it("formatta i secondi come m:ss.d", () => {
    expect(fmtTime(0)).toBe("0:00.0");
    expect(fmtTime(5)).toBe("0:05.0");
    expect(fmtTime(83.4)).toBe("1:23.4");
  });

  it("gestisce valori negativi, nulli e NaN", () => {
    expect(fmtTime(-5)).toBe("-0:05.0");
    expect(fmtTime(null)).toBe("-");
    expect(fmtTime(undefined)).toBe("-");
    expect(fmtTime(NaN)).toBe("-");
  });
});

describe("parseTime", () => {
  it("interpreta il formato mm:ss e i secondi grezzi", () => {
    expect(parseTime("1:23.4")).toBeCloseTo(83.4);
    expect(parseTime("83.4")).toBeCloseTo(83.4);
    expect(parseTime("83")).toBe(83);
    expect(parseTime("1:30")).toBe(90);
  });

  it("accetta la virgola come separatore decimale", () => {
    expect(parseTime("1,5")).toBeCloseTo(1.5);
  });

  it("ritorna null per input vuoto o non numerico", () => {
    expect(parseTime("")).toBeNull();
    expect(parseTime("   ")).toBeNull();
    expect(parseTime("abc")).toBeNull();
  });
});

describe("fmtSize", () => {
  it("sceglie l'unità (KB/MB/GB) in base ai byte", () => {
    expect(fmtSize(1500)).toBe("2 KB");
    expect(fmtSize(1_500_000)).toBe("1.5 MB");
    expect(fmtSize(2_000_000_000)).toBe("2.00 GB");
  });
});
