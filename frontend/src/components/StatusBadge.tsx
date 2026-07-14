const LABELS: Record<string, string> = {
  uploaded: "Caricato",
  transcribing: "Trascrizione…",
  review: "Da controllare",
  ready: "Pronto",
  exporting: "Export…",
  exported: "Esportato",
  error: "Errore",
};

export const STATUS_LABELS = LABELS;

export default function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{LABELS[status] ?? status}</span>;
}
