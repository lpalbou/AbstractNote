import React from "react";
import ReactDOM from "react-dom/client";
import "@abstractframework/ui-kit/theme.css";
import "@abstractframework/panel-chat/panel_chat.css";
import { installViewportVars } from "@abstractframework/ui-kit";
import "./styles.css";
import { App } from "./App";

installViewportVars();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
