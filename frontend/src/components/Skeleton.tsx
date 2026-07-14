import "../styles/toast.css";

/* ============================================================
   Componenti di stato "feedback" (Iterazione 4):
     - <Skeleton>            blocco shimmer riutilizzabile
     - <DashboardListSkeleton>  placeholder della lista video
     - <EditorSkeleton>      placeholder dell'editor (player + sottotitoli)
     - <EmptyState>          rifinitura di .empty (icona + testo)
     - <ErrorState>          box d'errore con pulsante "Riprova"

   Gli stili vivono in styles/toast.css (foglio unico del sistema di
   feedback), importato qui sopra così non serve toccare main.tsx.
   ============================================================ */

export function Skeleton({
  width, height, radius, className, style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      className={`skel${className ? ` ${className}` : ""}`}
      aria-hidden="true"
      style={{ width, height, borderRadius: radius, ...style }}
    />
  );
}

/** Placeholder della tabella video (dashboard) durante il primo caricamento. */
export function DashboardListSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="skel-rows" role="status" aria-label="Caricamento dei video in corso">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skel-row" key={i}>
          <Skeleton className="skel-thumb" />
          <div className="skel-col">
            <Skeleton height={13} width={`${45 + (i % 3) * 12}%`} />
            <Skeleton height={11} width="30%" />
          </div>
          <Skeleton className="skel-badge" />
        </div>
      ))}
    </div>
  );
}

/** Placeholder dell'editor (colonna player + colonna sottotitoli). */
export function EditorSkeleton() {
  return (
    <div className="skel-editor" role="status" aria-label="Caricamento del video in corso">
      <div>
        <Skeleton className="skel-player" />
        <div className="skel-controls">
          <Skeleton height={34} radius={6} />
          <Skeleton height={64} radius={8} />
          <Skeleton height={44} width="60%" radius={6} />
        </div>
      </div>
      <div>
        <div className="skel-subhead">
          <Skeleton height={18} width={160} />
          <Skeleton height={30} width={150} radius={6} />
        </div>
        <Skeleton height={52} radius={6} style={{ marginBottom: 14 }} />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="skel-sub" />
        ))}
      </div>
    </div>
  );
}

/** Empty state coerente: mantiene la classe .empty e aggiunge un'icona. */
export function EmptyState({
  icon = "◍", text, children, small = false,
}: {
  icon?: string;
  text?: string;
  children?: React.ReactNode;
  small?: boolean;
}) {
  return (
    <div className={`empty empty-state${small ? " small" : ""}`}>
      <div className="empty-ico" aria-hidden="true">{icon}</div>
      <div className="empty-text">{text ?? children}</div>
    </div>
  );
}

/** Box d'errore centrato con azione "Riprova" (richiama la stessa fetch). */
export function ErrorState({
  message, onRetry, retryLabel = "↻ Riprova",
}: {
  message: string;
  onRetry: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="error-state" role="alert">
      <div className="error-state-ico" aria-hidden="true">⚠</div>
      <div className="error-state-msg">{message}</div>
      <button type="button" className="btn" onClick={onRetry}>{retryLabel}</button>
    </div>
  );
}
