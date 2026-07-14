import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import "../styles/settings.css";

/* ============================================================
   IMPOSTAZIONI / TEMA (Iterazione 17) — stile Watch Dogs / ctOS.

   Modale accessibile (role="dialog", aria-modal, focus trap, Escape)
   aperto dall'ingranaggio ⚙ nella topbar (in App.tsx). Tre preferenze,
   persistite in localStorage e applicate DAL VIVO:

     • Accento  → CSS var --accent su <html> (document.documentElement)
     • Densità  → classe body.density-compact (override discreti)
     • Movimento→ classe html.motion-off (neutralizza animazioni/transizioni,
                  coerente con prefers-reduced-motion)

   Le funzioni readPrefs/applyPrefs sono esportate e riusate da App.tsx per
   applicare le preferenze all'avvio. Il foglio settings.css è importato QUI
   (nessuna modifica a main.tsx), come per palette.css/toast.css.
   ============================================================ */

/* ---------- modello preferenze ---------- */

export const ACCENTS = [
  { id: "teal", label: "Teal", color: "#00e5c7" },
  { id: "viola", label: "Viola", color: "#b16cea" },
  { id: "ambra", label: "Ambra", color: "#ff9e4d" },
  { id: "rosso", label: "Rosso", color: "#ff5c6c" },
  // Preset “Terminal” (Iterazione 25): verde fosforo e ambra CRT. Aggiunti
  // agli accenti esistenti (non li sostituiscono): l'accento scelto dall'utente
  // non viene mai forzato. Si abbinano bene all'effetto CRT ma sono indipendenti.
  { id: "fosforo", label: "Fosforo", color: "#39ff14" },
  { id: "ambra-crt", label: "Ambra CRT", color: "#ffb000" },
] as const;

export type AccentId = (typeof ACCENTS)[number]["id"];
export type Density = "comodo" | "compatto";
export type Motion = "auto" | "ridotto";
export type Crt = "on" | "off";

export interface Prefs {
  accent: AccentId;
  density: Density;
  motion: Motion;
  crt: Crt;
}

const LS_KEY = "editvideo:prefs";
const DEFAULTS: Prefs = { accent: "teal", density: "comodo", motion: "auto", crt: "off" };

/* ---------- lettura / scrittura localStorage (difensiva) ----------
   Se manca il valore o lo storage non è disponibile → default (=
   comportamento attuale dell'app). Ogni campo è validato contro i
   valori ammessi prima di essere accettato. */

export function readPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return { ...DEFAULTS };
    const p = JSON.parse(raw) as Partial<Record<keyof Prefs, string>>;
    return {
      accent: ACCENTS.some((a) => a.id === p.accent) ? (p.accent as AccentId) : DEFAULTS.accent,
      density: p.density === "compatto" ? "compatto" : "comodo",
      motion: p.motion === "ridotto" ? "ridotto" : "auto",
      // CRT SPENTO di default (UI pulita, animazioni solo dove servono): solo un
      // "on" esplicito attiva l'effetto terminale.
      crt: p.crt === "on" ? "on" : "off",
    };
  } catch {
    return { ...DEFAULTS };
  }
}

function savePrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage non disponibile (privato/quota): si ignora, resta il vivo */
  }
}

/* ---------- applicazione al DOM ---------- */

function applyAccent(accent: AccentId): void {
  const found = ACCENTS.find((a) => a.id === accent) ?? ACCENTS[0];
  document.documentElement.style.setProperty("--accent", found.color);
}

function applyDensity(density: Density): void {
  document.body.classList.toggle("density-compact", density === "compatto");
}

function applyMotion(motion: Motion): void {
  document.documentElement.classList.toggle("motion-off", motion === "ridotto");
}

function applyCrt(crt: Crt): void {
  // Interruttore unico del layer terminale/CRT: overlay scanline, tipografia
  // mono, cursore a blocco e typewriter sono tutti condizionati da html.crt-on
  // (vedi styles/terminal.css). Toglierla riporta l'app all'aspetto base.
  document.documentElement.classList.toggle("crt-on", crt === "on");
}

/** Applica l'intero set di preferenze (usata all'avvio in App.tsx e dal vivo). */
export function applyPrefs(prefs: Prefs): void {
  applyAccent(prefs.accent);
  applyDensity(prefs.density);
  applyMotion(prefs.motion);
  applyCrt(prefs.crt);
}

/* ============================================================
   Componente pannello
   ============================================================ */

const DENSITIES: { id: Density; label: string }[] = [
  { id: "comodo", label: "Comodo" },
  { id: "compatto", label: "Compatto" },
];

const MOTIONS: { id: Motion; label: string }[] = [
  { id: "auto", label: "Auto (sistema)" },
  { id: "ridotto", label: "Ridotto" },
];

const CRTS: { id: Crt; label: string }[] = [
  { id: "on", label: "Attivo" },
  { id: "off", label: "Disattivo" },
];

