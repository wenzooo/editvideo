import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EmptyState, ErrorState, Skeleton } from "./Skeleton";

// Smoke test: i componenti di feedback si montano e mostrano il contenuto atteso.
describe("Skeleton (smoke)", () => {
  it("renderizza un blocco shimmer con la classe base", () => {
    const { container } = render(<Skeleton width={40} height={10} />);
    expect(container.querySelector(".skel")).toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("mostra il testo passato via prop", () => {
    render(<EmptyState text="Nessun video" />);
    expect(screen.getByText("Nessun video")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("mostra il messaggio e invoca onRetry al click", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="Errore di rete" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Errore di rete");
    fireEvent.click(screen.getByRole("button", { name: "↻ Riprova" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
