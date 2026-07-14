export interface Cut {
  start: number;
  end: number;
}

export interface Video {
  id: string;
  original_name: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  size_bytes: number;
  has_audio: boolean;
  status: string;
  error_message: string | null;
  trim_start: number;
  trim_end: number | null;
  cuts: Cut[];
  speedups?: { start: number; end: number; factor: number }[];
  subtitle_style: string;
  karaoke_color?: string | null;
  sub_pos?: number;
  sub_scale?: number;
  intro_zoom: boolean;
  auto_silence: boolean;
  auto_retakes: boolean;
  auto_speedup?: boolean;
  auto_export: boolean;
  subtitle_count: number;
  has_export: boolean;
  created_at: string;
  updated_at: string;
}

export interface Segment {
  id?: number;
  idx?: number;
  start: number;
  end: number;
  text: string;
  // timestamp per-parola [start, end, testo] (base del karaoke); null se assenti
  // (es. caption editate a mano). Sulla timeline ORIGINALE, come start/end.
  words?: [number, number, string][] | null;
}

export interface Job {
  id: string;
  video_id: string;
  type: string;
  status: string;
  progress: number;
  error: string | null;
  video_name?: string | null;
}

export interface StylePreset {
  id: string;
  label: string;
  description: string;
}

export interface BatchResult {
  enqueued: number;
  skipped: number;
}

export interface Template {
  id: string;
  name: string;
  trim_start: number;
  tail_trim: number;
  cuts: Cut[];
  subtitle_style: string;
  karaoke_color?: string | null;
  sub_pos?: number;
  sub_scale?: number;
  auto_transcribe: boolean;
  intro_zoom: boolean;
  auto_silence: boolean;
  auto_retakes: boolean;
  auto_speedup?: boolean;
  auto_export: boolean;
}

export interface ApplyResult {
  applied: number;
  skipped: number;
}

export interface UploadResult {
  created: Video[];
  errors: { name: string; reason: string }[];
}
