import { memo, useEffect, useRef, useState } from "react";

/**
 * Waveform audio DECORATIVA disegnata dietro la timeline dell'editor.
 *
 * Calcolo 100% lato client, una sola volta per video:
 *   fetch(stesso file del <video>, con token ?t=) → OfflineAudioContext.decodeAudioData
 *   → downsampling in N picchi (memoizzati) → disegno su <canvas> in accent tenue.
 *
 * Degrada con eleganza (nessun crash, nessun blocco del render):
 *  - has_audio=false, durata eccessiva, file troppo grande, WebAudio assente,
 *    decode fallito o unmount → salta in silenzio (la timeline resta normale).
 *
 * È puramente visiva: aria-hidden, pointer-events:none (non intercetta seek/trim),
 * e allineata ai tempi (i picchi coprono l'intera larghezza = intera durata,
 * come dim/tagli/playhead della timeline).
 */

interface Props {
  /** URL del media (stesso del <video>, già con token ?t=). */
  src: string;
  /** Durata totale del video in secondi (per il gating). */
  duration: number;
  /** Il video ha una traccia audio? (noto sul video) */
  hasAudio: boolean;
}

const PEAK_COUNT = 480;                 // numero di picchi/barre disegnate
const MAX_DURATION_S = 15 * 60;         // oltre → decode troppo pesante: salta
const MAX_BYTES = 180 * 1024 * 1024;    // oltre → download troppo grande: salta
const TARGET_SR = 8000;                 // sample rate basso: meno memoria, ok per la grafica
const CACHE_MAX = 12;                   // limite voci in cache (memoria contenuta)

type OfflineCtx = OfflineAudioContext & { close?: () => void };
type OfflineCtor = new (channels: number, length: number, sampleRate: number) => OfflineAudioContext;

// Cache dei picchi per src: sopravvive a remount / navigazione avanti-indietro,
// così non si rifà il lavoro pesante ad ogni render o cambio pagina.
const peakCache = new Map<string, number[]>();

function offlineCtor(): OfflineCtor | null {
  const w = window as unknown as {
    OfflineAudioContext?: OfflineCtor;
    webkitOfflineAudioContext?: OfflineCtor;
  };
  return w.OfflineAudioContext ?? w.webkitOfflineAudioContext ?? null;
}

function makeOfflineCtx(sampleRate: number): OfflineCtx | null {
  const Ctor = offlineCtor();
  if (!Ctor) return null;
  try {
    return new Ctor(1, 1, sampleRate) as OfflineCtx;
  } catch {
    // sampleRate fuori range su alcuni browser → riprova col default
    try { return new Ctor(1, 1, 44100) as OfflineCtx; } catch { return null; }
  }
}

// decodeAudioData: supporta sia la forma Promise sia quella a callback (Safari datati).
function decodeAudio(ctx: BaseAudioContext, data: ArrayBuffer): Promise<AudioBuffer> {
  return new Promise<AudioBuffer>((resolve, reject) => {
    let settled = false;
    const ok = (b: AudioBuffer) => { if (!settled) { settled = true; resolve(b); } };
    const no = (e: unknown) => { if (!settled) { settled = true; reject(e as Error); } };
    try {
      const p = ctx.decodeAudioData(data, ok, no);
      if (p && typeof (p as Promise<AudioBuffer>).then === "function") {
        (p as Promise<AudioBuffer>).then(ok, no);
      }
    } catch (e) { no(e); }
  });
}

// Downsampling: picco (max ampiezza assoluta) per bucket, normalizzato a [0,1].
function computePeaks(buf: AudioBuffer, count: number): number[] {
  const peaks = new Array<number>(count).fill(0);
  const ch = buf.numberOfChannels > 0 ? buf.getChannelData(0) : null;
  const n = ch ? ch.length : 0;
  if (!ch || n === 0) return peaks;
  const per = n / count;
  let max = 0;
  for (let i = 0; i < count; i++) {
    const start = Math.floor(i * per);
    const end = Math.min(n, Math.floor((i + 1) * per));
    let peak = 0;
    for (let j = start; j < end; j++) {
      const a = ch[j] < 0 ? -ch[j] : ch[j];
      if (a > peak) peak = a;
    }
    peaks[i] = peak;
    if (peak > max) max = peak;
  }
  if (max > 0) {
    const inv = 1 / max;
    for (let i = 0; i < count; i++) peaks[i] *= inv;
  }
  return peaks;
}

