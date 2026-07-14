import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import { useToast } from "./Toast";
import "../styles/palette.css";

/* ============================================================
   Command Palette (Iterazione 12) — stile Watch Dogs / ctOS.

   - si apre con Ctrl+K / Cmd+K (listener globale su window; è una
     combinazione con modificatore, quindi NON intercetta la digitazione
     normale). Ctrl+K di nuovo, Escape o click esterno chiudono.
   - ricerca con filtro fuzzy semplice (sottosequenza) sui comandi;
     lista navigabile con ↑/↓ (Home/End), Invio esegue, voce attiva
     evidenziata.
   - accessibile: role="dialog" aria-modal, pattern combobox+listbox con
     aria-activedescendant, focus all'apertura, trappola del Tab, focus
     ripristinato alla chiusura. L'animazione d'entrata degrada con
     prefers-reduced-motion (vedi palette.css).
   - comandi DISACCOPPIATI dalle pagine: navigazione via useNavigate +
     CustomEvent su window per le azioni globali (logout/scorciatoie),
     agganciate in App.tsx.
   ============================================================ */

interface Command {
  id: string;
  label: string;
  /** Parole chiave extra usate solo dal filtro (non mostrate). */
  keywords?: string;
  icon: ReactNode;
  danger?: boolean;
  run: () => void;
}

/**
 * Fuzzy match a sottosequenza (case-insensitive): ogni carattere di `raw`
 * deve comparire, nell'ordine, dentro `text`. Ritorna un punteggio (più
 * alto = migliore) o -1 se non c'è match. Bonus per caratteri contigui e a
 * inizio parola → ordinamento sensato dei risultati.
 */
function fuzzyScore(raw: string, text: string): number {
  const q = raw.toLowerCase();
  if (!q) return 0;
  const t = text.toLowerCase();
  let score = 0;
  let ti = 0;
  let prev = -2;
  for (let qi = 0; qi < q.length; qi++) {
    const found = t.indexOf(q[qi], ti);
    if (found === -1) return -1;
    score += 1;
    if (found === prev + 1) score += 4; // contiguo
    if (found === 0 || t[found - 1] === " ") score += 3; // inizio parola
    prev = found;
    ti = found + 1;
  }
  return score;
}

// Su Mac la scorciatoia è Cmd(⌘)+K, altrove Ctrl+K: lo rileviamo una volta
// sola per etichettare correttamente il pulsante di avvio.
const IS_MAC =
  typeof navigator !== "undefined" &&
  /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || "");

// Pulsante di avvio flottante (in basso a sinistra, per non coprire i toast a
// destra): rende la palette scopribile e raggiungibile anche senza tastiera
// (touch). Stili inline per tenere tutto dentro questo componente.
const LAUNCHER_STYLE: CSSProperties = {
  position: "fixed",
  left: "16px",
  bottom: "16px",
  zIndex: 900, // sopra il contenuto, sotto overlay palette(1000) e toast(2000)
  display: "inline-flex",
  alignItems: "center",
  gap: "7px",
  padding: "7px 11px",
  fontFamily: "var(--mono)",
  fontSize: "12px",
  lineHeight: 1,
  letterSpacing: ".02em",
  color: "var(--muted)",
  background: "rgba(14, 19, 28, .92)",
  border: "1px solid var(--border)",
  borderRadius: "6px",
  cursor: "pointer",
  boxShadow: "0 6px 20px rgba(0, 0, 0, .4)",
};

