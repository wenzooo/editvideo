import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// La Dashboard interroga video/job/template all'avvio: mockiamo l'intero modulo
// API così il montaggio è deterministico e non tocca la rete.
vi.mock("../api", () => ({
  AuthError: class AuthError extends Error {},
  ApiError: class ApiError extends Error {},
  downloadUrl: (id: string) => `/dl/${id}`,
  thumbUrl: (id: string) => `/thumb/${id}`,
  api: {
    listVideos: vi.fn(() => Promise.resolve([])),
    jobs: vi.fn(() => Promise.resolve([])),
    templates: vi.fn(() => Promise.resolve([])),
  },
}));

import Dashboard from "./Dashboard";

function renderDashboard() {
  return render(
    <MemoryRouter>
      <Dashboard onAuthError={() => {}} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  // onboarding già "visto": niente overlay iniziale che sporca il DOM
  localStorage.setItem("onboarding_done", "1");
});

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("Dashboard — accessibilità e chiarezza etichette", () => {
  it("espone un <h1> di pagina (heading per screen reader / tasto H)", () => {
    renderDashboard();
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
  });

  it("il pulsante Auto è distinto dai sottotitoli e dice cosa fa senza hover", async () => {
    renderDashboard();
    // etichetta esplicita: «fai tutto» + la nota chiave «non esporta» visibile
    const auto = await screen.findByRole("button", { name: /Auto: fai tutto/ });
    expect(auto).toHaveTextContent(/non esporta/);
    // niente ⚡ (riservato ai soli sottotitoli): usa un'icona distinta
    expect(auto.textContent).not.toContain("⚡");
  });

  it("l'etichetta «Format» è autoesplicativa (stile + automazioni)", async () => {
    renderDashboard();
    await waitFor(() =>
      expect(screen.getByText(/Format \(stile \+ automazioni\)/)).toBeInTheDocument(),
    );
  });
});