function rememberPeaks(src: string, peaks: number[]): void {
  peakCache.set(src, peaks);
  while (peakCache.size > CACHE_MAX) {
    const oldest = peakCache.keys().next().value;
    if (oldest === undefined) break;
    peakCache.delete(oldest);
  }
}

// Colore accent del tema (ctOS) letto dalle CSS var, con fallback sicuro.
function accentFill(alpha: number): string {
  try {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue("--accent").trim();
    const m = /^#?([0-9a-fA-F]{6})$/.exec(v);
    if (m) {
      const int = parseInt(m[1], 16);
      const r = (int >> 16) & 255, g = (int >> 8) & 255, b = int & 255;
      return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
  } catch { /* ignore */ }
  return `rgba(0, 229, 199, ${alpha})`;
}

type LoadState = "idle" | "loading" | "ready" | "skip";

function Waveform({ src, duration, hasAudio }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [peaks, setPeaks] = useState<number[] | null>(() => peakCache.get(src) ?? null);
  const [state, setState] = useState<LoadState>(() => (peakCache.has(src) ? "ready" : "idle"));

  // ---- fetch + decode + downsampling: una volta per src, con fallback silenzioso ----
  useEffect(() => {
    const cached = peakCache.get(src);
    if (cached) { setPeaks(cached); setState("ready"); return; }

    // gating a monte: niente audio / durata assente o eccessiva / WebAudio non supportato
    if (!src || !hasAudio || !(duration > 0) || duration > MAX_DURATION_S || !offlineCtor()) {
      setPeaks(null); setState("skip"); return;
    }

    let cancelled = false;
    const ctrl = new AbortController();
    let ctx: OfflineCtx | null = null;
    setPeaks(null); setState("loading");

    (async () => {
      try {
        const res = await fetch(src, { signal: ctrl.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const len = Number(res.headers.get("content-length") ?? 0);
        if (len && len > MAX_BYTES) throw new Error("file troppo grande");
        const data = await res.arrayBuffer();
        if (cancelled) return;
        if (data.byteLength > MAX_BYTES) throw new Error("file troppo grande");
        ctx = makeOfflineCtx(TARGET_SR);
        if (!ctx) throw new Error("WebAudio non disponibile");
        const buf = await decodeAudio(ctx, data);
        if (cancelled) return;
        const p = computePeaks(buf, PEAK_COUNT);
        rememberPeaks(src, p);
        setPeaks(p); setState("ready");
      } catch {
        // qualunque errore (rete, decode, abort, non supportato) → timeline normale
        if (!cancelled) { setPeaks(null); setState("skip"); }
      } finally {
        try { ctx?.close?.(); } catch { /* ignore */ }
        ctx = null;
      }
    })();

    return () => {
      cancelled = true;
      ctrl.abort();
      try { ctx?.close?.(); } catch { /* ignore */ }
      ctx = null;
    };
  }, [src, hasAudio, duration]);

  // ---- disegno su canvas (HiDPI), ridisegnato solo su peaks/resize (non ad ogni frame) ----
  useEffect(() => {
    if (!peaks || peaks.length === 0) return;
    const canvas = canvasRef.current;
    const parent = canvas?.parentElement ?? null;
    if (!canvas || !parent) return;

    let raf = 0;
    const draw = () => {
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      if (w <= 0 || h <= 0) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      const g = canvas.getContext("2d");
      if (!g) return;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);
      g.fillStyle = accentFill(0.22); // tenue, coerente col tema
      const mid = h / 2;
      const n = peaks.length;
      const barW = w / n;
      const gap = barW > 2 ? 0.5 : 0;
      const drawW = Math.max(0.75, barW - gap);
      for (let i = 0; i < n; i++) {
        const bh = Math.max(1, peaks[i] * (h - 2));
        g.fillRect(i * barW, mid - bh / 2, drawW, bh);
      }
    };

    const schedule = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(draw);
    };
    schedule();

    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(schedule);
      ro.observe(parent);
    } else {
      window.addEventListener("resize", schedule);
    }
    return () => {
      if (raf) cancelAnimationFrame(raf);
      if (ro) ro.disconnect();
      else window.removeEventListener("resize", schedule);
    };
  }, [peaks]);

  if (state === "skip") return null;

  return (
    <div className={`tl-wave${state === "loading" ? " is-loading" : ""}`} aria-hidden="true">
      {peaks && peaks.length > 0 && <canvas ref={canvasRef} className="tl-wave-canvas" />}
    </div>
  );
}

// memo: la Timeline ri-renderizza ad ogni tick di riproduzione (current);
// i prop qui sono stabili → il canvas non viene ridisegnato inutilmente.
export default memo(Waveform);
