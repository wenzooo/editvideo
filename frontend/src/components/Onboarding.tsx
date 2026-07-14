import { useEffect, useId, useRef } from "react";
import "../styles/onboarding.css";

/* ============================================================
   ONBOARDING (Iterazione 11) — primo avvio + guida "come funziona"
   ------------------------------------------------------------
   Pannello NON invasivo mostrato in cima alla Dashboard al primo
   avvio (finché in localStorage non c'è il flag "onboarding_done").
   Riapribile on-demand dal pulsante "❔ Guida". NON è un modale
   bloccante: è una region dismissibile che scorre nel flusso e non
   intercetta i click sul resto della pagina.

   Accessibile: role="region" con aria-labelledby/-describedby, il
   focus viene spostato sul pannello all'apertura e ripristinato al
   trigger alla chiusura, Escape chiude. L'animazione d'entrata degrada
   con prefers-reduced-motion e su mobile va a piena larghezza con
   target ≥40px (vedi onboarding.css, importato qui sopra).
   ============================================================ */

const STORAGE_KEY = "onboarding_done";

/** Legge il flag in modo sicuro: localStorage può lanciare (modalità privata, quota). */
export function isOnboardingDone(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Marca la guida come vista, così non riappare ai prossimi avvii. */
export function markOnboardingDone(): void {
  try {
    localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    /* storage non disponibile: la guida potrà riapparire, ma non è un errore bloccante */
  }
}

interface Step {
  n: number;
  title: string;
  body: string;
}

// I 4 passi seguono il flusso reale del prodotto: batch su video verticali 9:16.
const STEPS: Step[] = [
  {
    n: 1,
    title: "Carica i video",
    body: "Trascina i tuoi video verticali 9:16 nella zona in alto (o clicca per sceglierli). Puoi caricarne tanti insieme: si elaborano in batch, uno alla volta.",
  },
  {
    n: 2,
    title: "Genera i sottotitoli",
    body: "Seleziona i video e premi ⚡ Sottotitoli, oppure usa ⚡ Auto per elaborare in blocco tutti i caricati (sottotitoli e tagli in un colpo solo). Un “Format” può avviarli in automatico.",
  },
  {
    n: 3,
    title: "Controlla e taglia",
    body: "Apri l’Editor di un video per rivedere i sottotitoli, scegliere lo stile e rifinire i tagli: trim iniziale/finale, silenzi e doppioni.",
  },
  {
    n: 4,
    title: "Esporta",
    body: "Quando un video è “Da controllare” (o “Pronto”), rivedi l’anteprima ed esportalo dal singolo video o in blocco con ▶ Esporta tutti: scarichi l’MP4 finale con i sottotitoli impressi.",
  },
];

interface Props {
  open: boolean;
  /** Chiamato alla chiusura ("Ho capito", ✕ o Escape). */
  onClose: () => void;
}

export default function Onboarding({ open, onClose }: Props) {
  const panelRef = useRef<HTMLElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const headingId = useId();
  const descId = useId();

  // All'apertura: memorizza il focus corrente e spostalo sul pannello (nessuna
  // trappola del Tab: la region è NON modale). Alla chiusura ripristina il
  // focus all'elemento che l'aveva prima (es. il pulsante "❔ Guida").
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    const t = window.setTimeout(() => panelRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(t);
      const el = restoreRef.current;
      if (el && typeof el.focus === "function" && document.contains(el)) el.focus();
    };
  }, [open]);

  // Escape chiude la guida da qualunque punto, finché è aperta.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <section
      ref={panelRef}
      className="onb-panel"
      role="region"
      aria-labelledby={headingId}
      aria-describedby={descId}
      tabIndex={-1}
    >
      <div className="onb-head">
        <div className="onb-title-wrap">
          <span className="onb-kicker" aria-hidden="true">›_ guida rapida</span>
          <h2 id={headingId} className="onb-title">Come funziona EditVideo</h2>
        </div>
        <button
          type="button"
          className="onb-x"
          aria-label="Chiudi la guida"
          title="Chiudi la guida"
          onClick={onClose}
        >
          ✕
        </button>
      </div>

      <p id={descId} className="onb-lead">
        Dal video grezzo al reel pronto in quattro passi. Pensato per i verticali 9:16
        e per lavorare su più video insieme.
      </p>

      <ol className="onb-steps">
        {STEPS.map((s) => (
          <li key={s.n} className="onb-step">
            <span className="onb-step-n" aria-hidden="true">{s.n}</span>
            <div className="onb-step-body">
              <h3 className="onb-step-title">{s.title}</h3>
              <p className="onb-step-text">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>

      <div className="onb-foot">
        <button type="button" className="btn primary onb-cta" onClick={onClose}>
          Ho capito
        </button>
        <span className="onb-foot-note muted small">
          Puoi riaprirla quando vuoi da <strong>❔ Guida</strong>.
        </span>
      </div>
    </section>
  );
}
