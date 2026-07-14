import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import CommandPalette from "./CommandPalette";

// jsdom non implementa scrollIntoView, usato dall'effetto che tiene visibile
// la voce attiva all'apertura: lo neutralizziamo per non far fallire i test.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// `useToast` degrada a no-op senza provider, quindi basta il Router (serve a
// `useNavigate` usato dai comandi).
function renderPalette() {
  return render(
    <MemoryRouter>
      <CommandPalette />
    </MemoryRouter>,
  );
}

describe("CommandPalette", () => {
  it("mostra un punto d'accesso visibile anche a palette chiusa (scopribilità)", () => {
    renderPalette();
    // Il launcher è sempre presente...
    expect(screen.getByRole("button", { name: "Apri la palette dei comandi" })).toBeInTheDocument();
    // ...mentre il dialog della palette non è ancora montato.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("si apre al click del launcher, senza bisogno della tastiera (touch)", () => {
    renderPalette();
    fireEvent.click(screen.getByRole("button", { name: "Apri la palette dei comandi" }));
    expect(screen.getByRole("dialog", { name: "Palette dei comandi" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Cerca un comando" })).toBeInTheDocument();
  });
});
