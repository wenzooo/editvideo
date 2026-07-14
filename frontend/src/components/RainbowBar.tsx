import { type CSSProperties } from "react";

/* ============================================================
   RainbowBar — barra di avanzamento "stile terminale":
   una riga di trattini (-) su cui scorre in loop un arcobaleno.
   La parte "piena" (value) mostra i trattini colorati e animati,
   il resto resta come traccia tenue. Monospace = allineamento da
   terminale. Rispetta reduced-motion / motion-off via CSS.
   ============================================================ */
interface Props {
  /** avanzamento 0..1 */
  value: number;
  label?: string;
  /** quanti trattini disegnare (abbastanza da riempire la larghezza) */
  chars?: number;
  /** variante grande (box di caricamento) */
  big?: boolean;
  className?: string;
}

export default function RainbowBar({ value, label, chars = 64, big = false, className = "" }: Props) {
  const v = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const dashes = "-".repeat(Math.max(8, chars));
  return (
    <div
      className={`rainbow-bar ${big ? "rainbow-bar-big" : ""} ${className}`.trim()}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
    >
      <span className="rainbow-bar-track" aria-hidden="true">{dashes}</span>
      <span
        className="rainbow-bar-fill"
        aria-hidden="true"
        style={{ width: `${v * 100}%` } as CSSProperties}
      >
        {dashes}
      </span>
    </div>
  );
}
