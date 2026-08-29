import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import "./index.css";
import { GEOCITIES_MODE } from "./lib/geocities";

document.documentElement.classList.add("dark");

if (GEOCITIES_MODE) {
  document.documentElement.classList.add("geocities");
  import("./styles/geocities.css");
}

createRoot(document.getElementById("root")!).render(<App />);
