import { describe, expect, it } from "vitest";
import {
  DEFAULT_LOCALE,
  dictionaries,
  getLocale,
  t,
  type MessageKey,
} from "./i18n";

describe("i18n", () => {
  it("ritorna le stringhe italiane per le chiavi note", () => {
    expect(t("nav.logout")).toBe("Esci");
    expect(t("nav.stats")).toBe("Statistiche");
    expect(t("topbar.tagline")).toBe("verticali 9:16 · batch");
  });

  it("usa l'italiano come lingua di default", () => {
    expect(DEFAULT_LOCALE).toBe("it");
    expect(getLocale()).toBe("it");
  });

  it("ha tutte le chiavi del dizionario italiano popolate", () => {
    const entries = Object.entries(dictionaries.it);
    expect(entries.length).toBeGreaterThan(0);
    for (const [key, value] of entries) {
      expect(value, `la chiave "${key}" è vuota`).toBeTruthy();
    }
  });

  it("fa fallback alla chiave stessa se non tradotta", () => {
    expect(t("chiave.inesistente" as MessageKey)).toBe("chiave.inesistente");
  });
});
