/// <reference types="vitest/config" />
import { defineConfig, mergeConfig } from "vitest/config";
import viteConfig from "./vite.config";

// Config dei test SEPARATA da `vite.config.ts` (che resta la config di
// produzione, invariata). Riusiamo la config Vite esistente — così i test
// vedono lo stesso plugin React e gli stessi alias — e vi aggiungiamo solo le
// impostazioni specifiche di Vitest. La build di produzione non è toccata.
export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: false,
      environment: "jsdom",
      setupFiles: ["./src/setupTests.ts"],
      include: ["src/**/*.{test,spec}.{ts,tsx}"],
      restoreMocks: true,
    },
  }),
);
