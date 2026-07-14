import type { StylePreset } from "../types";

interface Props {
  styles: StylePreset[];
  value: string;
  onChange: (id: string) => void;
}

export default function StylePicker({ styles, value, onChange }: Props) {
  return (
    <div className="style-grid">
      {styles.map((s) => (
        <button
          key={s.id}
          type="button"
          className={`style-card ${value === s.id ? "selected" : ""}`}
          aria-pressed={value === s.id}
          aria-label={`Stile ${s.label}: ${s.description}`}
          onClick={() => onChange(s.id)}
          title={s.description}
        >
          <div className={`style-sample bg-video sub-style-${s.id}`}>
            {s.id === "karaoke_word"
              ? <span>Anteprima <em>testo</em></span>
              : <span>Anteprima testo</span>}
          </div>
          <div className="style-label">{s.label}</div>
        </button>
      ))}
    </div>
  );
}
