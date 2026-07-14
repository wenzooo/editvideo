import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, AuthError, downloadUrl, thumbUrl } from "../api";
import JobsBar from "../components/JobsBar";
import { Menu, MenuItem, MenuSeparator } from "../components/Menu";
import Onboarding, { isOnboardingDone, markOnboardingDone } from "../components/Onboarding";
import { DashboardListSkeleton, EmptyState, ErrorState } from "../components/Skeleton";
import StatusBadge, { STATUS_LABELS } from "../components/StatusBadge";
import { useToast } from "../components/Toast";
import type { ToastType } from "../components/Toast";
import UploadZone from "../components/UploadZone";
import type { UploadStatus } from "../components/UploadZone";
import { fmtDate, fmtSize, fmtTime } from "../format";
import type { Job, Template, Video } from "../types";

const FILTERS = ["all", "uploaded", "review", "ready", "exported", "error"] as const;

// ordine di lavorazione, usato dall'ordinamento "Stato"
const STATUS_ORDER: Record<string, number> = {
  uploaded: 0, transcribing: 1, review: 2, ready: 3, exporting: 4, exported: 5, error: 6,
};

type SortKey = "name" | "duration" | "status" | "created";
type SortDir = "asc" | "desc";

// comparatori puri per colonna (ordine ascendente); la direzione è applicata a valle
const SORT_COMPARATORS: Record<SortKey, (a: Video, b: Video) => number> = {
  name: (a, b) => a.original_name.localeCompare(b.original_name, "it", { numeric: true, sensitivity: "base" }),
  duration: (a, b) => a.duration - b.duration,
  status: (a, b) => (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99),
  created: (a, b) => a.created_at.localeCompare(b.created_at),
};

// etichetta colonna + direzione naturale al primo click sull'intestazione
const SORT_META: Record<SortKey, { label: string; firstDir: SortDir }> = {
  name: { label: "Nome", firstDir: "asc" },
  duration: { label: "Durata", firstDir: "desc" },
  status: { label: "Stato", firstDir: "asc" },
  created: { label: "Caricato", firstDir: "desc" },
};

