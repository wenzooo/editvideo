export function fmtTime(t: number | null | undefined): string {
  if (t == null || isNaN(t)) return "-";
  const sign = t < 0 ? "-" : "";
  t = Math.abs(t);
  // si arrotonda ai decimi di secondo PRIMA di scomporre minuti/secondi, così
  // 59.99 diventa "1:00.0" e non "0:60.0" (rollover del minuto al bordo).
  const ds = Math.round(t * 10);
  const m = Math.floor(ds / 600);
  const s = (ds - m * 600) / 10;
  return `${sign}${m}:${s.toFixed(1).padStart(4, "0")}`;
}

/** Accetta "1:23.4", "83.4" o "83" -> secondi. */
export function parseTime(v: string): number | null {
  v = v.trim();
  if (!v) return null;
  const parts = v.split(":");
  let sec = 0;
  for (const p of parts) {
    const n = parseFloat(p.replace(",", "."));
    // ogni componente dev'essere non-negativa: "1:-30" non è 30s ma input invalido
    if (isNaN(n) || n < 0) return null;
    sec = sec * 60 + n;
  }
  return sec >= 0 ? sec : null;
}

export function fmtSize(bytes: number): string {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(2) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  return Math.round(bytes / 1e3) + " KB";
}

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" }) +
    " " + d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
}
