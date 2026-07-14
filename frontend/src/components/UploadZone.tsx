import { useRef, useState } from "react";
import RainbowBar from "./RainbowBar";

export interface UploadStatus {
  /** indice 1-based del file in corso */
  index: number;
  total: number;
  name: string;
  /** progresso del file corrente, 0..1 */
  fileFrac: number;
  /** progresso complessivo della coda, 0..1 */
  totalFrac: number;
}

interface Props {
  uploading: boolean;
  status: UploadStatus | null;
  onFiles: (files: File[]) => void;
}

export default function UploadZone({ uploading, status, onFiles }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);

  function handle(files: FileList | null) {
    if (!files || files.length === 0) return;
    onFiles(Array.from(files));
  }

  return (
    <div
      className={`dropzone ${drag ? "drag" : ""} ${uploading ? "busy" : ""}`}
      role="button"
      tabIndex={uploading ? -1 : 0}
      aria-label="Carica video: trascina i file qui oppure premi Invio per selezionarli"
      aria-busy={uploading}
      onClick={() => !uploading && inputRef.current?.click()}
      onKeyDown={(e) => {
        if (uploading) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        if (!uploading) handle(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="video/mp4,video/quicktime,video/webm,video/x-matroska,.mp4,.mov,.m4v,.webm,.mkv,.avi"
        style={{ display: "none" }}
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => { handle(e.target.files); e.target.value = ""; }}
      />
      {uploading ? (
        <div className="upload-progress" role="status" aria-live="polite">
          <div className="upload-file-line">
            <span className="upload-prompt" aria-hidden="true">&gt;_</span>{" "}
            {status
              ? `Caricamento ${status.name} — ${Math.round(status.totalFrac * 100)}%`
              : "Caricamento…"}
          </div>
          <RainbowBar
            big
            value={status?.fileFrac ?? 0}
            label="Avanzamento file corrente"
          />
          {status && status.total > 1 && (
            <>
              <div className="muted small">
                Totale: {Math.round(status.totalFrac * 100)}% · {status.index - 1}/{status.total} completati
              </div>
              <RainbowBar
                value={status.totalFrac}
                label="Avanzamento totale della coda"
              />
            </>
          )}
        </div>
      ) : (
        <div>
          <strong>Trascina qui i video</strong> oppure clicca per selezionarli
          <div className="muted">Upload multiplo · mp4, mov, m4v, webm, mkv, avi</div>
        </div>
      )}
    </div>
  );
}
