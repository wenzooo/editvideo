import { useEffect, useState } from "react";
import { readPrefs } from "./SettingsPanel";

/* ============================================================
   Typewriter + cursore a blocco (Iterazione 25) — riutilizzabile
   ------------------------------------------------------------
   Piccolo componente che mostra un testo con effetto “macchina da
   scrivere” e, opzionalmente, un cursore a blocco (▉) accanto —
   tocco da terminale usato sulla brand (“EDIT//VIDEO▉”).

   Rispetto delle preferenze (come il resto dell'app):
     • Se la modalità terminale è disattivata (prefs.crt !== "on")
       oppure il movimento è ridotto (prefs.motion === "ridotto"
       o prefers-reduced-motion: reduce) → NIENTE animazione: il
       testo completo è mostrato subito, statico.
     • La visibilità/blink del cursore è governata dal CSS su
       html.crt-on (vedi styles/terminal.css), quindi reagisce dal
       vivo al toggle senza bisogno di stato qui.

   Accessibilità: il testo completo è sempre presente per i lettori
   di schermo (span .sr-only); la parte animata e il cursore sono
   aria-hidden.
   ============================================================ */

/** true se il movimento va ridotto: impostazione app “Ridotto”
 *  OPPURE preferenza di sistema prefers-reduced-motion: reduce. */
function motionReduced(): boolean {
  if (readPrefs().motion === "ridotto") return true;
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export default function Typewriter({
  text,
  className,
  cursor = true,
  speedMs = 55,
}: {
  text: string;
  className?: string;
  cursor?: boolean;
  speedMs?: number;
}) {
  // Decisione presa UNA sola volta al montaggio: animare solo se la
  // modalità terminale è attiva e il movimento non è ridotto. Un
  // toggle successivo del CRT non ri-digita (mostra semplicemente il
  // testo intero, già presente) — comportamento volutamente “leggero”.
  const [animate] = useState(() => readPrefs().crt === "on" && !motionReduced());
  const [count, setCount] = useState(() => (animate ? 0 : text.length));

  useEffect(() => {
    if (!animate) {
      setCount(text.length);
      return;
    }
    setCount(0);
    let i = 0;
    const id = window.setInterval(() => {
      i += 1;
      setCount(i);
      if (i >= text.length) window.clearInterval(id);
    }, speedMs);
    return () => window.clearInterval(id);
  }, [animate, text, speedMs]);

  return (
    <span className={className}>
      {/* testo completo per gli screen reader, indipendente dall'animazione */}
      <span className="sr-only">{text}</span>
      <span className="term-typed" aria-hidden="true">{text.slice(0, count)}</span>
      {cursor && <span className="term-cursor" aria-hidden="true">▉</span>}
    </span>
  );
}
