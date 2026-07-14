import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusBadge from "./StatusBadge";

describe("StatusBadge", () => {
  it("mostra l'etichetta italiana e le classi per uno stato noto", () => {
    const { container } = render(<StatusBadge status="ready" />);
    expect(screen.getByText("Pronto")).toBeInTheDocument();
    const span = container.querySelector("span");
    expect(span).toHaveClass("badge", "badge-ready");
  });

  it("mostra un'etichetta per ogni stato conosciuto", () => {
    const { getByText } = render(<StatusBadge status="transcribing" />);
    expect(getByText("Trascrizione…")).toBeInTheDocument();
  });

  it("fa da fallback allo stato grezzo se sconosciuto", () => {
    const { container } = render(<StatusBadge status="mistero" />);
    expect(screen.getByText("mistero")).toBeInTheDocument();
    expect(container.querySelector("span")).toHaveClass("badge-mistero");
  });
});
