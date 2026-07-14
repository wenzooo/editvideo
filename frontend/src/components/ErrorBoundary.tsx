import { Component, type ErrorInfo, type ReactNode } from "react";

/* ============================================================
   ERROR BOUNDARY (Iterazione 21) — stile Watch Dogs / ctOS.

   Class component che cattura gli errori di render dell'albero figlio
   (avvolge le <Routes> in App.tsx, NON il login). Mostra un fallback
   ctOS chiaro con pulsante "Ricarica" e logga l'errore in console.

   Il fallback è autoconsistente: usa le classi/variabili di tema già
   esistenti (nessun foglio CSS aggiuntivo).
   ============================================================ */

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // log per diagnosi (console): messaggio + stack dei componenti
    console.error("ErrorBoundary: errore di render catturato", error, info.componentStack);
  }

  private handleReload = (): void => {
    window.location.reload();
  };

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="login-wrap">
        <div
          role="alert"
          style={{
            position: "relative",
            overflow: "hidden",
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            padding: 32,
            width: "min(92vw, 380px)",
            display: "flex",
            flexDirection: "column",
            gap: 14,
            textAlign: "center",
          }}
        >
          {/* riga accent in cima, coerente con topbar / palette / menu */}
          <span
            aria-hidden="true"
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              right: 0,
              height: 2,
              background: "var(--rainbow)",
              opacity: 0.9,
            }}
          />
          <h1
            style={{
              fontFamily: "var(--mono)",
              fontSize: 20,
              fontWeight: 700,
              letterSpacing: ".06em",
              background: "var(--rainbow)",
              WebkitBackgroundClip: "text",
              backgroundClip: "text",
              WebkitTextFillColor: "transparent",
              color: "transparent",
            }}
          >
            ERRORE//SISTEMA
          </h1>
          <p className="muted" style={{ margin: 0 }}>
            Si è verificato un errore imprevisto nell'interfaccia. Ricarica la pagina per riprendere.
          </p>
          <button
            type="button"
            className="btn primary"
            onClick={this.handleReload}
            style={{ justifyContent: "center" }}
          >
            Ricarica
          </button>
        </div>
      </div>
    );
  }
}
