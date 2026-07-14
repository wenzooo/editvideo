import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Menu, MenuItem } from "./Menu";

function renderMenu() {
  return render(
    <Menu label="Azioni" ariaLabel="Azioni">
      <MenuItem onClick={() => {}}>Prima voce</MenuItem>
      <MenuItem onClick={() => {}}>Seconda voce</MenuItem>
    </Menu>,
  );
}

describe("Menu", () => {
  it("è chiuso di default (nessun popover, aria-expanded=false)", () => {
    renderMenu();
    const trigger = screen.getByRole("button", { name: "Azioni" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("si apre al click del trigger mostrando le voci", () => {
    renderMenu();
    const trigger = screen.getByRole("button", { name: "Azioni" });
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("Prima voce")).toBeInTheDocument();
    expect(screen.getByText("Seconda voce")).toBeInTheDocument();
  });

  it("si chiude con il tasto Escape", () => {
    renderMenu();
    const trigger = screen.getByRole("button", { name: "Azioni" });
    fireEvent.click(trigger);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("esegue l'onClick della voce e poi chiude il menu", () => {
    const onClick = vi.fn();
    render(
      <Menu label="Azioni" ariaLabel="Azioni">
        <MenuItem onClick={onClick}>Esporta</MenuItem>
      </Menu>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Azioni" }));
    fireEvent.click(screen.getByText("Esporta"));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
