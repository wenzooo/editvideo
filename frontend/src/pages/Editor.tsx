import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, AuthError, downloadUrl, exportFileUrl, fileUrl } from "../api";
import StatusBadge from "../components/StatusBadge";
import StylePicker from "../components/StylePicker";
import Timeline from "../components/Timeline";
import { Menu, MenuItem } from "../components/Menu";
import { EditorSkeleton, EmptyState, ErrorState } from "../components/Skeleton";
import { useToast } from "../components/Toast";
import type { ToastType } from "../components/Toast";
import { fmtTime, parseTime } from "../format";
import type { Cut, Segment, StylePreset, Video } from "../types";

interface Props {
  onAuthError: () => void;
}

// Parametri dello zoom d'ingresso: allineati ai default della pipeline di export
// (amount 12%, durata 0.9s, curva 1 + amount*sin(PI*t/D)).
const ZOOM_AMOUNT = 0.12;
const ZOOM_DUR = 0.9;
// Rapporto verticale target dell'export (1080×1920 → 0.5625).
const AR_916 = 9 / 16;

// true se l'utente preferisce meno movimento: guida per l'anteprima zoom e il whoosh.
function prefersReduceMotion(): boolean {
  return typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export default function Editor({ onAuthError }: Props) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement>(null);
  const pollRef = useRef<number | null>(null);
  // anteprima zoom d'ingresso: loop rAF, inizio del montato e AudioContext (whoosh)
  const zoomRafRef = useRef<number | null>(null);
  const montageStartRef = useRef(0);
  const audioCtxRef = useRef<AudioContext | null>(null);

  const [video, setVideo] = useState<Video | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [styles, setStyles] = useState<StylePreset[]>([]);
  const [siblings, setSiblings] = useState<Video[]>([]);

  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [cuts, setCuts] = useState<Cut[]>([]);
  const [styleId, setStyleId] = useState("karaoke_word");
  // colore parola evidenziata (stile karaoke_word); default giallo se assente
  const [karaokeColor, setKaraokeColor] = useState("#FFFF00");
  // posizione verticale (0=alto..1=basso) e scala del blocco sottotitoli
  const [subPos, setSubPos] = useState(0.80);
  const [subScale, setSubScale] = useState(1.0);
  const [cutMark, setCutMark] = useState<number | null>(null);
  const [introZoom, setIntroZoom] = useState(true);
  const [autoSilence, setAutoSilence] = useState(true);
  const [autoRetakes, setAutoRetakes] = useState(true);
  const [autoSpeedup, setAutoSpeedup] = useState(true);
  const [autoExport, setAutoExport] = useState(false);
  const [skipCuts, setSkipCuts] = useState(true);

  const [current, setCurrent] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [jobProgress, setJobProgress] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showShortcuts, setShowShortcuts] = useState(false);
  // guida di ritaglio 9:16 sul player e stato "zoom in corso" (per il feedback)
  const [showCrop, setShowCrop] = useState(false);
  const [zoomActive, setZoomActive] = useState(false);

  // --- undo/redo: stack locale sui segmenti (prima del salvataggio) ---
  const [past, setPast] = useState<Segment[][]>([]);
  const [futureSegs, setFutureSegs] = useState<Segment[][]>([]);
  // accorpa modifiche ravvicinate sullo stesso campo in un unico "passo" annullabile
  const coalesceRef = useRef<{ key: string; at: number } | null>(null);
  // ultima posizione del cursore per riga (per la divisione al cursore)
  const caretRef = useRef<{ idx: number; pos: number } | null>(null);

  // --- trova / sostituisci ---
  const [showFind, setShowFind] = useState(false);
  const [findText, setFindText] = useState("");
  const [replaceText, setReplaceText] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [matchPos, setMatchPos] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const activeRowRef = useRef<HTMLDivElement>(null);
  const findInputRef = useRef<HTMLInputElement>(null);

  const toast = useToast();

  // messaggio "flash" inline (span) + toast ctOS affiancato
  // memoizzato (toast è stabile) così i callback che lo usano restano stabili
  const flash = useCallback(
    (m: string, type: ToastType = "info") => {
      setMessage(m);
      window.setTimeout(() => setMessage(""), 5000);
      toast.push({ type, message: m });
    },
    [toast],
  );

  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof AuthError) onAuthError();
      else flash(`Errore: ${String((e as Error).message ?? e)}`, "error");
    },
    [onAuthError, flash],
  );

  // Guardia anti-race: ogni load() prende un numero di sequenza; se al ritorno
  // delle fetch una load() più recente è già partita (es. cambio rapido di video
  // con Precedente/Successivo), la risposta vecchia viene SCARTATA invece di
  // sovrascrivere lo stato del video corrente.
  const loadSeqRef = useRef(0);
  const load = useCallback(async () => {
    const seq = ++loadSeqRef.current;
    setLoadError(null);
    try {
      const [v, segs, st] = await Promise.all([
        api.getVideo(id), api.getSubtitles(id), api.styles(),
      ]);
      if (seq !== loadSeqRef.current) return; // superata da una load più recente
      setVideo(v);
      setSegments(segs);
      setPast([]);
      setFutureSegs([]);
      coalesceRef.current = null;
      setStyles(st);
      setTrimStart(v.trim_start);
      setTrimEnd(v.trim_end ?? v.duration);
      setCuts(v.cuts);
      // si tiene solo il karaoke: qualunque stile legacy viene mostrato come karaoke
      setStyleId(st.some((s) => s.id === v.subtitle_style) ? v.subtitle_style : "karaoke_word");
      setKaraokeColor(v.karaoke_color ?? "#FFFF00");
      setSubPos(v.sub_pos ?? 0.80);
      setSubScale(v.sub_scale ?? 1.0);
      setIntroZoom(v.intro_zoom);
      setAutoSilence(v.auto_silence);
      setAutoRetakes(v.auto_retakes);
      setAutoSpeedup(v.auto_speedup ?? true);
      setAutoExport(v.auto_export);
      setDirty(false);
    } catch (e) {
      if (seq !== loadSeqRef.current) return; // errore di una richiesta superata: ignora
      if (!(e instanceof AuthError)) {
        setLoadError(`Impossibile caricare il video: ${String((e as Error).message ?? e)}`);
      }
      fail(e);
    }
  }, [id, fail]);

  useEffect(() => { load(); }, [load]);

  // lista video ordinata come la dashboard (più recenti prima) per Precedente/Successivo
  useEffect(() => {
    api.listVideos()
      .then((vs) => setSiblings([...vs].sort((a, b) => b.created_at.localeCompare(a.created_at))))
      .catch(() => undefined);
  }, []);

  // al cambio di video: reset del player e stop del poll del job precedente
  useEffect(() => {
    setCurrent(0);
    setCutMark(null);
    setJobProgress(null);
    setBusy("");
    // nuovo video: azzera storico undo/redo e chiudi la barra trova
    setPast([]);
    setFutureSegs([]);
    coalesceRef.current = null;
    setShowFind(false);
    setFindText("");
    setMatchPos(0);
    return () => {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [id]);

  const navIdx = siblings.findIndex((v) => v.id === id);
  const prevVideo = navIdx > 0 ? siblings[navIdx - 1] : null;
  const nextVideo = navIdx >= 0 && navIdx < siblings.length - 1 ? siblings[navIdx + 1] : null;

  // true se è sicuro abbandonare lo stato corrente: nessuna modifica in sospeso
  // oppure l'utente conferma di voler procedere perdendo le modifiche non salvate.
  function confirmLeave(msg: string): boolean {
    return !dirty || window.confirm(msg);
  }

  function goTo(v: Video | null) {
    if (!v) return;
    if (!confirmLeave("Ci sono modifiche non salvate: cambiare video senza salvare?")) return;
    navigate(`/editor/${v.id}`);
  }

  // avviso del browser su refresh/chiusura tab/navigazione esterna con modifiche
  // non salvate (le navigazioni interne sono già protette da confirmLeave).
  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
      e.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  // ---------- player ----------
  function seek(t: number) {
    if (videoRef.current) {
      videoRef.current.currentTime = Math.max(0, Math.min(t, video?.duration ?? t));
    }
    setCurrent(t);
  }

  const activeSegment = segments.find((s) => current >= s.start && current <= s.end);

  // auto-scroll: durante il play porta la riga sottotitolo attiva nel viewport
  // della lista (scorre SOLO il contenitore, mai la pagina; niente se in pausa)
  useEffect(() => {
    const list = listRef.current;
    const row = activeRowRef.current;
    const vid = videoRef.current;
    if (!list || !row || !vid || vid.paused) return;
    const r = row.getBoundingClientRect();
    const l = list.getBoundingClientRect();
    if (r.top < l.top + 4 || r.bottom > l.bottom - 4) {
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      const delta = r.top - l.top - (list.clientHeight - row.offsetHeight) / 2;
      list.scrollTo({ top: list.scrollTop + delta, behavior: reduce ? "auto" : "smooth" });
    }
  }, [activeSegment]);

  // anteprima "montata": in riproduzione salta i tagli e le zone fuori dal trim
  function handleTimeUpdate(e: React.SyntheticEvent<HTMLVideoElement>) {
    const el = e.currentTarget;
    let t = el.currentTime;
    if (skipCuts && !el.paused && video) {
      const EPS = 0.05;
      if (t < trimStart - EPS) {
        el.currentTime = trimStart;
        t = trimStart;
      } else if (trimEnd > 0 && t > trimEnd - EPS) {
        el.pause();
        const end = Math.min(trimEnd, video.duration || trimEnd);
        el.currentTime = end;
        t = end;
      } else {
        for (const c of cuts) {
          if (t >= c.start - EPS && t < c.end - EPS) {
            el.currentTime = Math.min(c.end, video.duration || c.end);
            t = el.currentTime;
            break;
          }
        }
      }
    }
    setCurrent(t);
  }

  // ---------- anteprima zoom d'ingresso (punch-in live nel player) ----------
  // Inizio effettivo del "montato": trimStart avanzato oltre eventuali tagli che
  // coprono l'inizio (coerente con lo skip di handleTimeUpdate). È l'istante t=0
  // della curva dello zoom.
  function computeMontageStart(): number {
    const EPS = 0.05;
    let t = trimStart;
    for (let i = 0; i <= cuts.length; i++) {
      const c = cuts.find((x) => t >= x.start - EPS && t < x.end - EPS);
      if (!c) break;
      t = c.end;
    }
    return Math.min(t, video?.duration ?? t);
  }

  // ferma il loop e azzera lo scale del video (a fine curva, in pausa o allo switch)
  const stopZoom = useCallback(() => {
    if (zoomRafRef.current != null) {
      cancelAnimationFrame(zoomRafRef.current);
      zoomRafRef.current = null;
    }
    const v = videoRef.current;
    if (v) v.style.transform = "";
    setZoomActive(false);
  }, []);

  // loop rAF: scale = 1 + 0.12*sin(PI*t/0.9) per i primi 0.9s del montato, poi 1.
  const zoomTick = useCallback(() => {
    const v = videoRef.current;
    if (!v || v.paused || v.ended) { stopZoom(); return; }
    const t = v.currentTime - montageStartRef.current;
    if (t < 0) {
      // breve attesa mentre lo skip porta il player all'inizio del montato
      v.style.transform = "";
      if (t > -0.6) zoomRafRef.current = requestAnimationFrame(zoomTick);
      else stopZoom();
      return;
    }
    if (t <= ZOOM_DUR) {
      const scale = 1 + ZOOM_AMOUNT * Math.sin((Math.PI * t) / ZOOM_DUR);
      v.style.transform = `scale(${scale})`;
      zoomRafRef.current = requestAnimationFrame(zoomTick);
    } else {
      stopZoom();
    }
  }, [stopZoom]);

  // in play: avvia lo zoom solo se attivo, se non si riduce il moto e se si
  // (ri)parte vicino all'inizio del montato (altrimenti niente overhead inutile).
  function handleVideoPlay() {
    if (!introZoom || prefersReduceMotion()) return;
    const v = videoRef.current;
    if (!v) return;
    const start = computeMontageStart();
    montageStartRef.current = start;
    if (v.currentTime <= start + ZOOM_DUR + 0.1) {
      setZoomActive(true);
      if (zoomRafRef.current == null) zoomRafRef.current = requestAnimationFrame(zoomTick);
    }
  }

  // breve "whoosh" sintetico (WebAudio): opzionale e best-effort. Saltato se
  // l'utente riduce il moto o se l'audio non è disponibile; il suono vero è in export.
  function playWhoosh() {
    if (prefersReduceMotion()) return;
    try {
      const Ctor = window.AudioContext
        ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return;
      const ctx = audioCtxRef.current ?? new Ctor();
      audioCtxRef.current = ctx;
      if (ctx.state === "suspended") void ctx.resume();
      const now = ctx.currentTime;
      const dur = ZOOM_DUR;
      const frames = Math.max(1, Math.floor(ctx.sampleRate * dur));
      const buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < frames; i++) data[i] = Math.random() * 2 - 1;
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      const filter = ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.Q.value = 0.7;
      filter.frequency.setValueAtTime(300, now);
      filter.frequency.exponentialRampToValueAtTime(1800, now + dur * 0.5);
      filter.frequency.exponentialRampToValueAtTime(500, now + dur);
      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.12, now + dur * 0.5);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + dur);
      src.connect(filter);
      filter.connect(gain);
      gain.connect(ctx.destination);
      src.start(now);
      src.stop(now + dur);
    } catch { /* audio non disponibile: si ignora, resta solo il visivo */ }
  }

  // "▶ Anteprima zoom": porta il player all'inizio del montato e avvia la
  // riproduzione, facendo partire subito la curva di zoom (e il whoosh).
  function previewZoom() {
    const v = videoRef.current;
    if (!v) return;
    const start = computeMontageStart();
    montageStartRef.current = start;
    try { v.currentTime = start; } catch { /* ignore */ }
    setCurrent(start);
    if (introZoom && !prefersReduceMotion()) {
      playWhoosh();
      setZoomActive(true);
      if (zoomRafRef.current == null) zoomRafRef.current = requestAnimationFrame(zoomTick);
    }
    v.play().catch(() => undefined);
  }

  // stop dello zoom al cambio video, quando si disattiva l'opzione e allo smontaggio
  useEffect(() => { stopZoom(); }, [id, stopZoom]);
  useEffect(() => { if (!introZoom) stopZoom(); }, [introZoom, stopZoom]);
  useEffect(() => () => stopZoom(), [stopZoom]);

  // ---------- edit ----------
  const touch = () => setDirty(true);

  function setTrim(s: number, e: number) {
    setTrimStart(Math.max(0, Math.round(s * 10) / 10));
    setTrimEnd(Math.round(e * 10) / 10);
    touch();
  }

  function addCutFromMark() {
    if (cutMark == null || !video) return;
    let a = cutMark, b = current;
    if (b < a) [a, b] = [b, a];
    if (b - a < 0.1) { flash("Taglio troppo corto"); return; }
    setCuts([...cuts, { start: Math.round(a * 10) / 10, end: Math.round(b * 10) / 10 }]
      .sort((x, y) => x.start - y.start));
    setCutMark(null);
    touch();
  }

  // modifica i due estremi (inizio/fine) di un taglio esistente trascinando le
  // maniglie sulla timeline. Non riordina durante il trascinamento (evita salti);
  // il salvataggio normalizza/fonde comunque i tagli lato backend.
  function changeCut(i: number, start: number, end: number) {
    if (!video) return;
    const s = Math.max(0, Math.min(start, video.duration));
    const e = Math.max(0, Math.min(end, video.duration));
    if (e - s < 0.1) return;
    setCuts(cuts.map((c, k) => (k === i ? { start: s, end: e } : c)));
    touch();
  }

  // -------- storico locale sui segmenti (undo/redo, prima del salvataggio) --------
  const HISTORY_MAX = 100;

  // fotografa lo stato ATTUALE dei segmenti sullo stack "past" (da chiamare prima di mutare)
  function snapshotSegments() {
    setPast((p) => {
      const np = [...p, segments];
      return np.length > HISTORY_MAX ? np.slice(np.length - HISTORY_MAX) : np;
    });
    setFutureSegs([]);
  }

  // applica una modifica strutturale (merge/split/aggiungi/elimina/sostituisci): sempre annullabile
  function commitSegments(next: Segment[]) {
    snapshotSegments();
    coalesceRef.current = null;
    setSegments(next);
    touch();
  }

  const canUndo = past.length > 0;
  const canRedo = futureSegs.length > 0;

  function undo() {
    if (past.length === 0) return;
    setFutureSegs([segments, ...futureSegs]);
    setSegments(past[past.length - 1]);
    setPast(past.slice(0, -1));
    coalesceRef.current = null;
    touch();
  }

  function redo() {
    if (futureSegs.length === 0) return;
    setPast([...past, segments]);
    setSegments(futureSegs[0]);
    setFutureSegs(futureSegs.slice(1));
    coalesceRef.current = null;
    touch();
  }

  function updateSegment(i: number, patch: Partial<Segment>) {
    // le digitazioni ravvicinate sullo stesso campo diventano un unico passo di undo
    const textOnly = "text" in patch && !("start" in patch) && !("end" in patch);
    const key = `${textOnly ? "text" : "time"}:${i}`;
    const now = Date.now();
    const c = coalesceRef.current;
    if (!(c && c.key === key && now - c.at < 900)) snapshotSegments();
    coalesceRef.current = { key, at: now };
    setSegments(segments.map((s, k) => (k === i ? { ...s, ...patch } : s)));
    touch();
  }

  function addSegmentAfter(i: number) {
    const prev = segments[i];
    const start = prev ? prev.end + 0.05 : current;
    const seg: Segment = { start, end: start + 1.5, text: "" };
    const next = [...segments];
    next.splice(i + 1, 0, seg);
    commitSegments(next);
  }

  // unisce la riga i con la successiva: testo concatenato, start della prima + end della seconda
  function mergeWithNext(i: number) {
    if (i < 0 || i >= segments.length - 1) return;
    const a = segments[i], b = segments[i + 1];
    const merged: Segment = {
      start: Math.min(a.start, b.start),
      end: Math.max(a.end, b.end),
      text: [a.text.trim(), b.text.trim()].filter(Boolean).join(" "),
    };
    const next = [...segments];
    next.splice(i, 2, merged);
    commitSegments(next);
    flash("Righe unite", "success");
  }

  // divide la riga i: al cursore nel testo (se valido) oppure a metà, con tempi coerenti
  function splitSegment(i: number) {
    const seg = segments[i];
    if (!seg) return;
    if (seg.end - seg.start < 0.2) { flash("Segmento troppo corto per dividerlo"); return; }
    const L = seg.text.length;
    const c = caretRef.current;
    let pos = c && c.idx === i ? c.pos : -1;
    const useCaret = pos > 0 && pos < L;
    if (!useCaret) pos = Math.floor(L / 2);
    const dur = seg.end - seg.start;
    let cut = useCaret && L > 0 ? seg.start + dur * (pos / L) : seg.start + dur / 2;
    cut = Math.round(cut * 10) / 10;
    cut = Math.max(seg.start + 0.1, Math.min(seg.end - 0.1, cut));
    const first: Segment = { start: seg.start, end: cut, text: seg.text.slice(0, pos).trim() };
    const second: Segment = { start: cut, end: seg.end, text: seg.text.slice(pos).trim() };
    const next = [...segments];
    next.splice(i, 1, first, second);
    commitSegments(next);
    flash("Riga divisa", "success");
  }

  // ---------- trova / sostituisci ----------
  // occorrenze piatte su tutti i segmenti: { seg: indice riga, start/end: offset nel testo }
  const matches = useMemo(() => {
    const res: { seg: number; start: number; end: number }[] = [];
    if (!findText) return res;
    const needle = caseSensitive ? findText : findText.toLowerCase();
    segments.forEach((s, si) => {
      const hay = caseSensitive ? s.text : s.text.toLowerCase();
      let from = 0;
      for (;;) {
        const at = hay.indexOf(needle, from);
        if (at < 0) break;
        res.push({ seg: si, start: at, end: at + findText.length });
        from = at + Math.max(1, needle.length);
      }
    });
    return res;
  }, [segments, findText, caseSensitive]);

  // Riepilogo "montato": durata finale stimata = finestra di trim (trimEnd|durata
  // − trimStart) meno la somma dei tagli che cadono dentro la finestra. Reattivo
  // su cuts/trim; n conta solo i tagli che incidono davvero sul montato.
  const montage = useMemo(() => {
    const dur = video?.duration ?? 0;
    const end = trimEnd > 0 ? trimEnd : dur;
    const windowLen = Math.max(0, end - trimStart);
    let removed = 0;
    let n = 0;
    for (const c of cuts) {
      const ov = Math.min(c.end, end) - Math.max(c.start, trimStart);
      if (ov > 0) { removed += ov; n += 1; }
    }
    return {
      final: Math.max(0, windowLen - removed),
      removedLabel: removed.toFixed(1).replace(".", ","),
      n,
    };
  }, [cuts, trimStart, trimEnd, video?.duration]);

  // nuova ricerca → riparti dalla prima occorrenza; se il numero cala, resta in range
  useEffect(() => { setMatchPos(0); }, [findText, caseSensitive]);
  useEffect(() => {
    setMatchPos((p) => (matches.length === 0 ? 0 : Math.min(p, matches.length - 1)));
  }, [matches.length]);
  useEffect(() => { if (showFind) findInputRef.current?.focus(); }, [showFind]);

  function scrollToSeg(si: number) {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-seg-idx="${si}"]`);
    if (!el) return;
    const reduce = typeof window.matchMedia === "function"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }

  function gotoMatch(pos: number) {
    if (matches.length === 0) return;
    const p = ((pos % matches.length) + matches.length) % matches.length;
    setMatchPos(p);
    scrollToSeg(matches[p].seg);
  }

  function replaceCurrent() {
    if (matches.length === 0) return;
    const m = matches[Math.min(matchPos, matches.length - 1)];
    const seg = segments[m.seg];
    const nt = seg.text.slice(0, m.start) + replaceText + seg.text.slice(m.end);
    commitSegments(segments.map((s, k) => (k === m.seg ? { ...s, text: nt } : s)));
  }

  function replaceAll() {
    if (!findText) return;
    const needle = caseSensitive ? findText : findText.toLowerCase();
    let count = 0;
    const next = segments.map((s) => {
      const hay = caseSensitive ? s.text : s.text.toLowerCase();
      if (hay.indexOf(needle) < 0) return s;
      let out = "", from = 0;
      for (;;) {
        const at = hay.indexOf(needle, from);
        if (at < 0) { out += s.text.slice(from); break; }
        out += s.text.slice(from, at) + replaceText;
        from = at + needle.length;
        count++;
      }
      return { ...s, text: out };
    });
    if (count > 0) { commitSegments(next); flash(`Sostituite ${count} occorrenze`, "success"); }
    else flash("Nessuna occorrenza trovata");
  }

  // notify=true → toast di conferma (bottone "Salva" / scorciatoia S).
  // I chiamanti interni (export, markReady, autocut…) usano notify=false
  // per evitare doppioni: mostrano già il proprio esito.
  async function saveAll(notify = false): Promise<boolean> {
    if (!video) return false;
    setBusy("save");
    try {
      await api.patchVideo(video.id, {
        trim_start: trimStart,
        trim_end: Math.abs(trimEnd - video.duration) < 0.05 ? null : trimEnd,
        clear_trim_end: Math.abs(trimEnd - video.duration) < 0.05,
        cuts,
        subtitle_style: styleId,
        karaoke_color: karaokeColor,
        sub_pos: subPos,
        sub_scale: subScale,
        intro_zoom: introZoom,
        auto_silence: autoSilence,
        auto_retakes: autoRetakes,
        auto_speedup: autoSpeedup,
        auto_export: autoExport,
      });
      const cleaned = segments.filter((s) => s.text.trim() && s.end > s.start);
      const saved = await api.putSubtitles(video.id, cleaned);
      setSegments(saved);
      setPast([]);
      setFutureSegs([]);
      coalesceRef.current = null;
      setDirty(false);
      if (notify) flash("Salvato ✔", "success");
      return true;
    } catch (e) {
      fail(e);
      return false;
    } finally {
      setBusy("");
    }
  }

  async function markReady() {
    if (!(await saveAll())) return;
    try {
      await api.patchVideo(id, { status: "ready" });
      await load();
      flash("Video segnato come pronto ✔", "success");
    } catch (e) { fail(e); }
  }

  // ---------- job (trascrizione / export) ----------
  function pollJob(jobId: string) {
    setJobProgress(0);
    if (pollRef.current != null) window.clearInterval(pollRef.current);
    const t = window.setInterval(async () => {
      try {
        const j = await api.job(jobId);
        setJobProgress(j.progress);
        if (j.status === "done" || j.status === "error") {
          window.clearInterval(t);
          if (pollRef.current === t) pollRef.current = null;
          setJobProgress(null);
          setBusy("");
          if (j.status === "error") {
            flash(`Job fallito: ${j.error ?? "errore sconosciuto"}`, "error");
          } else if (j.type === "export") {
            flash("Export completato ✔ — download avviato", "success");
            const a = document.createElement("a");
            a.href = downloadUrl(id);
            a.download = "";
            document.body.appendChild(a);
            a.click();
            a.remove();
          } else {
            flash("Sottotitoli generati ✔", "success");
          }
          await load();
        }
      } catch (e) {
        window.clearInterval(t);
        if (pollRef.current === t) pollRef.current = null;
        setJobProgress(null);
        setBusy("");
        fail(e);
      }
    }, 1500);
    pollRef.current = t;
  }

  async function generateSubtitles() {
    if (dirty && !(await saveAll())) return;
    setBusy("transcribe");
    try {
      const j = await api.transcribe(id);
      pollJob(j.id);
    } catch (e) { setBusy(""); fail(e); }
  }

  async function doExport() {
    if (!(await saveAll())) return;
    setBusy("export");
    try {
      const j = await api.exportVideo(id);
      flash("Salvato ✔ — export in corso…", "success");
      pollJob(j.id);
    } catch (e) { setBusy(""); fail(e); }
  }

  async function saveAsTemplate() {
    if (!video) return;
    const name = window.prompt("Nome del format (es. \"Tutorial\", \"Video completo\"):");
    if (!name?.trim()) return;
    const autoSubs = window.confirm(
      "Sottotitoli automatici all'upload con questo format?\nOK = sì · Annulla = no\n\n(Zoom, taglia-silenzi e taglia-doppioni vengono presi dalle caselle Extra.)",
    );
    try {
      await api.saveTemplate({
        name: name.trim(),
        trim_start: trimStart,
        tail_trim: Math.max(0, Math.round((video.duration - trimEnd) * 10) / 10),
        cuts,
        subtitle_style: styleId,
        karaoke_color: karaokeColor,
        sub_pos: subPos,
        sub_scale: subScale,
        auto_transcribe: autoSubs,
        intro_zoom: introZoom,
        auto_silence: autoSilence,
        auto_retakes: autoRetakes,
        auto_speedup: autoSpeedup,
        auto_export: autoExport,
      });
      flash(`Format “${name.trim()}” salvato ✔ — lo trovi nella dashboard`, "success");
    } catch (e) { fail(e); }
  }

  async function doAutocut() {
    if (!(await saveAll())) return;
    setBusy("autocut");
    try {
      const v = await api.autocut(id);
      setCuts(v.cuts);
      flash(`Silenzi rilevati ✔ — ora ${v.cuts.length} tagli (controlla la timeline)`, "success");
    } catch (e) { fail(e); } finally { setBusy(""); }
  }

  // Taglia doppioni ON-DEMAND: rispecchia doAutocut ma su /retakes. Il backend
  // rende i cuts già uniti; setCuts li porta subito in timeline e anteprima.
  // In caso di 400 il messaggio del backend ("Genera prima i sottotitoli")
  // arriva via fail() → toast d'errore.
  async function doRetakes() {
    if (!(await saveAll())) return;
    setBusy("retakes");
    try {
      const v = await api.retakes(id);
      setCuts(v.cuts);
      touch();
      flash(`Doppioni rilevati ✔ — ora ${v.cuts.length} tagli (controlla la timeline)`, "success");
    } catch (e) { fail(e); } finally { setBusy(""); }
  }

  function cutSegment(s: Segment) {
    const c = { start: Math.round(s.start * 10) / 10, end: Math.round(s.end * 10) / 10 };
    if (cuts.some((x) => x.start === c.start && x.end === c.end)) return;
    setCuts([...cuts, c].sort((a, b) => a.start - b.start));
    touch();
    flash(`Aggiunto taglio ${fmtTime(c.start)}–${fmtTime(c.end)} — la caption sparirà dall'export`);
  }

  const working = video?.status === "transcribing" || video?.status === "exporting" || busy !== "";

  // scorciatoie tastiera (ignorate quando si scrive in un campo).
  // Il listener window si registra UNA sola volta (effetto con [] più sotto);
  // qui si tiene solo aggiornato un ref all'ultima closure, così durante il
  // playback (setCurrent ~4Hz) non si fa add/removeEventListener ad ogni render.
  const shortcutRef = useRef<((e: KeyboardEvent) => void) | null>(null);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      const tag = (el?.tagName ?? "").toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.repeat && e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      switch (e.key) {
        case " ": {
          e.preventDefault();
          const v = videoRef.current;
          if (!v) return;
          if (v.paused) void v.play(); else v.pause();
          break;
        }
        case "i": case "I":
          e.preventDefault();
          setTrim(current, trimEnd);
          break;
        case "o": case "O":
          e.preventDefault();
          setTrim(trimStart, current);
          break;
        case "c": case "C":
          e.preventDefault();
          if (cutMark == null) setCutMark(current); else addCutFromMark();
          break;
        case "s": case "S":
          e.preventDefault();
          if (!working && dirty) void saveAll(true);
          break;
        case "?":
          e.preventDefault();
          setShowShortcuts((v) => !v);
          break;
        case "ArrowLeft":
          e.preventDefault();
          seek(current - (e.shiftKey ? 0.1 : 1));
          break;
        case "ArrowRight":
          e.preventDefault();
          seek(current + (e.shiftKey ? 0.1 : 1));
          break;
      }
    }
    shortcutRef.current = onKey;
  });
  useEffect(() => {
    const h = (e: KeyboardEvent) => shortcutRef.current?.(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // undo/redo dei segmenti: Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y.
  // Mentre si digita in un campo (input/textarea) lasciamo l'undo NATIVO del testo:
  // lo stack locale agisce solo fuori dai campi (o dai bottoni della barra sottotitoli).
  // Stesso pattern latest-ref dell'effetto sopra: listener registrato una volta.
  const undoRedoRef = useRef<((e: KeyboardEvent) => void) | null>(null);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      const redoCombo = (k === "z" && e.shiftKey) || k === "y";
      const undoCombo = k === "z" && !e.shiftKey;
      if (!redoCombo && !undoCombo) return;
      const el = e.target as HTMLElement | null;
      const tag = (el?.tagName ?? "").toUpperCase();
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el?.isContentEditable) return;
      if (redoCombo) { if (futureSegs.length) { e.preventDefault(); redo(); } }
      else if (past.length) { e.preventDefault(); undo(); }
    }
    undoRedoRef.current = onKey;
  });
  useEffect(() => {
    const h = (e: KeyboardEvent) => undoRedoRef.current?.(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  if (!video) {
    return (
      <main id="main-content" tabIndex={-1} className="page editor">
        <div className="editor-head">
          <Link to="/" className="btn ghost">← Dashboard</Link>
        </div>
        {loadError
          ? <ErrorState message={loadError} onRetry={() => { void load(); }} />
          : <EditorSkeleton />}
      </main>
    );
  }

  const activeAutos = [introZoom, autoSilence, autoRetakes, autoSpeedup, autoExport].filter(Boolean).length;

  // formato sorgente vs export verticale 1080×1920 (scale+crop centrale 9:16).
  const srcAr = video.width > 0 && video.height > 0 ? video.width / video.height : AR_916;
  const aspectOff = Math.abs(srcAr - AR_916) > 0.02;
  // larghezza (px sorgente) della fascia 9:16 centrale che sopravvive al crop
  const keptW = srcAr >= AR_916 ? video.height * AR_916 : video.width;
  const lowRes = keptW < 1079; // < 1080px → verrà ingrandita, perde nitidezza
  // lato del riquadro 9:16 come frazione del frame (frame e box sono entrambi 9:16)
  const cropSide = Math.min(AR_916 / srcAr, srcAr / AR_916);
  const cropInset = (1 - cropSide) * 50;

  const activeMatchSeg = showFind && matches.length
    ? matches[Math.min(matchPos, matches.length - 1)].seg
    : -1;

  return (
    <main id="main-content" tabIndex={-1} className="page editor">
      <div className="editor-head">
        <Link
          to="/"
          className="btn ghost"
          onClick={(e) => {
            if (!confirmLeave("Ci sono modifiche non salvate: uscire senza salvare?"))
              e.preventDefault();
          }}
        >
          ← Dashboard
        </Link>
        <h2 className="video-title" title={video.original_name}>{video.original_name}</h2>
        <StatusBadge status={video.status} />
        <div className="spacer" />
        {message && <span className="flash">{message}</span>}
        <div className="editor-nav">
          <button
            className="btn ghost"
            disabled={!prevVideo}
            onClick={() => goTo(prevVideo)}
            title={prevVideo ? `Vai a “${prevVideo.original_name}”` : "Sei al primo video"}
          >
            ‹ Precedente
          </button>
          <button
            className="btn ghost"
            disabled={!nextVideo}
            onClick={() => goTo(nextVideo)}
            title={nextVideo ? `Vai a “${nextVideo.original_name}”` : "Sei all'ultimo video"}
          >
            Successivo ›
          </button>
        </div>
      </div>

      {video.error_message && video.status === "error" && (
        <div className="error-box">⚠ {video.error_message}</div>
      )}

      {jobProgress != null && (
        <div className="jobsbar" role="status" aria-live="polite">
          <span className="pulse" />
          <span>{busy === "export" ? "Export" : "Trascrizione"} in corso… {Math.round(jobProgress * 100)}%</span>
          <div
            className="progress slim"
            role="progressbar"
            aria-label={busy === "export" ? "Avanzamento export" : "Avanzamento trascrizione"}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(jobProgress * 100)}
          >
            <div style={{ width: `${jobProgress * 100}%` }} />
          </div>
        </div>
      )}

      <div className="editor-grid">
        {/* ---------- colonna player ---------- */}
        <section className="player-col">
          <div className="player-frame">
            <video
              ref={videoRef}
              src={fileUrl(video.id)}
              controls
              playsInline
              onTimeUpdate={handleTimeUpdate}
              onPlay={handleVideoPlay}
              onPause={stopZoom}
              onEnded={stopZoom}
            />
            {showCrop && (
              <div
                className="ed-cropguide"
                aria-hidden="true"
                style={{
                  left: `${cropInset}%`,
                  right: `${cropInset}%`,
                  top: `${cropInset}%`,
                  bottom: `${cropInset}%`,
                }}
              >
                <span className="ed-cropguide-tag">9:16</span>
              </div>
            )}
            {activeSegment && (
              <div
                className={`sub-overlay sub-style-${styleId}`}
                style={{
                  top: `${Math.round(subPos * 100)}%`,
                  bottom: "auto",
                  transform: "translateY(-50%)",
                  "--sub-scale": String(subScale),
                } as CSSProperties}
              >
                {styleId === "karaoke_word" && activeSegment.words && activeSegment.words.length > 0 ? (
                  <span>
                    {activeSegment.words.map((w, i) => (
                      <Fragment key={i}>
                        <span
                          style={current >= w[0] && current < w[1] ? { color: karaokeColor } : undefined}
                        >
                          {w[2]}
                        </span>
                        {i < activeSegment.words!.length - 1 ? " " : ""}
                      </Fragment>
                    ))}
                  </span>
                ) : (
                  <span>{activeSegment.text}</span>
                )}
              </div>
            )}
          </div>

          <div className="ed-format">
            <span
              className="ed-format-badge"
              title="Ogni export è verticale Full HD, ritagliato al centro dalla sorgente"
            >
              <span className="ed-format-dot" aria-hidden="true" />
              Export: 1080×1920 · verticale 9:16
            </span>
            <label
              className="ed-cropguide-toggle"
              title="Mostra sul player il riquadro 9:16 che verrà mantenuto nell'export"
            >
              <input
                type="checkbox"
                checked={showCrop}
                onChange={(e) => setShowCrop(e.target.checked)}
              />
              ▦ Guida ritaglio 9:16
            </label>
          </div>

          {(aspectOff || lowRes) && (
            <p className="ed-format-warn" role="note">
              ⚠ Sorgente {video.width}×{video.height}: verrà scalata e ritagliata al centro
              in verticale (per qualità piena usa video verticali ≥1080×1920).
            </p>
          )}

          <label
            className="check-label preview-skip"
            title="In riproduzione salta i tagli e le zone fuori dal trim: vedi il video come uscirà dall'export"
          >
            <input
              type="checkbox"
              checked={skipCuts}
              onChange={(e) => setSkipCuts(e.target.checked)}
            />
            ▶ Anteprima con tagli applicati
          </label>

          <div
            className="ed-montage"
            title="Durata stimata del montato finale: la finestra di trim meno i tagli"
          >
            <span className="ed-montage-key">Montaggio:</span>
            <span className="ed-montage-final">{fmtTime(montage.final)}</span>
            {montage.n > 0 ? (
              <span className="ed-montage-cut">
                {" · "}−{montage.removedLabel}s · {montage.n} {montage.n === 1 ? "taglio" : "tagli"}
              </span>
            ) : (
              <span className="ed-montage-cut">{" · "}durata piena</span>
            )}
          </div>

          <Timeline
            duration={video.duration}
            current={current}
            trimStart={trimStart}
            trimEnd={trimEnd}
            cuts={cuts}
            cutMark={cutMark}
            onSeek={seek}
            onTrim={setTrim}
            onCutChange={changeCut}
            mediaSrc={fileUrl(video.id)}
            hasAudio={video.has_audio}
          />

          <div className="trim-controls">
            <div className="control-row">
              <span className="control-label">Trim</span>
              <button className="btn small" onClick={() => setTrim(current, trimEnd)}>
                Inizio = qui
              </button>
              <input
                className="time-input"
                aria-label="Inizio trim (mm:ss)"
                value={fmtTime(trimStart)}
                onChange={(e) => {
                  const t = parseTime(e.target.value);
                  if (t != null) setTrim(t, trimEnd);
                }}
              />
              <span className="muted" aria-hidden="true">→</span>
              <input
                className="time-input"
                aria-label="Fine trim (mm:ss)"
                value={fmtTime(trimEnd)}
                onChange={(e) => {
                  const t = parseTime(e.target.value);
                  if (t != null) setTrim(trimStart, t);
                }}
              />
              <button className="btn small" onClick={() => setTrim(trimStart, current)}>
                Fine = qui
              </button>
            </div>

            <div className="control-row">
              <span className="control-label">Tagli</span>
              {cutMark == null ? (
                <button className="btn small" onClick={() => setCutMark(current)}>
                  ✂ Inizia taglio qui ({fmtTime(current)})
                </button>
              ) : (
                <>
                  <button className="btn small primary" onClick={addCutFromMark}>
                    Chiudi taglio a {fmtTime(current)}
                  </button>
                  <button className="btn small ghost" onClick={() => setCutMark(null)}>
                    Annulla
                  </button>
                  <span className="muted small">inizio: {fmtTime(cutMark)}</span>
                </>
              )}
            </div>

            {cuts.length > 0 && (
              <div className="cut-list">
                {cuts.map((c, i) => (
                  <span key={i} className="cut-pill">
                    <button
                      className="linklike"
                      onClick={() => seek(c.start)}
                      aria-label={`Vai al taglio ${fmtTime(c.start)}–${fmtTime(c.end)}`}
                    >
                      {fmtTime(c.start)}–{fmtTime(c.end)}
                    </button>
                    <button
                      className="linklike danger"
                      onClick={() => { setCuts(cuts.filter((_, k) => k !== i)); touch(); }}
                      title="Rimuovi taglio"
                      aria-label={`Rimuovi taglio ${fmtTime(c.start)}–${fmtTime(c.end)}`}
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
            )}

            <details className="ed-automations" open>
              <summary className="ed-eyebrow ed-automations-summary">
                <span>Automazioni &amp; extra</span>
                {activeAutos > 0 && <span className="ed-badge-count">{activeAutos} attive</span>}
              </summary>
              <div className="ed-auto-grid">
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={introZoom}
                    onChange={(e) => { setIntroZoom(e.target.checked); touch(); }}
                  />
                  🔍 Zoom d'ingresso con suono (punch-in all'inizio del video)
                </label>
                <div className="ed-auto-actions">
                  <button
                    type="button"
                    className={`btn small ed-preview-zoom${zoomActive ? " is-live" : ""}`}
                    onClick={previewZoom}
                    disabled={!introZoom || working}
                    title={introZoom
                      ? "Riproduce l'inizio del montato mostrando il punch-in dello zoom"
                      : "Attiva prima lo zoom d'ingresso"}
                  >
                    ▶ Anteprima zoom
                  </button>
                  <span className="ed-auto-hint">Anteprima live · l'effetto reale è impresso in export.</span>
                </div>
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={autoSilence}
                    onChange={(e) => { setAutoSilence(e.target.checked); touch(); }}
                  />
                  🔇 Taglia silenzi automatico (alla generazione sottotitoli)
                </label>
                <div className="ed-auto-actions">
                  <button
                    type="button"
                    className="linklike ed-apply-now"
                    onClick={doAutocut}
                    disabled={working}
                    title="Rileva ora i silenzi e porta i tagli in timeline (previewabili con « Anteprima con tagli applicati »)"
                  >
                    ⚡ Applica ora
                  </button>
                </div>
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={autoRetakes}
                    onChange={(e) => { setAutoRetakes(e.target.checked); touch(); }}
                  />
                  🔁 Taglia doppioni/errori del parlato (tiene l'ultima ripresa)
                </label>
                <div className="ed-auto-actions">
                  <button
                    type="button"
                    className="linklike ed-apply-now"
                    onClick={doRetakes}
                    disabled={working}
                    title="Rileva ora i doppioni e porta i tagli in timeline (richiede i sottotitoli già generati)"
                  >
                    ⚡ Applica ora
                  </button>
                </div>
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={autoSpeedup}
                    onChange={(e) => { setAutoSpeedup(e.target.checked); touch(); }}
                  />
                  ⏩ Velocizza i silenzi lunghi (es. apertura pacchetti) invece di tagliarli
                </label>
                {autoSpeedup && video?.speedups && video.speedups.length > 0 && (
                  <div className="ed-auto-actions">
                    <span className="ed-auto-hint">
                      {video.speedups.length} tratto/i velocizzato/i nell'ultima analisi
                      (×{video.speedups[0].factor}).
                    </span>
                  </div>
                )}
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={autoExport}
                    onChange={(e) => { setAutoExport(e.target.checked); touch(); }}
                  />
                  🚀 Esporta da solo dopo i sottotitoli (upload → esportato)
                </label>
                <p className="ed-auto-note">
                  Le caselle valgono per la <strong>pipeline automatica</strong> (zoom in export,
                  silenzi/doppioni alla trascrizione). « Applica ora » esegue subito l'azione così
                  vedi i tagli in timeline adesso.
                </p>
              </div>
            </details>
          </div>

          <div className="kbd-hint-row">
            <div className="muted small kbd-hint">
              ⌨ Spazio play/pausa · I inizio trim · O fine trim · C apri/chiudi taglio · S salva · ←/→ ±1s · Shift+←/→ ±0,1s · ? scorciatoie
            </div>
            <button
              type="button"
              className="btn small ghost kbd-help-btn"
              onClick={() => setShowShortcuts(true)}
              aria-haspopup="dialog"
              aria-label="Mostra le scorciatoie da tastiera"
              title="Scorciatoie da tastiera (?)"
            >
              ?
            </button>
          </div>

          {showShortcuts && <ShortcutsDialog onClose={() => setShowShortcuts(false)} />}

          <div className="actions-row">
            <button
              className="btn primary"
              onClick={doExport}
              disabled={working}
              title="Salva tutto, applica i tagli, imprime i sottotitoli e avvia il download dell'MP4 1080×1920"
            >
              💾 Salva ed esporta
            </button>
            <button className="btn" onClick={() => saveAll(true)} disabled={working || !dirty}>
              Salva
            </button>
            <button
              className="btn"
              onClick={markReady}
              disabled={working || video.status === "ready"}
              title="Salva e segna pronto per l'export in batch dalla dashboard"
            >
              ✔ Segna pronto
            </button>
            {video.has_export && (
              <>
                <a className="btn" href={exportFileUrl(video.id)} target="_blank" rel="noreferrer">
                  Guarda export
                </a>
                <a className="btn" href={downloadUrl(video.id)}>Scarica MP4</a>
              </>
            )}
            <Menu label="Strumenti" buttonClassName="ghost" align="right" className="tools-menu">
              <MenuItem icon="🔇" onClick={doAutocut} disabled={working}>
                Taglia silenzi
              </MenuItem>
              <MenuItem icon="🔁" onClick={doRetakes} disabled={working}>
                Taglia doppioni
              </MenuItem>
              <MenuItem icon="⭐" onClick={saveAsTemplate} disabled={working}>
                Salva come format
              </MenuItem>
            </Menu>
          </div>
        </section>

        {/* ---------- colonna sottotitoli ---------- */}
        <section className="subs-col">
          <div className="subs-head">
            <h3>Sottotitoli ({segments.length})</h3>
            <div className="subs-tools">
              <button
                className="btn small ghost"
                onClick={undo}
                disabled={!canUndo}
                title="Annulla (Ctrl+Z)"
                aria-label="Annulla l'ultima modifica ai sottotitoli"
              >
                ↶ Annulla
              </button>
              <button
                className="btn small ghost"
                onClick={redo}
                disabled={!canRedo}
                title="Ripristina (Ctrl+Shift+Z / Ctrl+Y)"
                aria-label="Ripristina la modifica annullata"
              >
                ↷ Ripristina
              </button>
              <button
                className="btn small ghost"
                onClick={() => setShowFind((v) => !v)}
                aria-pressed={showFind}
                aria-expanded={showFind}
                aria-controls="subs-find-bar"
                title="Trova e sostituisci nel testo dei sottotitoli"
              >
                🔍 Trova
              </button>
              <button className="btn small" onClick={generateSubtitles} disabled={working}>
                🎙 {segments.length ? "Rigenera automatici" : "Genera automatici"}
              </button>
            </div>
          </div>

          {showFind && (
            <div className="subs-find" id="subs-find-bar" role="search" aria-label="Trova e sostituisci nei sottotitoli">
              <div className="find-row">
                <input
                  ref={findInputRef}
                  className="find-input"
                  type="text"
                  value={findText}
                  onChange={(e) => setFindText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); gotoMatch(matchPos + (e.shiftKey ? -1 : 1)); }
                    else if (e.key === "Escape") { e.preventDefault(); setShowFind(false); }
                  }}
                  placeholder="Trova…"
                  aria-label="Testo da cercare"
                />
                <span className="find-count" aria-live="polite" aria-atomic="true">
                  {findText ? (matches.length ? `${Math.min(matchPos, matches.length - 1) + 1}/${matches.length}` : "0/0") : ""}
                </span>
                <button
                  className="btn small ghost find-nav"
                  onClick={() => gotoMatch(matchPos - 1)}
                  disabled={matches.length === 0}
                  aria-label="Occorrenza precedente"
                  title="Precedente (Shift+Invio)"
                >
                  ‹
                </button>
                <button
                  className="btn small ghost find-nav"
                  onClick={() => gotoMatch(matchPos + 1)}
                  disabled={matches.length === 0}
                  aria-label="Occorrenza successiva"
                  title="Successiva (Invio)"
                >
                  ›
                </button>
                <label className="find-case" title="Distingui maiuscole e minuscole">
                  <input
                    type="checkbox"
                    checked={caseSensitive}
                    onChange={(e) => setCaseSensitive(e.target.checked)}
                    aria-label="Distingui maiuscole e minuscole"
                  />
                  <span aria-hidden="true">Aa</span>
                </label>
                <button
                  className="btn small ghost find-close"
                  onClick={() => setShowFind(false)}
                  aria-label="Chiudi trova e sostituisci"
                  title="Chiudi (Esc)"
                >
                  ×
                </button>
              </div>
              <div className="find-row">
                <input
                  className="find-input"
                  type="text"
                  value={replaceText}
                  onChange={(e) => setReplaceText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); replaceCurrent(); }
                    else if (e.key === "Escape") { e.preventDefault(); setShowFind(false); }
                  }}
                  placeholder="Sostituisci con…"
                  aria-label="Testo sostitutivo"
                />
                <button
                  className="btn small"
                  onClick={replaceCurrent}
                  disabled={matches.length === 0}
                  title="Sostituisci l'occorrenza corrente"
                >
                  Sostituisci
                </button>
                <button
                  className="btn small"
                  onClick={replaceAll}
                  disabled={matches.length === 0}
                  title="Sostituisci tutte le occorrenze"
                >
                  Tutti
                </button>
              </div>
              {findText && matches.length > 0 && (() => {
                const m = matches[Math.min(matchPos, matches.length - 1)];
                const t = segments[m.seg]?.text ?? "";
                const CTX = 28;
                const preStart = Math.max(0, m.start - CTX);
                const postEnd = Math.min(t.length, m.end + CTX);
                return (
                  <div className="find-preview" aria-live="polite">
                    <span className="find-preview-loc">Riga {m.seg + 1}:</span>{" "}
                    {preStart > 0 ? "…" : ""}{t.slice(preStart, m.start)}
                    <mark>{t.slice(m.start, m.end)}</mark>
                    {t.slice(m.end, postEnd)}{postEnd < t.length ? "…" : ""}
                  </div>
                );
              })()}
            </div>
          )}

          <div className="style-section">
            <div className="ed-eyebrow">Stile burn-in</div>
            <StylePicker
              styles={styles}
              value={styleId}
              onChange={(s) => { setStyleId(s); touch(); }}
            />
            {styleId === "karaoke_word" && (
              <>
                <div className="karaoke-color-row">
                  <label htmlFor="karaoke-color" className="karaoke-color-label">
                    Colore parola evidenziata
                  </label>
                  <input
                    id="karaoke-color"
                    type="color"
                    className="karaoke-color-input"
                    value={karaokeColor}
                    onChange={(e) => { setKaraokeColor(e.target.value); touch(); }}
                    title="Colore con cui si evidenzia la parola pronunciata"
                  />
                  <span className="karaoke-color-value" aria-hidden="true">
                    {karaokeColor.toUpperCase()}
                  </span>
                </div>
                <div className="sub-adjust-row">
                  <label htmlFor="sub-pos" className="sub-adjust-label">
                    Posizione ↕ <span className="sub-adjust-val">{Math.round(subPos * 100)}%</span>
                  </label>
                  <input
                    id="sub-pos"
                    type="range"
                    min={5}
                    max={95}
                    step={1}
                    value={Math.round(subPos * 100)}
                    onChange={(e) => { setSubPos(Number(e.target.value) / 100); touch(); }}
                    title="Altezza del blocco sottotitoli: 5% = in alto, 95% = in fondo"
                  />
                </div>
                <div className="sub-adjust-row">
                  <label htmlFor="sub-scale" className="sub-adjust-label">
                    Dimensione ⤢ <span className="sub-adjust-val">{Math.round(subScale * 100)}%</span>
                  </label>
                  <input
                    id="sub-scale"
                    type="range"
                    min={60}
                    max={180}
                    step={5}
                    value={Math.round(subScale * 100)}
                    onChange={(e) => { setSubScale(Number(e.target.value) / 100); touch(); }}
                    title="Scala del testo dei sottotitoli"
                  />
                </div>
              </>
            )}
          </div>

          <div className="subs-list" ref={listRef}>
            {segments.length === 0 && (
              <EmptyState small icon="💬">
                Nessun sottotitolo: genera quelli automatici o
                <button className="linklike" onClick={() => addSegmentAfter(-1)}> aggiungi una riga</button>.
              </EmptyState>
            )}
            {segments.map((s, i) => (
              <div
                key={i}
                data-seg-idx={i}
                ref={activeSegment === s ? activeRowRef : null}
                className={`sub-row ${activeSegment === s ? "active" : ""} ${activeMatchSeg === i ? "match-row" : ""}`}
              >
                <div className="sub-times">
                  <button
                    className="linklike"
                    onClick={() => seek(s.start)}
                    title="Vai al punto"
                    aria-label={`Riproduci dalla riga ${i + 1} (${fmtTime(s.start)})`}
                  >
                    ▶
                  </button>
                  <input
                    className="time-input"
                    aria-label={`Inizio riga ${i + 1} (mm:ss)`}
                    value={fmtTime(s.start)}
                    onChange={(e) => {
                      const t = parseTime(e.target.value);
                      if (t != null) updateSegment(i, { start: t });
                    }}
                  />
                  <input
                    className="time-input"
                    aria-label={`Fine riga ${i + 1} (mm:ss)`}
                    value={fmtTime(s.end)}
                    onChange={(e) => {
                      const t = parseTime(e.target.value);
                      if (t != null) updateSegment(i, { end: t });
                    }}
                  />
                  <span className="spacer" />
                  <button
                    className="linklike"
                    onClick={() => addSegmentAfter(i)}
                    title="Aggiungi riga sotto"
                    aria-label={`Aggiungi una riga dopo la ${i + 1}`}
                  >
                    ＋
                  </button>
                  <button
                    className="linklike"
                    onClick={() => splitSegment(i)}
                    title="Dividi la riga (al cursore nel testo, o a metà)"
                    aria-label={`Dividi in due la riga ${i + 1}`}
                  >
                    ⋔
                  </button>
                  <button
                    className="linklike"
                    onClick={() => mergeWithNext(i)}
                    disabled={i >= segments.length - 1}
                    title="Unisci con la riga successiva"
                    aria-label={`Unisci la riga ${i + 1} con la successiva`}
                  >
                    ⇊
                  </button>
                  <button
                    className="linklike"
                    onClick={() => cutSegment(s)}
                    title="Parola sbagliata? Taglia dal video questo pezzo di parlato"
                    aria-label={`Taglia dal video il parlato della riga ${i + 1}`}
                  >
                    ✂
                  </button>
                  <button
                    className="linklike danger"
                    onClick={() => commitSegments(segments.filter((_, k) => k !== i))}
                    title="Elimina riga (solo il testo, il video resta)"
                    aria-label={`Elimina il testo della riga ${i + 1}`}
                  >
                    ✕
                  </button>
                </div>
                <textarea
                  rows={2}
                  aria-label={`Testo del sottotitolo, riga ${i + 1}`}
                  value={s.text}
                  onChange={(e) => updateSegment(i, { text: e.target.value })}
                  onSelect={(e) => { caretRef.current = { idx: i, pos: e.currentTarget.selectionStart ?? 0 }; }}
                  onClick={(e) => { caretRef.current = { idx: i, pos: e.currentTarget.selectionStart ?? 0 }; }}
                  placeholder="Testo del sottotitolo…"
                />
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

/**
 * Pannello scorciatoie (dialog modale). Accessibilità:
 * - role="dialog" aria-modal + titolo collegato (aria-labelledby)
 * - focus iniziale sul bottone Chiudi, focus riportato all'elemento
 *   precedente alla chiusura, trappola del Tab dentro il dialog
 * - Escape / "?" chiudono; mentre è aperto scherma le scorciatoie
 *   dell'editor (stopPropagation) così non scattano dietro al pannello
 * - nessuna animazione bloccante; entrata gestita in a11y.css e disattivata
 *   con prefers-reduced-motion
 */
function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" || e.key === "?") {
        e.preventDefault();
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key === "Tab") {
        const nodes = dialogRef.current?.querySelectorAll<HTMLElement>(
          'button, a[href], input, [tabindex]:not([tabindex="-1"])',
        );
        const f = nodes ? Array.from(nodes) : [];
        if (f.length > 0) {
          const first = f[0];
          const last = f[f.length - 1];
          const active = document.activeElement as HTMLElement | null;
          if (e.shiftKey && active === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && active === last) { e.preventDefault(); first.focus(); }
        }
      }
      // finché il dialog è aperto, le scorciatoie dell'editor restano zitte
      e.stopPropagation();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
  }, []);

  return (
    <div className="kbd-overlay" onClick={() => onClose()}>
      <div
        className="kbd-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="kbd-dialog-title"
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="kbd-dialog-head">
          <h2 id="kbd-dialog-title">Scorciatoie da tastiera</h2>
          <button
            ref={closeRef}
            type="button"
            className="linklike kbd-close"
            onClick={() => onClose()}
            aria-label="Chiudi il pannello scorciatoie"
          >
            ×
          </button>
        </div>
        <dl className="kbd-list">
          <dt><kbd>Spazio</kbd></dt><dd>Play / pausa</dd>
          <dt><kbd>I</kbd></dt><dd>Inizio trim = posizione corrente</dd>
          <dt><kbd>O</kbd></dt><dd>Fine trim = posizione corrente</dd>
          <dt><kbd>C</kbd></dt><dd>Apri / chiudi un taglio</dd>
          <dt><kbd>S</kbd></dt><dd>Salva le modifiche</dd>
          <dt><kbd>←</kbd> <kbd>→</kbd></dt><dd>Sposta di ±1 secondo</dd>
          <dt><kbd>Shift</kbd> + <kbd>←</kbd> / <kbd>→</kbd></dt><dd>Sposta di ±0,1 secondi</dd>
          <dt><kbd>Ctrl</kbd> + <kbd>Z</kbd></dt><dd>Annulla la modifica ai sottotitoli</dd>
          <dt><kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>Z</kbd></dt><dd>Ripristina (anche <kbd>Ctrl</kbd> + <kbd>Y</kbd>)</dd>
          <dt><kbd>?</kbd></dt><dd>Apri / chiudi questo pannello</dd>
          <dt><kbd>Esc</kbd></dt><dd>Chiudi il pannello</dd>
        </dl>
        <p className="muted small kbd-note">
          Le scorciatoie sono ignorate mentre scrivi in un campo di testo.
        </p>
      </div>
    </div>
  );
}