export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);

  const navigate = useNavigate();
  const toast = useToast();

  const overlayRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  const rid = useId();
  const listboxId = `${rid}-list`;
  const optionId = (i: number) => `${rid}-opt-${i}`;

  const close = useCallback(() => setOpen(false), []);

  // Comandi: navigazione (useNavigate) + CustomEvent per le azioni globali,
  // così la palette non dipende dallo stato interno delle pagine.
  const commands = useMemo<Command[]>(
    () => [
      {
        id: "dashboard",
        label: "Vai alla Dashboard",
        keywords: "home inizio elenco video lista",
        icon: "⌂",
        run: () => navigate("/"),
      },
      {
        id: "back",
        label: "Torna indietro",
        keywords: "indietro precedente back",
        icon: "‹",
        run: () => navigate(-1),
      },
      {
        id: "reload",
        label: "Ricarica dati",
        keywords: "aggiorna refresh reload ricarica",
        icon: "↻",
        run: () => window.location.reload(),
      },
      {
        id: "copy-link",
        label: "Copia link pagina",
        keywords: "url condividi appunti clipboard",
        icon: "⧉",
        run: () => {
          const url = window.location.href;
          navigator.clipboard?.writeText(url).then(
            () => toast.success("Link copiato negli appunti"),
            () => toast.error("Impossibile copiare il link"),
          );
        },
      },
      {
        id: "shortcuts",
        label: "Apri scorciatoie",
        keywords: "tasti tastiera keyboard aiuto help",
        icon: "⌨",
        run: () => window.dispatchEvent(new CustomEvent("app:shortcuts")),
      },
      {
        id: "logout",
        label: "Esci",
        keywords: "logout disconnetti uscita esci",
        icon: "⏻",
        danger: true,
        run: () => window.dispatchEvent(new CustomEvent("app:logout")),
      },
    ],
    [navigate, toast],
  );

  // Risultati filtrati + ordinati per punteggio (query vuota = tutti).
  const results = useMemo(() => {
    const q = query.trim();
    if (!q) return commands;
    return commands
      .map((c) => ({ c, s: fuzzyScore(q, `${c.label} ${c.keywords ?? ""}`) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => b.s - a.s)
      .map((x) => x.c);
  }, [query, commands]);

  const activeIdx = results.length ? Math.min(active, results.length - 1) : -1;

  // Apertura con Ctrl+K / Cmd+K (globale). È un combo con modificatore:
  // non interferisce con la digitazione normale nei campi.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // All'apertura: azzera lo stato, memorizza il focus e mettilo sull'input.
  // Alla chiusura (cleanup): ripristina il focus all'elemento precedente.
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    setQuery("");
    setActive(0);
    const t = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(t);
      const el = restoreRef.current;
      if (el && typeof el.focus === "function" && document.contains(el)) el.focus();
    };
  }, [open]);

  // Mantieni la voce attiva visibile durante la navigazione da tastiera.
  useEffect(() => {
    if (!open || activeIdx < 0) return;
    document.getElementById(optionId(activeIdx))?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, activeIdx]);

  const execute = useCallback(
    (idx: number) => {
      const cmd = results[idx];
      if (!cmd) return;
      close(); // chiudi prima, poi esegui (navigazione / reload / evento)
      cmd.run();
    },
    [results, close],
  );

  function onPanelKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (results.length) setActive((i) => (Math.min(i, results.length - 1) + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (results.length)
        setActive((i) => (Math.min(i, results.length - 1) - 1 + results.length) % results.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      if (results.length) setActive(results.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      execute(activeIdx);
    } else if (e.key === "Tab") {
      // trappola basilare del focus: tieni il focus dentro il pannello
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

  return (
    <>
      {/* Punto d'accesso sempre visibile: senza di esso la palette sarebbe
          scopribile solo con Ctrl/Cmd+K e del tutto irraggiungibile da touch. */}
      <button
        type="button"
        style={LAUNCHER_STYLE}
        aria-keyshortcuts="Meta+K Control+K"
        aria-label="Apri la palette dei comandi"
        title={`Palette dei comandi (${IS_MAC ? "⌘" : "Ctrl"} + K)`}
        onClick={() => setOpen(true)}
      >
        <span aria-hidden="true" style={{ color: "var(--accent)" }}>
          ›_
        </span>
        <span aria-hidden="true">{IS_MAC ? "⌘K" : "Ctrl+K"}</span>
      </button>

      {open && (
        <div
          className="cmdk-overlay"
          ref={overlayRef}
          onMouseDown={(e) => {
            if (e.target === overlayRef.current) close();
          }}
        >
          <div
            className="cmdk-panel"
            role="dialog"
            aria-modal="true"
            aria-label="Palette dei comandi"
            ref={panelRef}
            onKeyDown={onPanelKeyDown}
          >
            <div className="cmdk-search">
              <span className="cmdk-prompt" aria-hidden="true">
                ›_
              </span>
              <input
                ref={inputRef}
                className="cmdk-input"
                type="text"
                role="combobox"
                aria-expanded="true"
                aria-controls={listboxId}
                aria-activedescendant={activeIdx >= 0 ? optionId(activeIdx) : undefined}
                aria-autocomplete="list"
                aria-label="Cerca un comando"
                placeholder="Cerca un comando…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
            </div>

            {results.length > 0 ? (
              <ul className="cmdk-list" role="listbox" id={listboxId} aria-label="Comandi">
                {results.map((cmd, i) => (
                  <li
                    key={cmd.id}
                    id={optionId(i)}
                    role="option"
                    aria-selected={i === activeIdx}
                    className={`cmdk-item${i === activeIdx ? " active" : ""}${cmd.danger ? " danger" : ""}`}
                    onMouseEnter={() => setActive(i)}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => execute(i)}
                  >
                    <span className="cmdk-item-icon" aria-hidden="true">
                      {cmd.icon}
                    </span>
                    <span className="cmdk-item-label">{cmd.label}</span>
                    {i === activeIdx && (
                      <span className="cmdk-item-hint" aria-hidden="true">
                        ↵
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <div className="cmdk-empty" role="status">
                Nessun comando per <strong>“{query}”</strong>.
              </div>
            )}

            <div className="cmdk-foot" aria-hidden="true">
              <span className="cmdk-foot-item">
                <kbd>↑</kbd>
                <kbd>↓</kbd> naviga
              </span>
              <span className="cmdk-foot-item">
                <kbd>↵</kbd> esegui
              </span>
              <span className="cmdk-foot-item">
                <kbd>esc</kbd> chiudi
              </span>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
