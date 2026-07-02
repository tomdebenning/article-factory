import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { FactoryIdentityProvider } from "./context/FactoryIdentityContext";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <FactoryIdentityProvider>
        <App />
      </FactoryIdentityProvider>
    </BrowserRouter>
  </StrictMode>,
);
