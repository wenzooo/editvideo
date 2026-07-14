import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Segment, StylePreset, Video } from "../types";

// L'editor carica video/sottotitoli/stili all'avvio: mockiamo l'intero modulo API
// così il montaggio è deterministico e non tocca la rete.
const video: Video = {
  id: "v1",
  original_name: "clip.mp4",
  duration: 30,
  width: 1080,
  height: 1920,
  fps: 30,
  size_bytes: 1000,
  has_audio: true,
  status: "uploaded",
  error_message: null,
  trim_start: 0,
  trim_end: null,
  cuts: [],
  subtitle_style: "karaoke_word",
  karaoke_color: "#FFFF00",
  sub_pos: 0.8,
  sub_scale: 1,
  intro_zoom: true,
  auto_silence: true,
  auto_retakes: true,
  auto_speedup: true,
  auto_export: false,
  subtitle_count: 0,
  has_export: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const styles: StylePreset[] = [
  { id: "karaoke_word", label: "Karaoke", description: "parola per parola" },
];
const segments: Segment[] = [];

vi.mock("../api", () => ({
  AuthError: class AuthError extends Error {},
  downloadUrl: (id: string) => `/dl/${id}`,
  exportFileUrl: (id: string) => `/ex/${id}`,
  fileUrl: (id: string) => `/file/${id}`,
  api: {
    getVideo: vi.fn(() => Promise.resolve(video)),
    getSubtitles: vi.fn(() => Promise.resolve(segments)),
    styles: vi.fn(() => Promise.resolve(styles)),
    listVideos: vi.fn(() => Promise.resolve([video])),
  },
}));

import Editor from "./Editor";

function renderEditor() {
  return render(
    <MemoryRouter initialEntries={["/editor/v1"]}>
      <Routes>
        <Route path="/editor/:id" element={<Editor onAuthError={() => {}} />} />
        <Route path="/" element={<div>DASHBOARD-MARKER</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

// rende "sporco" lo stato dell'editor spuntando una casella delle automazioni
function makeDirty() {
  fireEvent.click(screen.getByRole("checkbox", { name: /Zoom d'ingresso/ }));
}

beforeEach(() => {
  // jsdom non implementa matchMedia: stub minimale usato dall'editor
  window.matchMedia = vi.fn().mockReturnValue({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
  }) as unknown as typeof window.matchMedia;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Editor — guardia modifiche non salvate", () => {
  it("«← Dashboard» con modifiche non salvate chiede conferma e, se negata, non naviga", async () => {
    renderEditor();
    await screen.findByText("clip.mp4");
    makeDirty();

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("link", { name: /Dashboard/ }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("DASHBOARD-MARKER")).not.toBeInTheDocument();
    expect(screen.getByText("clip.mp4")).toBeInTheDocument();
  });

  it("«← Dashboard» naviga se l'utente conferma di scartare le modifiche", async () => {
    renderEditor();
    await screen.findByText("clip.mp4");
    makeDirty();

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("link", { name: /Dashboard/ }));

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByText("DASHBOARD-MARKER")).toBeInTheDocument());
  });

  it("senza modifiche «← Dashboard» naviga subito senza chiedere conferma", async () => {
    renderEditor();
    await screen.findByText("clip.mp4");

    const confirmSpy = vi.spyOn(window, "confirm");
    fireEvent.click(screen.getByRole("link", { name: /Dashboard/ }));

    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByText("DASHBOARD-MARKER")).toBeInTheDocument());
  });

  it("con modifiche non salvate beforeunload viene annullato (avviso del browser)", async () => {
    renderEditor();
    await screen.findByText("clip.mp4");
    makeDirty();

    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
  });

  it("senza modifiche beforeunload non viene annullato", async () => {
    renderEditor();
    await screen.findByText("clip.mp4");

    const ev = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
  });
});
