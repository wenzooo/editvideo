/* ============================================================
   Overlay CRT full-screen (Iterazione 25)
   ------------------------------------------------------------
   Sovrapposizione decorativa in stile terminale/CRT: scanline
   orizzontali sottili, vignettatura leggera e un flicker molto
   tenue. Montato UNA volta in App.

   - È puramente estetico: aria-hidden e pointer-events:none (via
     CSS), quindi NON intercetta mai i click e non è annunciato dai
     lettori di schermo.
   - La visibilità è guidata dalla classe html.crt-on (impostazione
     “Effetto CRT / terminale” in SettingsPanel): quando l'effetto è
     disattivato l'elemento resta display:none, senza costo di paint
     e senza stato React. Reagisce dal vivo al toggle.
   - z-index/opacità/keyframes sono in styles/terminal.css, importato
     una volta a livello globale in main.tsx (le regole html.crt-on
     valgono per tutta l'app, non solo per l'overlay).
   ============================================================ */
export default function CRTOverlay() {
  return (
    <div className="crt-overlay" aria-hidden="true">
      <div className="crt-flicker" />
    </div>
  );
}
