import { useState } from "react";
import { api } from "../api";
import type { Job } from "../types";
import { useToast } from "./Toast";

const TYPE_LABEL: Record<string, string> = {
  transcribe: "Trascrizione",
  export: "Export",
};

export default function JobsBar({ jobs }: { jobs: Job[] }) {
  const toast = useToast();
  // job per cui è già partita la richiesta di annullamento: disattiva il pulsante
  // fino a quando il polling dei job non lo fa sparire dalla lista.
  const [canceling, setCanceling] = useState<Set<string>>(new Set());

  if (jobs.length === 0) return null;
  const running = jobs.find((j) => j.status === "running") ?? jobs[0];
  const label = TYPE_LABEL[running.type] ?? running.type;
  const isCanceling = canceling.has(running.id);

  const cancel = async (jobId: string) => {
    setCanceling((s) => new Set(s).add(jobId));
    try {
      const r = await api.cancelJob(jobId);
      toast.info(r.canceled ? "Job rimosso dalla coda" : "Annullamento in corso…");
    } catch (e) {
      // fallito: riabilita il pulsante e segnala l'errore
      setCanceling((s) => {
        const n = new Set(s);
        n.delete(jobId);
        return n;
      });
      toast.error(e instanceof Error ? e.message : "Impossibile annullare il job");
    }
  };

  return (
    <div className="jobsbar" role="status" aria-live="polite">
      <span className="pulse" />
      <span>
        {jobs.length} job in coda — {label}
        {running.video_name ? ` di “${running.video_name}”` : ""}
        {running.status === "running" ? ` ${Math.round(running.progress * 100)}%` : " (in attesa)"}
      </span>
      <div
        className="progress slim"
        role="progressbar"
        aria-label={`Avanzamento ${label}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(running.progress * 100)}
      >
        <div style={{ width: `${running.progress * 100}%` }} />
      </div>
      <button
        type="button"
        className="btn ghost small"
        onClick={() => cancel(running.id)}
        disabled={isCanceling}
        aria-label={`Annulla ${label}${running.video_name ? ` di ${running.video_name}` : ""}`}
        title="Annulla questo job"
        style={{ marginLeft: "auto" }}
      >
        {isCanceling ? "…" : "✕"}
      </button>
    </div>
  );
}
