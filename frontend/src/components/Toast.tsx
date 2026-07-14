import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import "../styles/toast.css";

/* ============================================================
   Sistema di toast (Iterazione 4) — stile ctOS/HUD.

   API:
     const toast = useToast();
     toast.success("Salvato ✔");
     toast.error("Qualcosa è andato storto");     // dura di più
     toast.info("Sottotitoli in coda");
     toast.push({ type, message, duration });      // duration in ms (0 = persistente)
     toast.dismiss(id);

   Caratteristiche: stack in basso a destra (piena larghezza in basso su
   mobile), auto-dismiss (~3,5s, errori ~7s), chiusura manuale, max 4
   visibili, aria-live polite (assertive per gli errori). Le animazioni
   sono leggere e degradano con prefers-reduced-motion (vedi toast.css).
   ============================================================ */

export type ToastType = "success" | "error" | "info";

export interface ToastInput {
  type?: ToastType;
  message: string;
  /** ms prima dell'auto-dismiss; 0 = resta finché non lo si chiude. */
  duration?: number;
}

interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
  duration: number;
  leaving: boolean;
}

export interface ToastApi {
  push: (t: ToastInput) => number;
  success: (message: string, duration?: number) => number;
  error: (message: string, duration?: number) => number;
  info: (message: string, duration?: number) => number;
  dismiss: (id: number) => void;
}

const DEFAULT_MS = 3500;
const ERROR_MS = 7000;
const EXIT_MS = 220;       // deve combaciare con l'animazione toast-out in toast.css
const MAX_VISIBLE = 4;

const ICONS: Record<ToastType, string> = {
  success: "✓",
  error: "⚠",
  info: "ℹ",
};

// Fallback no-op: se per qualche motivo si usa useToast() fuori dal
// provider, l'app non si rompe (i toast semplicemente non compaiono).
const NOOP: ToastApi = {
  push: () => -1,
  success: () => -1,
  error: () => -1,
  info: () => -1,
  dismiss: () => undefined,
};

const ToastCtx = createContext<ToastApi>(NOOP);

export function useToast(): ToastApi {
  return useContext(ToastCtx);
}

function ToastCard({ toast, onClose }: { toast: ToastItem; onClose: () => void }) {
  const isError = toast.type === "error";
  return (
    <div
      className={`toast toast-${toast.type}${toast.leaving ? " leaving" : ""}`}
      role={isError ? "alert" : "status"}
      aria-live={isError ? "assertive" : "polite"}
    >
      <span className="toast-ico" aria-hidden="true">{ICONS[toast.type]}</span>
      <span className="toast-msg">{toast.message}</span>
      <button type="button" className="toast-x" onClick={onClose} aria-label="Chiudi notifica">
        ×
      </button>
      {toast.duration > 0 && !toast.leaving && (
        <span className="toast-timer" aria-hidden="true" style={{ animationDuration: `${toast.duration}ms` }} />
      )}
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const idRef = useRef(0);
  const autoTimers = useRef<Map<number, number>>(new Map());
  const exitTimers = useRef<Map<number, number>>(new Map());

  const dismiss = useCallback((id: number) => {
    // annulla l'auto-dismiss ancora pendente
    const auto = autoTimers.current.get(id);
    if (auto != null) {
      window.clearTimeout(auto);
      autoTimers.current.delete(id);
    }
    if (exitTimers.current.has(id)) return; // già in uscita
    // avvia l'animazione d'uscita, poi rimuovi a fine transizione
    setToasts((list) => list.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
    const rm = window.setTimeout(() => {
      exitTimers.current.delete(id);
      setToasts((list) => list.filter((t) => t.id !== id));
    }, EXIT_MS);
    exitTimers.current.set(id, rm);
  }, []);

  const push = useCallback((input: ToastInput): number => {
    const id = ++idRef.current;
    const type: ToastType = input.type ?? "info";
    const duration = input.duration ?? (type === "error" ? ERROR_MS : DEFAULT_MS);
    setToasts((list) => [...list, { id, type, message: input.message, duration, leaving: false }]);
    if (duration > 0) {
      autoTimers.current.set(id, window.setTimeout(() => dismiss(id), duration));
    }
    return id;
  }, [dismiss]);

  const api = useMemo<ToastApi>(() => ({
    push,
    success: (message, duration) => push({ type: "success", message, duration }),
    error: (message, duration) => push({ type: "error", message, duration }),
    info: (message, duration) => push({ type: "info", message, duration }),
    dismiss,
  }), [push, dismiss]);

  // pulizia di tutti i timer allo smontaggio del provider
  useEffect(() => {
    const autos = autoTimers.current;
    const exits = exitTimers.current;
    return () => {
      autos.forEach((t) => window.clearTimeout(t));
      exits.forEach((t) => window.clearTimeout(t));
      autos.clear();
      exits.clear();
    };
  }, []);

  // mostra al massimo gli ultimi MAX_VISIBLE (i più recenti)
  const visible = toasts.slice(-MAX_VISIBLE);

  return (
    <ToastCtx.Provider value={api}>
      {children}
      {visible.length > 0 && (
        <div className="toast-stack" role="region" aria-label="Notifiche">
          {visible.map((t) => (
            <ToastCard key={t.id} toast={t} onClose={() => dismiss(t.id)} />
          ))}
        </div>
      )}
    </ToastCtx.Provider>
  );
}
