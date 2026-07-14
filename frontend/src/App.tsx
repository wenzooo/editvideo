import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation } from "react-router-dom";
import { api, AuthError } from "./api";
import CommandPalette from "./components/CommandPalette";
import CRTOverlay from "./components/CRTOverlay";
import ErrorBoundary from "./components/ErrorBoundary";
import SettingsPanel, { applyPrefs, readPrefs } from "./components/SettingsPanel";
import { ToastProvider } from "./components/Toast";
import Typewriter from "./components/Typewriter";
import { t } from "./i18n";
import { APP_VERSION } from "./version";

// Code-splitting (Iter 18): le route pesanti sono caricate on-demand con
// React.lazy, così il bundle iniziale (login + shell) resta leggero e ogni
// pagina finisce in un chunk separato. I default export sono garantiti nei
// rispettivi file (Dashboard, Editor, Stats).
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Editor = lazy(() => import("./pages/Editor"));
const Stats = lazy(() => import("./pages/Stats"));

function Login({ onOk }: { onOk: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.login(password);
      onOk();
    } catch (err) {
      // AuthError = credenziali sbagliate (401); qualsiasi altro errore è
      // tipicamente di rete (fetch fallito su HF a freddo): mostriamo un
      // messaggio italiano generico invece della stringa tecnica inglese.
      setError(err instanceof AuthError ? "Password errata" : "Server non raggiungibile, riprova");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      {/* login MINIMALE stile TTY: banner, prompt password:, invio come comando.
          Niente APP_VERSION qui: la versione esatta resta visibile solo dopo
          l'auth (topbar), coerente con l'anti-fingerprinting di /api/health. */}
      <form className="login-term" onSubmit={submit}>
        <p className="login-line login-dim">
          editvideo — tty1
          <span className="login-cursor" aria-hidden="true">
            ▊
          </span>
        </p>
        {/* riga di contesto: spiega cos'è l'app a chi arriva allo Space. */}
        <p className="login-line login-dim">{t("login.tagline")}</p>
        <label className="login-line login-field" htmlFor="login-password">
          <span aria-hidden="true">password:</span>
          <span className="sr-only">Password</span>
          <input
            id="login-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            autoComplete="current-password"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "login-error" : undefined}
          />
        </label>
        {error && (
          <div id="login-error" className="login-line error-text" role="alert">
            ✗ {error.toLowerCase()}
          </div>
        )}
        <button className="login-enter" disabled={busy || !password}>
          {busy ? "accesso…" : "[ entra ↵ ]"}
        </button>
      </form>
    </div>
  );
}

/**
 * Al cambio di route sposta il focus sul contenuto principale (<main
 * id="main-content">), così screen reader e utenti da tastiera ripartono
 * dall'inizio della nuova pagina. Salta il primo mount per non rubare il
 * focus iniziale. Nessuna animazione (rispetta prefers-reduced-motion).
 */
function RouteFocus() {
  const { pathname } = useLocation();
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    const main = document.getElementById("main-content");
    if (main) {
      main.focus();
      window.scrollTo({ top: 0, behavior: "auto" });
    }
  }, [pathname]);
  return null;
}

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // applica le preferenze salvate (accento/densità/movimento) all'avvio;
  // se non c'è nulla in localStorage vale il comportamento attuale (default).
  useEffect(() => {
    applyPrefs(readPrefs());
  }, []);

  useEffect(() => {
    api.me().then((r) => setAuthed(r.authenticated)).catch(() => setAuthed(false));
  }, []);

  // qualunque AuthError dalle pagine riporta al login
  useEffect(() => {
    const handler = (e: PromiseRejectionEvent) => {
      if (e.reason instanceof AuthError) setAuthed(false);
    };
    window.addEventListener("unhandledrejection", handler);
    return () => window.removeEventListener("unhandledrejection", handler);
  }, []);

  // logica di logout condivisa dal bottone "Esci" della topbar e dalla
  // Command Palette (che emette il CustomEvent 'app:logout' per restare
  // disaccoppiata). Il catch assicura che si torni comunque al login.
  const logout = useCallback(() => {
    api.logout().then(() => setAuthed(false)).catch(() => setAuthed(false));
  }, []);

  useEffect(() => {
    window.addEventListener("app:logout", logout);
    return () => window.removeEventListener("app:logout", logout);
  }, [logout]);

  if (authed === null) return <div className="page-loading">{t("common.loading")}</div>;
  if (!authed) return <Login onOk={() => setAuthed(true)} />;

  return (
    <ToastProvider>
      <BrowserRouter>
        <RouteFocus />
        <CommandPalette />
        <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
        {/* Overlay CRT: montato una volta, sopra il contenuto ma sotto
            menu/modali/toast. Visibile solo con l'effetto attivo (html.crt-on). */}
        <CRTOverlay />
        <a className="skip-link" href="#main-content">{t("a11y.skipToContent")}</a>
        <header className="topbar" style={{ flexWrap: "wrap" }}>
          <Link to="/" className="brand"><Typewriter text="EDIT//VIDEO" /></Link>
          <span className="muted">{t("topbar.tagline")}</span>
          <span className="muted small">v{APP_VERSION}</span>
          <Link to="/statistiche" className="btn ghost small">{t("nav.stats")}</Link>
          <div className="spacer" />
          <button
            type="button"
            className="btn ghost settings-gear"
            aria-label={t("nav.settings")}
            aria-haspopup="dialog"
            aria-expanded={settingsOpen}
            onClick={() => setSettingsOpen(true)}
          >
            <span aria-hidden="true">⚙</span>
          </button>
          <button className="btn ghost" onClick={logout}>
            {t("nav.logout")}
          </button>
        </header>
        <ErrorBoundary>
          <Suspense fallback={<div className="page-loading">{t("common.loading")}</div>}>
            <Routes>
              <Route path="/" element={<Dashboard onAuthError={() => setAuthed(false)} />} />
              <Route path="/editor/:id" element={<Editor onAuthError={() => setAuthed(false)} />} />
              <Route path="/statistiche" element={<Stats onAuthError={() => setAuthed(false)} />} />
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </BrowserRouter>
    </ToastProvider>
  );
}
