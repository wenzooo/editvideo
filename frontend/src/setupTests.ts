// Setup globale eseguito da Vitest prima di ogni file di test (vedi
// `vitest.config.ts` -> test.setupFiles). Aggiunge i matcher DOM di
// @testing-library/jest-dom (toBeInTheDocument, toHaveClass, toHaveAttribute…)
// all'`expect` di Vitest, con la relativa augmentation dei tipi.
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library smonta i componenti automaticamente solo se esiste un
// `afterEach` globale (cioè con test.globals: true). Qui teniamo globals: false
// e importiamo tutto in modo esplicito, quindi facciamo noi il cleanup dopo
// ogni test per non far accumulare il DOM tra un test e l'altro.
afterEach(() => {
  cleanup();
});
