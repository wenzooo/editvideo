import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";
import "./styles/menu.css";
import "./styles/animations.css";
import "./styles/dashboard.css";
import "./styles/editor.css";
import "./styles/a11y.css";
import "./styles/terminal.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
