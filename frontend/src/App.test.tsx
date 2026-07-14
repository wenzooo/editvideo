import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// La schermata di login vive dentro App.tsx e usa il modulo API: lo mockiamo
// così il montaggio è deterministico e non tocca la rete. `AuthError` è una
// classe reale, perché App distingue le credenziali sbagliate dagli altri
// errori con `instanceof AuthError`.
const meMock = vi.fn();
const loginMock = vi.fn();

vi.mock("./api", () => {
  class AuthError extends Error {}
  return {
    AuthError,
    api: {
      me: () => meMock(),
      login: (password: string) => loginMock(password),
      logout: vi.fn(() => Promise.resolve({ ok: true })),
    },
  };
});

import App from "./App";
import { AuthError } from "./api";

beforeEach(() => {
  meMock.mockResolvedValue({ authenticated: false });
  loginMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function submitLogin() {
  const input = await screen.findByLabelText(/password/i);
  fireEvent.change(input, { target: { value: "segreto" } });
  fireEvent.click(screen.getByRole("button", { name: /entra/i }));
}

describe("Login", () => {
  it("mostra la tagline che spiega cos'è l'app", async () => {
    render(<App />);
    expect(
      await screen.findByText("Batch editor per video verticali 9:16"),
    ).toBeInTheDocument();
  });

  it("traduce le credenziali sbagliate (AuthError) in italiano", async () => {
    loginMock.mockRejectedValueOnce(new AuthError("non autenticato"));
    render(<App />);
    await submitLogin();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/password errata/i);
  });

  it("mostra un messaggio italiano generico per gli errori di rete", async () => {
    loginMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    render(<App />);
    await submitLogin();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/server non raggiungibile/i);
    // non deve trapelare la stringa tecnica inglese di fetch
    expect(alert).not.toHaveTextContent(/failed to fetch/i);
  });
});
