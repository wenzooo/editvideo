// Configurazione ESLint (flat config) minimale e coerente con Prettier.
//
// Prettier possiede TUTTA la formattazione (printWidth 100, virgolette, ecc.):
// qui non attiviamo nessuna regola stilistica, così non ci sono conflitti. Lo
// scopo di ESLint è solo la CORRETTEZZA che il typecheck non copre:
//   - variabili/import inutilizzati (tsconfig ha noUnusedLocals: false);
//   - regole dei React Hooks (rules-of-hooks + exhaustive-deps), la fonte più
//     comune di bug da stale-closure in una SPA.
// no-undef è spento sui file TS perché è TypeScript a garantire i nomi definiti.
import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist", "coverage", "node_modules"] },
  {
    files: ["src/**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      // TypeScript copre già i nomi non definiti.
      "no-undef": "off",
      // Il carattere "_" iniziale marca un argomento/variabile scartato di proposito.
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // any esplicito è talvolta pragmatico: non lo trattiamo come errore.
      "@typescript-eslint/no-explicit-any": "off",
      // `cond ? a() : b()` e `cond && a()` usati per l'effetto collaterale sono
      // idiomi voluti nel codice: non li segnaliamo come espressioni inutili.
      "@typescript-eslint/no-unused-expressions": [
        "error",
        { allowShortCircuit: true, allowTernary: true },
      ],
    },
  },
);
