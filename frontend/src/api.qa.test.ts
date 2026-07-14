// Test QA per src/api.ts: wrapper `req()` (errori, header auth, Content-Type),
// gestione del token in localStorage (login/logout), helper di URL firmati e
// costruzione delle querystring. `fetch` è mockata con vi.stubGlobal e
// ripristinata dopo ogni test; localStorage è pulita prima di ogni test.
//
// Include 1 CHARACTERIZATION (QA-11 lato client): se la fetch del logout
// fallisce, il token resta in localStorage — vedi TEST_REPORT.md.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, AuthError, downloadUrl, exportFileUrl, fileUrl, getToken, thumbUrl } from "./api";

const TOKEN_KEY = "ev_token";

/** Costruisce una finta Response JSON con lo status voluto. */
function jsonResponse(body: unknown, status = 200, statusText = "") {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/** Risposta con body NON JSON: `json()` rigetta come farebbe una Response vera. */
function nonJsonResponse(status: number, statusText: string) {
  return {
    ok: false,
    status,
    statusText,
    json: () => Promise.reject(new SyntaxError("Unexpected token < in JSON")),
  } as unknown as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  localStorage.clear();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("req() — gestione delle risposte", () => {
  it("risposta 401 -> AuthError", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "non autenticato" }, 401, "Unauthorized"));
    await expect(api.me()).rejects.toBeInstanceOf(AuthError);
  });

  it("risposta !ok con detail stringa -> ApiError con message e status", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "video non trovato" }, 404, "Not Found"));
    const err = await api.getVideo("v1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("video non trovato");
    expect((err as ApiError).status).toBe(404);
  });

  it("detail oggetto -> message = JSON.stringify(detail)", async () => {
    const detail = [{ loc: ["body", "password"], msg: "field required" }];
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail }, 422, "Unprocessable Entity"));
    const err = await api.me().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe(JSON.stringify(detail));
    expect((err as ApiError).status).toBe(422);
  });

  it("body non-JSON -> fallback sullo statusText", async () => {
    fetchMock.mockResolvedValueOnce(nonJsonResponse(502, "Bad Gateway"));
    const err = await api.me().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).message).toBe("Bad Gateway");
    expect((err as ApiError).status).toBe(502);
  });

  it("risposta ok -> ritorna il json deserializzato", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ authenticated: true }));
    await expect(api.me()).resolves.toEqual({ authenticated: true });
  });
});

describe("req() — header", () => {
  it("senza token: nessun Authorization (e nessun Content-Type senza body)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ authenticated: false }));
    await api.me();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/auth/me");
    expect(init.headers).not.toHaveProperty("Authorization");
    expect(init.headers).not.toHaveProperty("Content-Type");
  });

  it("con token in localStorage: header Authorization 'Bearer <t>'", async () => {
    localStorage.setItem(TOKEN_KEY, "tok-123");
    fetchMock.mockResolvedValueOnce(jsonResponse({ authenticated: true }));
    await api.me();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toHaveProperty("Authorization", "Bearer tok-123");
  });

  it("Content-Type application/json presente solo quando c'è un body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api.login("segreta");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/auth/login");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ password: "segreta" }));
    expect(init.headers).toHaveProperty("Content-Type", "application/json");
  });
});

describe("login / logout — ciclo di vita del token", () => {
  it("login con {ok:true, token:'abc'} salva il token in localStorage", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true, token: "abc" }));
    const r = await api.login("pw");
    expect(r).toEqual({ ok: true, token: "abc" });
    expect(localStorage.getItem(TOKEN_KEY)).toBe("abc");
    expect(getToken()).toBe("abc");
  });

  it("logout andato a buon fine rimuove il token", async () => {
    localStorage.setItem(TOKEN_KEY, "abc");
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api.logout();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(getToken()).toBe("");
  });

  it("se la fetch del logout rigetta (rete giù), il token resta in localStorage", async () => {
    // CHARACTERIZATION QA-11: `logout()` fa `await req(...)` PRIMA di
    // `setToken("")`, quindi se la richiesta di rete fallisce il token NON
    // viene rimosso dal client e l'utente resta di fatto loggato — vedi
    // TEST_REPORT.md. È una decisione di design se pulire comunque il token
    // locale (logout "best effort") anche quando il server è irraggiungibile;
    // qui documentiamo il comportamento attuale.
    localStorage.setItem(TOKEN_KEY, "abc");
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api.logout()).rejects.toThrow();
    expect(localStorage.getItem(TOKEN_KEY)).toBe("abc");
  });
});

describe("URL firmati (fileUrl/thumbUrl/exportFileUrl/downloadUrl)", () => {
  it("con token: aggiunge '?t=<token urlencodato>' (caratteri speciali inclusi)", () => {
    localStorage.setItem(TOKEN_KEY, "a+b/c");
    const qs = `?t=${encodeURIComponent("a+b/c")}`; // "?t=a%2Bb%2Fc"
    expect(fileUrl("v1")).toBe(`/api/videos/v1/file${qs}`);
    expect(thumbUrl("v1")).toBe(`/api/videos/v1/thumbnail${qs}`);
    expect(exportFileUrl("v1")).toBe(`/api/videos/v1/export/file${qs}`);
    expect(downloadUrl("v1")).toBe(`/api/videos/v1/export/download${qs}`);
  });

  it("senza token: nessuna querystring", () => {
    expect(fileUrl("v1")).toBe("/api/videos/v1/file");
    expect(thumbUrl("v1")).toBe("/api/videos/v1/thumbnail");
    expect(exportFileUrl("v1")).toBe("/api/videos/v1/export/file");
    expect(downloadUrl("v1")).toBe("/api/videos/v1/export/download");
  });
});

describe("api.jobs — querystring active", () => {
  it("jobs(false) chiama /api/jobs?active=false", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.jobs(false);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/jobs?active=false");
  });

  it("jobs() senza argomenti usa active=true", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await api.jobs();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/jobs?active=true");
  });
});

describe("classi di errore", () => {
  it("AuthError è un Error", () => {
    const e = new AuthError("non autenticato");
    expect(e).toBeInstanceOf(AuthError);
    expect(e).toBeInstanceOf(Error);
    expect(e).not.toBeInstanceOf(ApiError);
  });

  it("ApiError è un Error ed espone lo status", () => {
    const e = new ApiError("conflitto", 409);
    expect(e).toBeInstanceOf(ApiError);
    expect(e).toBeInstanceOf(Error);
    expect(e.message).toBe("conflitto");
    expect(e.status).toBe(409);
  });
});
