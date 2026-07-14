// Scaffolding i18n minimale, senza dipendenze esterne (Iter 24).
//
// Obiettivo: avere un punto unico dove vivono le stringhe dell'interfaccia,
// tipizzato, e pronto ad accogliere una seconda lingua (es. 'en') in futuro
// senza toccare i componenti. Per ora l'app resta interamente in italiano:
// `t(key)` ritorna la stringa italiana (lingua di default).
//
// Come aggiungere l'inglese domani:
//   1. estendere il tipo `Locale` con | "en";
//   2. aggiungere la voce `en: { ... }` a `dictionaries` (TypeScript segnalerà
//      subito eventuali chiavi mancanti, perché `Messages` le richiede tutte);
//   3. chiamare `setLocale("en")` (o leggere la preferenza utente) all'avvio.

/** Lingue supportate. Aggiungere qui i codici futuri (es. | "en"). */
export type Locale = "it";

/**
 * Chiavi di traduzione disponibili. Sono un'unione di stringhe letterali così
 * un refuso nella chiamata a `t()` diventa un errore di compilazione, non un
 * bug silenzioso a runtime.
 */
export type MessageKey =
  | "topbar.tagline"
  | "nav.stats"
  | "nav.logout"
  | "nav.settings"
  | "a11y.skipToContent"
  | "login.tagline"
  | "common.loading";

/** Un dizionario completo per una lingua: tutte le chiavi sono obbligatorie. */
export type Messages = Record<MessageKey, string>;

/** Lingua di default dell'applicazione. */
export const DEFAULT_LOCALE: Locale = "it";

/**
 * Dizionari per lingua. Oggi solo `it`; la struttura `Record<Locale, Messages>`
 * garantisce che ogni lingua aggiunta copra tutte le chiavi.
 */
export const dictionaries: Record<Locale, Messages> = {
  it: {
    "topbar.tagline": "verticali 9:16 · batch",
    "nav.stats": "Statistiche",
    "nav.logout": "Esci",
    "nav.settings": "Impostazioni",
    "a11y.skipToContent": "Salta al contenuto",
    "login.tagline": "Batch editor per video verticali 9:16",
    "common.loading": "Caricamento…",
  },
};

let currentLocale: Locale = DEFAULT_LOCALE;

/** Imposta la lingua attiva (per ora esiste solo 'it'). */
export function setLocale(locale: Locale): void {
  currentLocale = locale;
}

/** Ritorna la lingua attiva. */
export function getLocale(): Locale {
  return currentLocale;
}

/**
 * Traduce `key` nella lingua indicata (default: quella attiva, cioè 'it').
 * Fallback a cascata: lingua richiesta -> lingua di default -> la chiave stessa,
 * così una chiave non ancora tradotta resta visibile ma non rompe la UI.
 */
export function t(key: MessageKey, locale: Locale = currentLocale): string {
  const dict = dictionaries[locale] ?? dictionaries[DEFAULT_LOCALE];
  return dict[key] ?? dictionaries[DEFAULT_LOCALE][key] ?? key;
}

export default t;
