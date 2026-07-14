import type {
  ApplyResult, BatchResult, Cut, Job, Segment, StylePreset, Template, UploadResult, Video,
} from "./types";

export class AuthError extends Error {}

/** Errore API che conserva lo status HTTP (utile per ignorare i 409 nelle azioni in serie). */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

// Token di sessione in localStorage: immune ai blocchi dei cookie di terze
// parti (iframe di Hugging Face) e ai riavvii del browser.
const TOKEN_KEY = "ev_token";
export const getToken = (): string => {
  try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
};
const setToken = (t: string) => {
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
};
const authQS = () => (getToken() ? `?t=${encodeURIComponent(getToken())}` : "");

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, {
    headers: {
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    ...init,
  });
  if (r.status === 401) throw new AuthError("non autenticato");
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* ignore */ }
    throw new ApiError(detail, r.status);
  }
  return r.json() as Promise<T>;
}

export const api = {
  // auth
  me: () => req<{ authenticated: boolean }>("/api/auth/me"),
  login: async (password: string) => {
    const r = await req<{ ok: boolean; token?: string }>(
      "/api/auth/login", { method: "POST", body: JSON.stringify({ password }) });
    if (r.token) setToken(r.token);
    return r;
  },
  logout: async () => {
    const r = await req<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
    setToken("");
    return r;
  },

  // videos
  listVideos: () => req<Video[]>("/api/videos"),
  getVideo: (id: string) => req<Video>(`/api/videos/${id}`),
  patchVideo: (id: string, data: Partial<{
    trim_start: number; trim_end: number | null; clear_trim_end: boolean;
    cuts: Cut[]; subtitle_style: string; karaoke_color: string; status: string;
    sub_pos: number; sub_scale: number;
    intro_zoom: boolean; auto_silence: boolean; auto_retakes: boolean;
    auto_speedup: boolean; auto_export: boolean;
  }>) => req<Video>(`/api/videos/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  autocut: (id: string) => req<Video>(`/api/videos/${id}/autocut`, { method: "POST" }),
  retakes: (id: string) => req<Video>(`/api/videos/${id}/retakes`, { method: "POST" }),
  deleteVideo: (id: string) => req<{ ok: boolean }>(`/api/videos/${id}`, { method: "DELETE" }),

  // subtitles
  getSubtitles: (id: string) => req<Segment[]>(`/api/videos/${id}/subtitles`),
  putSubtitles: (id: string, segments: Segment[]) =>
    req<Segment[]>(`/api/videos/${id}/subtitles`, {
      method: "PUT",
      body: JSON.stringify({ segments: segments.map(({ start, end, text }) => ({ start, end, text })) }),
    }),

  // actions
  transcribe: (id: string) => req<Job>(`/api/videos/${id}/transcribe`, { method: "POST" }),
  exportVideo: (id: string) => req<Job>(`/api/videos/${id}/export`, { method: "POST" }),
  batchTranscribe: () => req<BatchResult>("/api/batch/transcribe", { method: "POST" }),
  batchExport: () => req<BatchResult>("/api/batch/export", { method: "POST" }),
  // UN CLICK: attiva silenzi+doppioni sui caricati e li manda in trascrizione;
  // restano in "da controllare" (anteprima), l'export NON parte da solo.
  batchAuto: () => req<BatchResult>("/api/batch/auto", { method: "POST" }),
  // Conferma ed esporta in blocco i video "da controllare" (e i pronti).
  batchExportReviewed: () => req<BatchResult>("/api/batch/export-reviewed", { method: "POST" }),

  // formats (template di editing)
  templates: () => req<Template[]>("/api/templates"),
  saveTemplate: (data: Omit<Template, "id">) =>
    req<Template>("/api/templates", { method: "POST", body: JSON.stringify(data) }),
  deleteTemplate: (id: string) =>
    req<{ ok: boolean }>(`/api/templates/${id}`, { method: "DELETE" }),
  applyTemplateBatch: (templateId: string) =>
    req<ApplyResult>("/api/batch/apply-template", {
      method: "POST", body: JSON.stringify({ template_id: templateId }),
    }),

  // jobs / meta
  jobs: (active = true) => req<Job[]>(`/api/jobs?active=${active}`),
  job: (id: string) => req<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) =>
    req<{ ok: boolean; job_id: string; status: string; canceled: boolean }>(
      `/api/jobs/${id}/cancel`, { method: "POST" }),
  styles: () => req<StylePreset[]>("/api/styles"),

  // upload con progresso (XHR: fetch non espone l'upload progress)
  upload(files: File[], templateId?: string, onProgress?: (frac: number) => void): Promise<UploadResult> {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      files.forEach((f) => fd.append("files", f));
      if (templateId) fd.append("template_id", templateId);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/videos/upload");
      if (getToken()) xhr.setRequestHeader("Authorization", `Bearer ${getToken()}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        if (xhr.status === 401) return reject(new AuthError("non autenticato"));
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          let detail = `Upload fallito (${xhr.status})`;
          try {
            const j = JSON.parse(xhr.responseText);
            if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
          } catch { /* ignore */ }
          reject(new ApiError(detail, xhr.status));
        }
      };
      xhr.onerror = () => reject(new Error("Errore di rete durante l'upload"));
      xhr.send(fd);
    });
  },
};

export const fileUrl = (id: string) => `/api/videos/${id}/file${authQS()}`;
export const thumbUrl = (id: string) => `/api/videos/${id}/thumbnail${authQS()}`;
export const exportFileUrl = (id: string) => `/api/videos/${id}/export/file${authQS()}`;
export const downloadUrl = (id: string) => `/api/videos/${id}/export/download${authQS()}`;
