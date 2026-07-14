// Test QA aggiuntivi per src/format.ts — complementari a src/format.test.ts
// (che copre già i casi base di fmtTime/parseTime/fmtSize). Qui: bug confermati
// (test rossi che asseriscono il comportamento CORRETTO), edge case verdi e
// characterization test del comportamento attuale.
import { describe, expect, it } from "vitest";
import { fmtDate, fmtSize, fmtTime, parseTime } from "./format";

describe("fmtTime — bordo del minuto", () => {
  // BUG CONFERMATO QA-05: fmtTime a format.ts:7 non gestisce il rollover del
  // minuto: s.toFixed(1) arrotonda 59.99->"60.0" senza riportare al minuto
  // successivo, producendo "0:60.0" e "1:60.0" — vedi TEST_REPORT.md
  it("arrotonda i secondi riportando al minuto successivo (59.99 -> 1:00.0)", () => {
    expect(fmtTime(59.99)).toBe("1:00.0");
  });

  // BUG CONFERMATO QA-05: stesso difetto di rollover su valori oltre il primo
  // minuto: fmtTime(119.97) oggi ritorna "1:60.0" invece di "2:00.0" — vedi TEST_REPORT.md
  it("arrotonda i secondi riportando al minuto successivo (119.97 -> 2:00.0)", () => {
    expect(fmtTime(119.97)).toBe("2:00.0");
  });
});

describe("fmtTime — casi limite verdi", () => {
  it("antepone il segno '-' ai valori negativi", () => {
    expect(fmtTime(-83.4)).toBe("-1:23.4");
    expect(fmtTime(-83.4).startsWith("-")).toBe(true);
  });

  it("formatta lo zero come 0:00.0", () => {
    expect(fmtTime(0)).toBe("0:00.0");
  });

  it("applica il padding sotto i 10 secondi", () => {
    expect(fmtTime(5)).toBe("0:05.0");
    expect(fmtTime(9.5)).toBe("0:09.5");
  });

  it("ritorna '-' per null, undefined e NaN", () => {
    expect(fmtTime(null)).toBe("-");
    expect(fmtTime(undefined)).toBe("-");
    expect(fmtTime(NaN)).toBe("-");
  });
});

describe("parseTime — validazione componenti", () => {
  // BUG CONFERMATO QA-06: parseTime a format.ts:16-21 non valida il segno delle
  // singole componenti: "1:-30" viene calcolato come 1*60 + (-30) = 30 invece
  // di essere rifiutato come input invalido — vedi TEST_REPORT.md
  it("rifiuta componenti negative come '1:-30'", () => {
    expect(parseTime("1:-30")).toBeNull();
  });
});

describe("parseTime — casi verdi", () => {
  it("interpreta m:ss.d e secondi grezzi interi", () => {
    expect(parseTime("1:23.4")).toBeCloseTo(83.4);
    expect(parseTime("83")).toBe(83);
  });

  it("accetta la virgola decimale anche nella componente dei secondi", () => {
    expect(parseTime("1:23,4")).toBeCloseTo(83.4);
  });

  it("ritorna null per stringa vuota o testo non numerico", () => {
    expect(parseTime("")).toBeNull();
    expect(parseTime("abc")).toBeNull();
  });

  // CHARACTERIZATION QA: comportamento attuale documentato, la correttezza è
  // una decisione di design (non un bug confermato).
  it("characterization: accetta componenti oltre 59 ('1:99' -> 159)", () => {
    // Nessun limite superiore sulle componenti: 1*60 + 99 = 159.
    expect(parseTime("1:99")).toBe(159);
  });

  it("characterization: parseFloat permissivo accetta suffissi non numerici ('1abc' -> 1)", () => {
    // parseFloat("1abc") = 1: il suffisso spazzatura viene ignorato in silenzio.
    expect(parseTime("1abc")).toBe(1);
  });
});

describe("fmtSize", () => {
  it("usa MB dalla soglia di 1e6 byte", () => {
    expect(fmtSize(999_999)).toBe("1000 KB");
    expect(fmtSize(1_000_000)).toBe("1.0 MB");
  });

  it("usa GB dalla soglia di 1e9 byte", () => {
    expect(fmtSize(999_999_999)).toBe("1000.0 MB");
    expect(fmtSize(1_000_000_000)).toBe("1.00 GB");
  });

  // CHARACTERIZATION QA-12: sotto i 500 byte Math.round(bytes/1e3) dà 0 e
  // l'output "0 KB" è fuorviante per file piccoli ma non vuoti. Comportamento
  // attuale documentato — vedi TEST_REPORT.md.
  it("characterization: file piccoli ma non vuoti mostrano '0 KB'", () => {
    expect(fmtSize(400)).toBe("0 KB");
  });
});

describe("fmtDate", () => {
  it("formatta una data ISO valida come gg/mm hh:mm", () => {
    expect(fmtDate("2026-07-09T15:30:00")).toMatch(/\d{2}\/\d{2}.*\d{2}:\d{2}/);
  });

  // CHARACTERIZATION QA: nessuna guardia sull'input — una stringa non parsabile
  // produce "Invalid Date Invalid Date" invece di un fallback. Comportamento
  // attuale documentato, la correttezza è una decisione di design.
  it("characterization: input invalido produce testo 'Invalid'", () => {
    expect(fmtDate("non-una-data")).toContain("Invalid");
  });
});
