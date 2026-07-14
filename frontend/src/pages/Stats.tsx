import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, AuthError } from "../api";
import { EmptyState, ErrorState, Skeleton } from "../components/Skeleton";
import { STATUS_LABELS } from "../components/StatusBadge";
import type { Video } from "../types";
import "../styles/stats.css";

/**
 * Iterazione 22 — Pagina Statistiche.
 * Tutto client-side: riusa api.listVideos() (la STESSA fonte della Dashboard)
 * e deriva le metriche in memoria. Nessun nuovo endpoint, nessuna dipendenza
 * chart: le visualizzazioni sono barre in puro CSS.
 */

// Assunzione dichiarata per la stima del "tempo risparmiato": minuti di editing
// manuale (trim, tagli, sottotitoli, export) risparmiati per ogni video
// effettivamente esportato. Resa esplicita nella nota a fondo pagina.
const MIN_SAVED_PER_EXPORT = 12;

// ordine di lavorazione per la distribuzione degli stati (coerente con la Dashboard)
const STATUS_FLOW = ["uploaded", "transcribing", "review", "ready", "exporting", "exported", "error"] as const;

// quanti giorni mostrare nel grafico di produzione giornaliera
const DAILY_WINDOW = 14;

/** Secondi -> "Xh Ym" / "Ym Zs" / "Zs" (per durate anche lunghe). */
function fmtDuration(totalSec: number): string {
  const s = Math.max(0, Math.round(totalSec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

/** Minuti -> "Xh Ym" / "Ym". */
function fmtMinutes(totalMin: number): string {
  const m = Math.max(0, Math.round(totalMin));
  const h = Math.floor(m / 60);
  return h > 0 ? `${h}h ${m % 60}m` : `${m}m`;
}

/** Chiave giorno locale YYYY-MM-DD (stesso fuso di fmtDate). */
function dayKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Chiave giorno -> etichetta "gg/mm". */
function dayLabel(key: string): string {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" });
}

interface StatsData {
  total: number;
  byStatus: Record<string, number>;
  exported: number;
  totalDuration: number;
  avgDuration: number;
  subtitles: number;
  exportsAvailable: number;
  savedMinutes: number;
  perDay: { key: string; label: string; count: number }[];
  avgPerDay: number;
  spanDays: number;
  peak: { label: string; count: number } | null;
}

function computeStats(videos: Video[]): StatsData {
  const byStatus: Record<string, number> = {};
  const countByDay = new Map<string, number>();
  let totalDuration = 0;
  let subtitles = 0;
  let exportsAvailable = 0;
  let minTime = Infinity;
  let maxTime = -Infinity;

  for (const v of videos) {
    byStatus[v.status] = (byStatus[v.status] ?? 0) + 1;
    totalDuration += v.duration || 0;
    subtitles += v.subtitle_count || 0;
    if (v.has_export) exportsAvailable++;
    const d = new Date(v.created_at);
    if (!isNaN(d.getTime())) {
      countByDay.set(dayKey(d), (countByDay.get(dayKey(d)) ?? 0) + 1);
      const midnight = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
      if (midnight < minTime) minTime = midnight;
      if (midnight > maxTime) maxTime = midnight;
    }
  }

  const total = videos.length;
  const exported = byStatus["exported"] ?? 0;

  // finestra continua: gli ultimi DAILY_WINDOW giorni fino all'ultimo upload
  // (i giorni senza caricamenti restano a 0 per mostrare la cadenza reale).
  const perDay: { key: string; label: string; count: number }[] = [];
  if (maxTime > -Infinity) {
    for (let i = DAILY_WINDOW - 1; i >= 0; i--) {
      const key = dayKey(new Date(maxTime - i * 86400000));
      perDay.push({ key, label: dayLabel(key), count: countByDay.get(key) ?? 0 });
    }
  }

  // throughput medio sull'intera finestra attiva (primo->ultimo upload, inclusivi)
  const spanDays = maxTime > -Infinity ? Math.round((maxTime - minTime) / 86400000) + 1 : 0;
  const avgPerDay = spanDays > 0 ? total / spanDays : 0;

  // giorno più produttivo
  let peak: { label: string; count: number } | null = null;
  for (const [key, count] of countByDay) {
    if (!peak || count > peak.count) peak = { label: dayLabel(key), count };
  }

  return {
    total,
    byStatus,
    exported,
    totalDuration,
    avgDuration: total > 0 ? totalDuration / total : 0,
    subtitles,
    exportsAvailable,
    savedMinutes: exported * MIN_SAVED_PER_EXPORT,
    perDay,
    avgPerDay,
    spanDays,
    peak,
  };
}

/** Skeleton coerente col resto dell'app (riusa il primitivo <Skeleton>). */
function StatsSkeleton() {
  return (
    <div role="status" aria-label="Caricamento delle statistiche in corso">
      <div className="stats-kpis">
        {Array.from({ length: 4 }).map((_, i) => (
          <div className="kpi" key={i}>
            <Skeleton height={26} width="55%" />
            <Skeleton height={12} width="80%" style={{ marginTop: 10 }} />
          </div>
        ))}
      </div>
      <div className="stats-section">
        <Skeleton height={14} width={180} style={{ marginBottom: 16 }} />
        {Array.from({ length: 5 }).map((_, i) => (
          <div className="stats-skel-row" key={i}>
            <Skeleton height={12} width={100} />
            <Skeleton height={12} width={`${35 + i * 12}%`} />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Stats({ onAuthError }: { onAuthError: () => void }) {
  const [videos, setVideos] = useState<Video[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setLoadError(null);
    try {
      const v = await api.listVideos();
      setVideos(v);
      setLoaded(true);
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
      else setLoadError(`Impossibile caricare le statistiche: ${String((e as Error).message ?? e)}`);
    } finally {
      setBusy(false);
    }
  }, [onAuthError]);

  useEffect(() => { load(); }, [load]);

  const stats = useMemo(() => computeStats(videos), [videos]);

  // stati da mostrare (in ordine di flusso) + eventuali stati sconosciuti; max per la scala barre
  const dist = useMemo(() => {
    const flow = STATUS_FLOW.filter((s) => (stats.byStatus[s] ?? 0) > 0) as string[];
    const extra = Object.keys(stats.byStatus).filter((s) => !(STATUS_FLOW as readonly string[]).includes(s));
    const all = [...flow, ...extra];
    const max = all.reduce((m, s) => Math.max(m, stats.byStatus[s] ?? 0), 1);
    return { all, max };
  }, [stats]);

  const maxDaily = useMemo(() => stats.perDay.reduce((m, d) => Math.max(m, d.count), 1), [stats.perDay]);

  const dailyAriaLabel = useMemo(() => {
    const active = stats.perDay.filter((d) => d.count > 0).map((d) => `${d.label}: ${d.count}`);
    return `Video caricati negli ultimi ${DAILY_WINDOW} giorni. ${active.length ? active.join("; ") : "nessun caricamento nel periodo"}.`;
  }, [stats.perDay]);

  return (
    <main id="main-content" tabIndex={-1} className="page stats">
      <div className="stats-head">
        <h1>Statistiche</h1>
        <div className="spacer" />
        <Link to="/" className="btn ghost small">← Dashboard</Link>
        <button type="button" className="btn ghost small" onClick={load} disabled={busy}>
          {busy ? "Aggiorno…" : "↻ Aggiorna"}
        </button>
      </div>

      {loadError ? (
        <ErrorState message={loadError} onRetry={load} />
      ) : !loaded ? (
        <StatsSkeleton />
      ) : stats.total === 0 ? (
        <EmptyState
          icon="📊"
          text="Ancora nessun dato: carica ed elabora qualche video per vedere le statistiche."
        />
      ) : (
        <>
          {/* KPI principali */}
          <section className="stats-kpis" aria-label="Riepilogo">
            <div className="kpi">
              <div className="kpi-val">{stats.total}</div>
              <div className="kpi-lbl">Video totali</div>
            </div>
            <div className="kpi">
              <div className="kpi-val">{stats.exported}</div>
              <div className="kpi-lbl">
                Esportati{stats.total > 0 ? ` · ${Math.round((stats.exported / stats.total) * 100)}%` : ""}
              </div>
            </div>
            <div className="kpi">
              <div className="kpi-val">{fmtDuration(stats.totalDuration)}</div>
              <div className="kpi-lbl">Durata elaborata</div>
            </div>
            <div className="kpi kpi-accent">
              <div className="kpi-val">{fmtMinutes(stats.savedMinutes)}</div>
              <div className="kpi-lbl">Tempo risparmiato stimato*</div>
            </div>
          </section>

          {/* Distribuzione per stato: barre orizzontali */}
          <section className="stats-section">
            <h2>Distribuzione per stato</h2>
            <div className="stat-bars">
              {dist.all.map((s) => {
                const c = stats.byStatus[s] ?? 0;
                const pct = Math.round((c / dist.max) * 100);
                return (
                  <div className="stat-row" key={s}>
                    <span className="stat-row-label">{STATUS_LABELS[s] ?? s}</span>
                    <span className="stat-track" aria-hidden="true">
                      <span className="stat-fill" data-status={s} style={{ width: `${pct}%` }} />
                    </span>
                    <span className="stat-row-val">{c}</span>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Produzione: throughput + grafico giornaliero a colonne */}
          <section className="stats-section">
            <h2>Produzione</h2>
            <div className="stats-inline">
              <div className="mini">
                <div className="mini-val">{stats.avgPerDay.toFixed(1)}</div>
                <div className="mini-lbl">
                  video/giorno · media su {stats.spanDays} {stats.spanDays === 1 ? "giorno" : "giorni"}
                </div>
              </div>
              {stats.peak && (
                <div className="mini">
                  <div className="mini-val">{stats.peak.count}</div>
                  <div className="mini-lbl">picco giornaliero ({stats.peak.label})</div>
                </div>
              )}
            </div>

            <h3 className="stats-subh">Ultimi {DAILY_WINDOW} giorni</h3>
            <div className="daily" role="img" aria-label={dailyAriaLabel}>
              {stats.perDay.map((d) => {
                const pct = Math.round((d.count / maxDaily) * 100);
                return (
                  <div className="daily-col" key={d.key} title={`${d.label}: ${d.count}`}>
                    <div className="daily-bar-wrap">
                      <div
                        className="daily-bar"
                        style={{ height: `${d.count === 0 ? 0 : Math.max(6, pct)}%` }}
                      />
                    </div>
                    <div className="daily-cap">{d.count > 0 ? d.count : ""}</div>
                    <div className="daily-day">{d.label}</div>
                  </div>
                );
              })}
            </div>
          </section>

          {/* Dettagli aggiuntivi */}
          <section className="stats-section">
            <h2>Dettagli</h2>
            <dl className="stats-dl">
              <div>
                <dt>Durata media / video</dt>
                <dd>{fmtDuration(stats.avgDuration)}</dd>
              </div>
              <div>
                <dt>Sottotitoli generati</dt>
                <dd>{stats.subtitles}</dd>
              </div>
              <div>
                <dt>Export disponibili</dt>
                <dd>{stats.exportsAvailable}</dd>
              </div>
            </dl>
          </section>

          <p className="stats-note">
            <strong>* Tempo risparmiato:</strong> stima basata sull'assunzione di
            {" "}~{MIN_SAVED_PER_EXPORT} min di editing manuale (trim, tagli, sottotitoli, export)
            risparmiati per ogni video esportato ({stats.exported} × {MIN_SAVED_PER_EXPORT} min).
            Valore indicativo, non misurato.
          </p>
        </>
      )}
    </main>
  );
}