export default function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [prefs, setPrefs] = useState<Prefs>(() => readPrefs());

  const overlayRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  const titleId = useId();

  // All'apertura: risincronizza dallo storage, memorizza il focus e spostalo
  // dentro il pannello. Alla chiusura (cleanup): ripristina il focus.
  useEffect(() => {
    if (!open) return;
    setPrefs(readPrefs());
    restoreRef.current = document.activeElement as HTMLElement | null;
    const t = window.setTimeout(() => {
      const first = panelRef.current?.querySelector<HTMLElement>(
        'input, button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      );
      first?.focus();
    }, 0);
    return () => {
      window.clearTimeout(t);
      const el = restoreRef.current;
      if (el && typeof el.focus === "function" && document.contains(el)) el.focus();
    };
  }, [open]);

  // Aggiorna una preferenza: stato + applica dal vivo + persisti.
  const update = useCallback((patch: Partial<Prefs>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch };
      applyPrefs(next);
      savePrefs(next);
      return next;
    });
  }, []);

  function onPanelKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "Tab") {
      // trappola del focus: tieni il Tab dentro il pannello
      const nodes = panelRef.current?.querySelectorAll<HTMLElement>(
        'input, button:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
      );
      const f = nodes ? Array.from(nodes) : [];
      if (f.length === 0) {
        e.preventDefault();
        return;
      }
      const first = f[0];
      const last = f[f.length - 1];
      const act = document.activeElement as HTMLElement | null;
      if (e.shiftKey && act === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && act === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  if (!open) return null;

  return (
    <div
      className="settings-overlay"
      ref={overlayRef}
      onMouseDown={(e) => {
        if (e.target === overlayRef.current) onClose();
      }}
    >
      <div
        className="settings-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={panelRef}
        onKeyDown={onPanelKeyDown}
      >
        <div className="settings-head">
          <h2 id={titleId} className="settings-title">Impostazioni</h2>
          <button
            type="button"
            className="settings-close"
            onClick={onClose}
            aria-label="Chiudi impostazioni"
          >
            ×
          </button>
        </div>

        <div className="settings-body">
          {/* ---- Accento ---- */}
          <fieldset className="settings-group">
            <legend className="settings-legend">Accento</legend>
            <div className="settings-swatches">
              {ACCENTS.map((a) => (
                <label
                  key={a.id}
                  className={`settings-swatch${prefs.accent === a.id ? " selected" : ""}`}
                >
                  <input
                    className="sr-only"
                    type="radio"
                    name="settings-accent"
                    value={a.id}
                    checked={prefs.accent === a.id}
                    onChange={() => update({ accent: a.id })}
                  />
                  <span className="settings-dot" style={{ background: a.color }} aria-hidden="true" />
                  <span className="settings-swatch-name">{a.label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          {/* ---- Densità ---- */}
          <fieldset className="settings-group">
            <legend className="settings-legend">Densità</legend>
            <div className="settings-seg">
              {DENSITIES.map((d) => (
                <label
                  key={d.id}
                  className={`settings-opt${prefs.density === d.id ? " selected" : ""}`}
                >
                  <input
                    className="sr-only"
                    type="radio"
                    name="settings-density"
                    value={d.id}
                    checked={prefs.density === d.id}
                    onChange={() => update({ density: d.id })}
                  />
                  <span>{d.label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          {/* ---- Movimento ---- */}
          <fieldset className="settings-group">
            <legend className="settings-legend">Movimento</legend>
            <div className="settings-seg">
              {MOTIONS.map((m) => (
                <label
                  key={m.id}
                  className={`settings-opt${prefs.motion === m.id ? " selected" : ""}`}
                >
                  <input
                    className="sr-only"
                    type="radio"
                    name="settings-motion"
                    value={m.id}
                    checked={prefs.motion === m.id}
                    onChange={() => update({ motion: m.id })}
                  />
                  <span>{m.label}</span>
                </label>
              ))}
            </div>
            <p className="settings-hint muted small">
              “Auto” segue le preferenze di sistema; “Ridotto” disattiva le animazioni.
            </p>
          </fieldset>

          {/* ---- Effetto CRT / terminale ---- */}
          <fieldset className="settings-group">
            <legend className="settings-legend">Effetto CRT / terminale</legend>
            <div className="settings-seg">
              {CRTS.map((c) => (
                <label
                  key={c.id}
                  className={`settings-opt${prefs.crt === c.id ? " selected" : ""}`}
                >
                  <input
                    className="sr-only"
                    type="radio"
                    name="settings-crt"
                    value={c.id}
                    checked={prefs.crt === c.id}
                    onChange={() => update({ crt: c.id })}
                  />
                  <span>{c.label}</span>
                </label>
              ))}
            </div>
            <p className="settings-hint muted small">
              Scanline, cursore a blocco e monospace in stile terminale. Sottile;
              rispetta “Movimento” (niente flicker/typewriter con movimento ridotto).
            </p>
          </fieldset>
        </div>
      </div>
    </div>
  );
}
