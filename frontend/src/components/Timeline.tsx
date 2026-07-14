import { Fragment, useEffect, useRef, useState } from "react";
import type { Cut } from "../types";
import { fmtTime } from "../format";
import Waveform from "./Waveform";

interface Props {
  duration: number;
  current: number;
  trimStart: number;
  trimEnd: number;
  cuts: Cut[];
  cutMark: number | null;
  onSeek: (t: number) => void;
  onTrim: (start: number, end: number) => void;
  /** modifica i due estremi di un taglio (inizio/fine) trascinando le maniglie */
  onCutChange?: (index: number, start: number, end: number) => void;
  /** URL del media (con token) per la waveform di sfondo — opzionale. */
  mediaSrc?: string;
  /** Il video ha audio? Se assente/false la waveform viene saltata. */
  hasAudio?: boolean;
}

type Drag =
  | { kind: "seek" }
  | { kind: "trim"; side: "start" | "end" }
  | { kind: "cut"; index: number; side: "start" | "end" }
  | null;

const r2 = (t: number) => Math.round(t * 100) / 100;

export default function Timeline({
  duration, current, trimStart, trimEnd, cuts, cutMark, onSeek, onTrim, onCutChange,
  mediaSrc, hasAudio,
}: Props) {
  const railRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<Drag>(null);

  const pct = (t: number) => `${Math.max(0, Math.min(100, (t / duration) * 100))}%`;

  function posToTime(clientX: number): number {
    const rect = railRef.current!.getBoundingClientRect();
    const frac = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    return frac * duration;
  }

  useEffect(() => {
    if (!drag) return;
    function move(e: PointerEvent) {
      const t = posToTime(e.clientX);
      if (drag!.kind === "seek") onSeek(t);
      else if (drag!.kind === "trim") {
        if (drag!.side === "start") onTrim(Math.min(t, trimEnd - 0.1), trimEnd);
        else onTrim(trimStart, Math.max(t, trimStart + 0.1));
      } else if (drag!.kind === "cut" && onCutChange) {
        const c = cuts[drag!.index];
        if (!c) return;
        if (drag!.side === "start") onCutChange(drag!.index, r2(Math.max(0, Math.min(t, c.end - 0.1))), c.end);
        else onCutChange(drag!.index, c.start, r2(Math.min(duration, Math.max(t, c.start + 0.1))));
      }
    }
    function up() { setDrag(null); }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag, trimStart, trimEnd, duration, cuts, onCutChange]);

  // tastiera per una maniglia di taglio: frecce = ±step, clamp entro l'altro estremo
  function cutKey(e: React.KeyboardEvent, i: number, side: "start" | "end") {
    if (!onCutChange) return;
    const c = cuts[i];
    if (!c) return;
    const step = e.shiftKey ? 0.1 : 1;
    let done = true;
    if (side === "start") {
      if (e.key === "ArrowLeft" || e.key === "ArrowDown") onCutChange(i, r2(Math.max(0, c.start - step)), c.end);
      else if (e.key === "ArrowRight" || e.key === "ArrowUp") onCutChange(i, r2(Math.min(c.end - 0.1, c.start + step)), c.end);
      else done = false;
    } else {
      if (e.key === "ArrowLeft" || e.key === "ArrowDown") onCutChange(i, c.start, r2(Math.max(c.start + 0.1, c.end - step)));
      else if (e.key === "ArrowRight" || e.key === "ArrowUp") onCutChange(i, c.start, r2(Math.min(duration, c.end + step)));
      else done = false;
    }
    if (done) { e.preventDefault(); e.stopPropagation(); }
  }

  return (
    <div className="timeline-wrap">
      <div
        className="timeline"
        ref={railRef}
        role="group"
        aria-label="Timeline: trascina o usa le maniglie di trim e dei tagli (frecce per regolare)"
        onPointerDown={(e) => {
          setDrag({ kind: "seek" });
          onSeek(posToTime(e.clientX));
        }}
      >
        {/* waveform decorativa di sfondo: dietro tutto, non intercetta i pointer */}
        {mediaSrc && duration > 0 && (
          <Waveform src={mediaSrc} duration={duration} hasAudio={hasAudio ?? false} />
        )}
        {/* zone escluse dal trim */}
        <div className="tl-dim" style={{ left: 0, width: pct(trimStart) }} />
        <div className="tl-dim" style={{ left: pct(trimEnd), right: 0 }} />
        {/* tagli interni + maniglie inizio/fine trascinabili */}
        {cuts.map((c, i) => (
          <Fragment key={i}>
            <div
              className="tl-cut"
              style={{ left: pct(c.start), width: `calc(${pct(c.end)} - ${pct(c.start)})` }}
              title={`Taglio ${fmtTime(c.start)} → ${fmtTime(c.end)}`}
            />
            {onCutChange && (
              <>
                <div
                  className="tl-cut-handle start"
                  style={{ left: pct(c.start) }}
                  role="slider"
                  tabIndex={0}
                  aria-label={`Taglio ${i + 1}: inizio`}
                  aria-valuemin={0}
                  aria-valuemax={duration}
                  aria-valuenow={c.start}
                  aria-valuetext={fmtTime(c.start)}
                  title={`Inizio taglio: ${fmtTime(c.start)} — trascina per modificare`}
                  onPointerDown={(e) => { e.stopPropagation(); setDrag({ kind: "cut", index: i, side: "start" }); }}
                  onKeyDown={(e) => cutKey(e, i, "start")}
                />
                <div
                  className="tl-cut-handle end"
                  style={{ left: pct(c.end) }}
                  role="slider"
                  tabIndex={0}
                  aria-label={`Taglio ${i + 1}: fine`}
                  aria-valuemin={0}
                  aria-valuemax={duration}
                  aria-valuenow={c.end}
                  aria-valuetext={fmtTime(c.end)}
                  title={`Fine taglio: ${fmtTime(c.end)} — trascina per modificare`}
                  onPointerDown={(e) => { e.stopPropagation(); setDrag({ kind: "cut", index: i, side: "end" }); }}
                  onKeyDown={(e) => cutKey(e, i, "end")}
                />
              </>
            )}
          </Fragment>
        ))}
        {cutMark != null && <div className="tl-cutmark" style={{ left: pct(cutMark) }} />}
        <div className="tl-playhead" style={{ left: pct(current) }} />
        <div
          className="tl-handle left"
          style={{ left: pct(trimStart) }}
          role="slider"
          tabIndex={0}
          aria-label="Inizio trim"
          aria-orientation="horizontal"
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={trimStart}
          aria-valuetext={fmtTime(trimStart)}
          onPointerDown={(e) => { e.stopPropagation(); setDrag({ kind: "trim", side: "start" }); }}
          onKeyDown={(e) => {
            const step = e.shiftKey ? 0.1 : 1;
            let done = true;
            if (e.key === "ArrowLeft" || e.key === "ArrowDown") onTrim(Math.max(0, trimStart - step), trimEnd);
            else if (e.key === "ArrowRight" || e.key === "ArrowUp") onTrim(Math.min(trimEnd - 0.1, trimStart + step), trimEnd);
            else if (e.key === "Home") onTrim(0, trimEnd);
            else if (e.key === "End") onTrim(Math.max(0, trimEnd - 0.1), trimEnd);
            else done = false;
            if (done) { e.preventDefault(); e.stopPropagation(); }
          }}
          title={`Inizio: ${fmtTime(trimStart)}`}
        />
        <div
          className="tl-handle right"
          style={{ left: pct(trimEnd) }}
          role="slider"
          tabIndex={0}
          aria-label="Fine trim"
          aria-orientation="horizontal"
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={trimEnd}
          aria-valuetext={fmtTime(trimEnd)}
          onPointerDown={(e) => { e.stopPropagation(); setDrag({ kind: "trim", side: "end" }); }}
          onKeyDown={(e) => {
            const step = e.shiftKey ? 0.1 : 1;
            let done = true;
            if (e.key === "ArrowLeft" || e.key === "ArrowDown") onTrim(trimStart, Math.max(trimStart + 0.1, trimEnd - step));
            else if (e.key === "ArrowRight" || e.key === "ArrowUp") onTrim(trimStart, Math.min(duration, trimEnd + step));
            else if (e.key === "Home") onTrim(trimStart, Math.min(duration, trimStart + 0.1));
            else if (e.key === "End") onTrim(trimStart, duration);
            else done = false;
            if (done) { e.preventDefault(); e.stopPropagation(); }
          }}
          title={`Fine: ${fmtTime(trimEnd)}`}
        />
      </div>
      <div className="tl-times">
        <span>0:00.0</span>
        <span className="tl-current">{fmtTime(current)}</span>
        <span>{fmtTime(duration)}</span>
      </div>
    </div>
  );
}