export default function Dashboard({ onAuthError }: { onAuthError: () => void }) {
  const toast = useToast();
  const [videos, setVideos] = useState<Video[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: "created", dir: "desc" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [uploading, setUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<UploadStatus | null>(null);
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [tplId, setTplId] = useState("");
  // onboarding: aperto al primo avvio (finché il flag non è in localStorage),
  // riapribile on-demand da "❔ Guida". Alla chiusura persiste il flag.
  const [onboardingOpen, setOnboardingOpen] = useState(() => !isOnboardingDone());
  const closeOnboarding = useCallback(() => { markOnboardingDone(); setOnboardingOpen(false); }, []);
  const openOnboarding = useCallback(() => setOnboardingOpen(true), []);

  const refreshVideos = useCallback(async () => {
    try {
      const v = await api.listVideos();
      setVideos(v);
      // togli dalla selezione i video che non esistono più
      setSelected((prev) => {
        if (prev.size === 0) return prev;
        const next = new Set(Array.from(prev).filter((id) => v.some((x) => x.id === id)));
        return next.size === prev.size ? prev : next;
      });
      setLoaded(true);
      setLoadError(null);
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
      else setLoadError(`Impossibile caricare i video: ${String((e as Error).message ?? e)}`);
    }
  }, [onAuthError]);

  const refreshJobs = useCallback(async () => {
    try {
      setJobs(await api.jobs(true));
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
    }
  }, [onAuthError]);

  const refresh = useCallback(
    async () => { await Promise.all([refreshVideos(), refreshJobs()]); },
    [refreshVideos, refreshJobs],
  );

  const jobsActive = jobs.length > 0;

  // jobs: poll leggero e costante (2.5s) per accorgersi subito dei lavori nuovi
  useEffect(() => {
    refreshJobs();
    const t = window.setInterval(refreshJobs, 2500);
    return () => window.clearInterval(t);
  }, [refreshJobs]);

  // lista video: poll adattivo — 2.5s quando c'è attività (job o upload), 10s a riposo
  useEffect(() => {
    refreshVideos();
    const t = window.setInterval(refreshVideos, jobsActive || uploading ? 2500 : 10000);
    return () => window.clearInterval(t);
  }, [refreshVideos, jobsActive, uploading]);

  const loadTemplates = useCallback(() => {
    api.templates().then(setTemplates).catch(() => undefined);
  }, []);
  useEffect(() => { loadTemplates(); }, [loadTemplates]);

  const jobByVideo = useMemo(() => {
    const m = new Map<string, Job>();
    for (const j of jobs) if (!m.has(j.video_id)) m.set(j.video_id, j);
    return m;
  }, [jobs]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: videos.length };
    for (const v of videos) c[v.status] = (c[v.status] ?? 0) + 1;
    return c;
  }, [videos]);

  // filtro stato + ricerca per nome + ordinamento per colonna (tutto client-side).
  // Memoizzato su [videos, filter, query, sort]: il polling dei job (2.5s) non
  // tocca queste dipendenze, quindi la vista non viene ricomputata inutilmente.
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = videos.filter((v) =>
      (filter === "all" || v.status === filter) &&
      (!q || v.original_name.toLowerCase().includes(q)));
    const cmp = SORT_COMPARATORS[sort.key];
    const dir = sort.dir === "asc" ? 1 : -1;
    // tie-break stabile: a parità di chiave, i più recenti in cima
    const byRecent = (a: Video, b: Video) => b.created_at.localeCompare(a.created_at);
    list.sort((a, b) => {
      const primary = cmp(a, b) * dir;
      return primary !== 0 ? primary : byRecent(a, b);
    });
    return list;
  }, [videos, filter, query, sort]);

  const allShownSelected = shown.length > 0 && shown.every((v) => selected.has(v.id));
  const someShownSelected = shown.some((v) => selected.has(v.id));

  // quanti selezionati NON sono nella vista attuale (nascosti da filtro/ricerca)
  const hiddenSelected = useMemo(() => {
    if (selected.size === 0) return 0;
    const shownIds = new Set(shown.map((v) => v.id));
    return Array.from(selected).filter((id) => !shownIds.has(id)).length;
  }, [selected, shown]);

  // click su intestazione: se già attiva inverte la direzione, altrimenti
  // parte dalla direzione naturale della colonna
  function applySort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: SORT_META[key].firstDir });
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function toggleSelectAllShown() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allShownSelected) shown.forEach((v) => next.delete(v.id));
      else shown.forEach((v) => next.add(v.id));
      return next;
    });
  }

  // seleziona (solo aggiunta) tutti i video attualmente visibili
  function selectAllShown() {
    setSelected((prev) => {
      const next = new Set(prev);
      shown.forEach((v) => next.add(v.id));
      return next;
    });
  }

  // messaggio "flash" inline (span verde) + toast ctOS affiancato
  function flash(msg: string, ms = 5000, type: ToastType = "info") {
    setMessage(msg);
    window.setTimeout(() => setMessage(""), ms);
    toast.push({ type, message: msg, duration: ms });
  }

  async function run(fn: () => Promise<unknown>, okMsg?: (r: any) => string,
                     after?: (r: any) => void) {
    try {
      const r = await fn();
      if (okMsg) flash(okMsg(r), 5000, "success");
      refresh();
      after?.(r);
    } catch (e) {
      if (e instanceof AuthError) onAuthError();
      else flash(`Errore: ${String((e as Error).message ?? e)}`, 8000, "error");
    }
  }

  // azioni sulla selezione: una chiamata per video, in sequenza; i 409
  // (stato non compatibile / già in lavorazione) vengono saltati senza fermarsi
  async function runSelected(
    label: string,
    action: (id: string) => Promise<unknown>,
    opts?: { confirmMsg?: string; clearAfter?: boolean },
  ) {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    if (opts?.confirmMsg && !window.confirm(opts.confirmMsg)) return;
    let ok = 0, skipped = 0, failed = 0;
    for (const id of ids) {
      try {
        await action(id);
        ok++;
      } catch (e) {
        if (e instanceof AuthError) { onAuthError(); return; }
        if (e instanceof ApiError && e.status === 409) skipped++;
        else failed++;
      }
    }
    if (opts?.clearAfter) setSelected(new Set());
    flash(
      `${label}: ${ok} ok` +
      (skipped ? ` · ${skipped} saltati` : "") +
      (failed ? ` · ${failed} errori` : ""),
      failed ? 8000 : 5000,
      failed ? "error" : "success",
    );
    refresh();
  }

  // upload robusto e VELOCE: pool concorrente (piu' file insieme), un errore
  // non ferma gli altri; progresso aggregato sull'intera coda.
  async function handleUpload(files: File[]) {
    if (files.length === 0) return;
    setUploading(true);
    const total = files.length;
    const errors: { name: string; reason: string }[] = [];
    const fracs = new Array<number>(total).fill(0);
    let created = 0;
    let done = 0;
    let authFailed = false;

    const pushStatus = (activeName: string) => {
      const totalFrac = fracs.reduce((a, b) => a + b, 0) / total;
      setUploadStatus({
        index: Math.min(done + 1, total), total, name: activeName,
        fileFrac: totalFrac, totalFrac,
      });
    };

    let next = 0;
    const worker = async () => {
      // JS single-thread: next++ e' atomico tra gli await
      for (let i = next++; i < total && !authFailed; i = next++) {
        const f = files[i];
        pushStatus(f.name);
        try {
          const r = await api.upload([f], tplId || undefined, (frac) => {
            fracs[i] = frac;
            pushStatus(f.name);
          });
          created += r.created.length;
          errors.push(...r.errors.map((x) => ({ name: x.name, reason: x.reason })));
        } catch (e) {
          if (e instanceof AuthError) { authFailed = true; break; }
          errors.push({ name: f.name, reason: String((e as Error).message ?? e) });
        }
        fracs[i] = 1;
        done++;
        pushStatus(f.name);
        refreshVideos(); // i video compaiono in lista man mano che finiscono
      }
    };

    // fino a 3 upload in parallelo: batch di 30-40 video molto piu' rapido
    const CONCURRENCY = Math.min(3, total);
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));

    setUploading(false);
    setUploadStatus(null);
    if (authFailed) { onAuthError(); return; }
    const tpl = tplId ? templates.find((t) => t.id === tplId) : undefined;
    let msg = `${created}/${total} video caricati`;
    if (tpl) msg += ` · format “${tpl.name}” applicato${tpl.auto_transcribe ? " + sottotitoli in coda" : ""}`;
    if (errors.length) {
      const first = errors.slice(0, 3).map((e) => `${e.name}: ${e.reason}`).join(" · ");
      msg += ` — ${errors.length} falliti (${first}${errors.length > 3 ? ` e altri ${errors.length - 3}` : ""})`;
    }
    flash(msg, errors.length ? 12000 : 5000, errors.length ? "error" : "success");
    refresh();
  }

  // intestazione ordinabile: <th aria-sort> con <button> attivabile da tastiera.
  // La freccia è puramente visiva (aria-hidden); aria-sort annuncia lo stato.
  const sortableTh = (key: SortKey) => {
    const active = sort.key === key;
    return (
      <th
        scope="col"
        className={`sortable${active ? " sorted" : ""}`}
        aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      >
        <button
          type="button"
          className="th-sort"
          onClick={() => applySort(key)}
          title={`Ordina per ${SORT_META[key].label.toLowerCase()}`}
        >
          <span>{SORT_META[key].label}</span>
          <span className="sort-arrow" aria-hidden="true">
            {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
          </span>
        </button>
      </th>
    );
  };

  return (
    <main id="main-content" tabIndex={-1} className="page dashboard">
      {/* titolo di pagina per screen reader e navigazione per heading (tasto H):
          la home non ha un titolo visibile, il primo elemento è la dropzone */}
      <h1 className="sr-only">EditVideo — Dashboard</h1>
      <Onboarding open={onboardingOpen} onClose={closeOnboarding} />

      <UploadZone uploading={uploading} status={uploadStatus} onFiles={handleUpload} />

      <div className="batch-bar">
        <label className="tpl-picker">
          Format (stile + automazioni):
          <select
            value={tplId}
            onChange={(e) => setTplId(e.target.value)}
            title="Format: preset di stile e automazioni (sottotitoli, silenzi, retake, export) da applicare in blocco ai video caricati"
          >
            <option value="">— nessuno —</option>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
                {t.auto_transcribe ? " ⚡" : ""}
                {t.auto_silence ? " 🔇" : ""}
                {t.auto_retakes ? " 🔁" : ""}
                {t.intro_zoom ? " 🔍" : ""}
                {t.auto_export ? " 🚀" : ""}
              </option>
            ))}
          </select>
          {tplId && (
            <button
              className="linklike danger"
              title="Elimina questo format"
              aria-label="Elimina questo format"
              onClick={() => {
                const t = templates.find((x) => x.id === tplId);
                if (t && window.confirm(`Eliminare il format “${t.name}”?`)) {
                  run(
                    async () => { await api.deleteTemplate(tplId); setTplId(""); loadTemplates(); },
                    () => `Format “${t.name}” eliminato`,
                  );
                }
              }}
            >
              ✕
            </button>
          )}
        </label>
        <button
          className="btn"
          disabled={!tplId || (counts["uploaded"] ?? 0) + (counts["error"] ?? 0) === 0}
          onClick={() => run(() => api.applyTemplateBatch(tplId),
            (r) => `Format applicato a ${r.applied} video${r.skipped ? ` (${r.skipped} saltati)` : ""}`)}
          title="Applica trim/tagli/stile del format a tutti i video caricati"
        >
          🎯 Applica ai caricati
        </button>
        <button
          className="btn primary"
          disabled={(counts["uploaded"] ?? 0) + (counts["error"] ?? 0) === 0}
          onClick={() => run(api.batchAuto,
            (r) => `✨ Auto: ${r.enqueued} video in lavorazione — poi li controlli in “Da controllare”`,
            () => setFilter("review"))}
          title="Un click su tutti i caricati: sottotitoli + taglia-silenzi + taglia-doppioni + velocizza-silenzi. Restano in “Da controllare” per l’anteprima; l’export NON parte da solo — quando sei pronto premi “Esporta tutti”."
        >
          ✨ Auto: fai tutto (poi controlli, non esporta)
        </button>
        <button
          className="btn"
          disabled={(counts["review"] ?? 0) + (counts["ready"] ?? 0) === 0}
          onClick={() => run(api.batchExportReviewed,
            (r) => `Export avviato: ${r.enqueued} video in coda`)}
          title="Esporta in blocco tutti i video controllati/pronti (dopo aver visto le anteprime)."
        >
          ▶ Esporta tutti{(counts["review"] ?? 0) + (counts["ready"] ?? 0) > 0
            ? ` (${(counts["review"] ?? 0) + (counts["ready"] ?? 0)})` : ""}
        </button>
        {message && <span className="flash">{message}</span>}
        <div className="spacer" />
        <button
          type="button"
          className="btn ghost small onb-help-btn"
          onClick={openOnboarding}
          title="Riapri la guida rapida: come funziona EditVideo"
        >
          ❔ Guida
        </button>
      </div>

      <JobsBar jobs={jobs} />

      <div className="filters">
        {FILTERS.map((f) => (
          <button
            key={f}
            className={`chip ${filter === f ? "active" : ""}`}
            aria-pressed={filter === f}
            onClick={() => setFilter(f)}
            title={f === "all"
              ? "Mostra tutti i video"
              : `Filtra: mostra solo i video nello stato “${STATUS_LABELS[f] ?? f}”`}
          >
            {f === "all" ? "Tutti" : STATUS_LABELS[f] ?? f} ({counts[f] ?? 0})
          </button>
        ))}
        <div className="spacer" />
        <input
          className="search-input"
          type="search"
          placeholder="Cerca per nome…"
          aria-label="Cerca video per nome"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <label className="tpl-picker">
          Ordina:
          <select
            value={`${sort.key}:${sort.dir}`}
            aria-label="Ordina i video"
            onChange={(e) => {
              const [key, dir] = e.target.value.split(":") as [SortKey, SortDir];
              setSort({ key, dir });
            }}
          >
            <option value="created:desc">Più recenti</option>
            <option value="created:asc">Meno recenti</option>
            <option value="name:asc">Nome A→Z</option>
            <option value="name:desc">Nome Z→A</option>
            <option value="duration:desc">Durata: lunghi</option>
            <option value="duration:asc">Durata: brevi</option>
            <option value="status:asc">Stato: flusso</option>
            <option value="status:desc">Stato: inverso</option>
          </select>
        </label>
      </div>

      {selected.size > 0 && (
        <div className="batch-bar selection-bar">
          <strong>{selected.size} selezionati</strong>
          {hiddenSelected > 0 && (
            <span className="muted small">({hiddenSelected} fuori dai filtri)</span>
          )}
          {shown.length > 0 && !allShownSelected && (
            <button className="linklike sel-all" onClick={selectAllShown}>
              Seleziona i {shown.length} visibili
            </button>
          )}
          <button
            className="btn small"
            onClick={() => runSelected("Sottotitoli", (id) => api.transcribe(id))}
            title="Genera i sottotitoli dei video selezionati, uno alla volta"
          >
            ⚡ Sottotitoli ({selected.size})
          </button>
          <Menu label="Altre azioni" buttonClassName="small ghost" align="left">
            <MenuItem
              icon="📤"
              hint={selected.size}
              onClick={() => runSelected("Export", (id) => api.exportVideo(id))}
            >
              Esporta selezionati
            </MenuItem>
            <MenuSeparator />
            <MenuItem
              icon="🗑"
              danger
              hint={selected.size}
              onClick={() => runSelected("Eliminazione", (id) => api.deleteVideo(id), {
                confirmMsg: `Eliminare ${selected.size} video e tutti i loro file?`,
                clearAfter: true,
              })}
            >
              Elimina selezionati
            </MenuItem>
          </Menu>
          <button className="btn small ghost deselect" onClick={() => setSelected(new Set())}>
            Deseleziona
          </button>
        </div>
      )}

      {!loaded ? (
        loadError ? (
          <ErrorState message={loadError} onRetry={() => refresh()} />
        ) : (
          <DashboardListSkeleton />
        )
      ) : shown.length === 0 ? (
        <EmptyState
          icon={videos.length === 0 ? "🎬" : "🔍"}
          text={
            videos.length === 0
              ? "Nessun video: trascina qui i file per iniziare."
              : query.trim()
                ? "Nessun video corrisponde alla ricerca."
                : "Nessun video in questo stato."
          }
        />
      ) : (
        <table className="videos">
          <thead>
            <tr>
              <th className="check-col" scope="col">
                <input
                  type="checkbox"
                  className="row-check"
                  ref={(el) => { if (el) el.indeterminate = someShownSelected && !allShownSelected; }}
                  checked={allShownSelected}
                  disabled={shown.length === 0}
                  onChange={toggleSelectAllShown}
                  title={allShownSelected ? "Deseleziona tutti i visibili" : "Seleziona tutti i visibili"}
                  aria-label="Seleziona tutti i video visibili"
                />
              </th>
              <th scope="col"><span className="sr-only">Anteprima</span></th>
              {sortableTh("name")}
              {sortableTh("duration")}
              <th scope="col">Formato</th>
              <th scope="col">Sott.</th>
              {sortableTh("status")}
              {sortableTh("created")}
              <th className="actions-col" scope="col">Azioni</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((v) => {
              const job = jobByVideo.get(v.id);
              const canTranscribe = ["uploaded", "review", "ready", "error"].includes(v.status) && !job;
              const canExport = ["review", "ready"].includes(v.status) && !job;
              return (
                <tr key={v.id} className={v.status === "error" ? "row-error" : ""}>
                  <td className="check-col">
                    <input
                      type="checkbox"
                      className="row-check"
                      checked={selected.has(v.id)}
                      onChange={() => toggleSelect(v.id)}
                      aria-label={`Seleziona ${v.original_name}`}
                    />
                  </td>
                  <td>
                    <Link to={`/editor/${v.id}`} aria-label={`Apri l'editor di ${v.original_name}`}>
                      <img className="thumb" src={thumbUrl(v.id)} alt="" loading="lazy" />
                    </Link>
                  </td>
                  <td className="name-cell">
                    <Link to={`/editor/${v.id}`} className="video-name">{v.original_name}</Link>
                    <div className="muted small">
                      {fmtSize(v.size_bytes)}
                      {v.cuts.length > 0 && ` · ${v.cuts.length} tagli`}
                      {v.intro_zoom && <span title="Zoom d'ingresso con suono attivo">{" 🔍"}</span>}
                      {v.auto_silence && <span title="Taglia silenzi automatico attivo">{" 🔇"}</span>}
                      {v.auto_retakes && <span title="Taglia doppioni automatico attivo">{" 🔁"}</span>}
                      {v.auto_export && <span title="Export automatico dopo i sottotitoli">{" 🚀"}</span>}
                    </div>
                    {v.status === "error" && v.error_message && (
                      <div className="error-text small">{v.error_message}</div>
                    )}
                  </td>
                  <td>{fmtTime(v.duration)}</td>
                  <td className="muted">{v.width}×{v.height}</td>
                  <td>{v.subtitle_count > 0 ? v.subtitle_count : "—"}</td>
                  <td>
                    <StatusBadge status={v.status} />
                    {job && (
                      <div
                        className="progress slim"
                        title={job.type}
                        role="progressbar"
                        aria-label={`Avanzamento ${job.type}`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={Math.round(job.progress * 100)}
                      >
                        <div style={{ width: `${job.progress * 100}%` }} />
                      </div>
                    )}
                  </td>
                  <td className="muted">{fmtDate(v.created_at)}</td>
                  <td className="actions">
                    <Link className="btn small row-primary" to={`/editor/${v.id}`}>Editor</Link>
                    <Menu label="⋯" hideCaret buttonClassName="small ghost" align="right" title="Altre azioni" ariaLabel={`Altre azioni per ${v.original_name}`}>
                      <MenuItem
                        icon="⚡"
                        disabled={!canTranscribe}
                        onClick={() => run(() => api.transcribe(v.id), () => "Sottotitoli in coda ⚡")}
                      >
                        Genera sottotitoli
                      </MenuItem>
                      <MenuItem
                        icon="📤"
                        disabled={!canExport}
                        onClick={() => run(() => api.exportVideo(v.id), () => "Export in coda 📤")}
                      >
                        Esporta
                      </MenuItem>
                      <MenuItem
                        icon="⤓"
                        href={downloadUrl(v.id)}
                        download
                        disabled={!v.has_export}
                      >
                        Scarica MP4
                      </MenuItem>
                      <MenuSeparator />
                      <MenuItem
                        icon="🗑"
                        danger
                        onClick={() => {
                          if (window.confirm(`Eliminare “${v.original_name}” e tutti i suoi file?`)) {
                            run(() => api.deleteVideo(v.id), () => `“${v.original_name}” eliminato`);
                          }
                        }}
                      >
                        Elimina
                      </MenuItem>
                    </Menu>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </main>
  );
}
