/* The Light / Dark / System switch (Green & Mint, design-docs/theme-kit).
   theme.js is the kit's controller, unchanged; this file only wires the three
   buttons to it and shows them. The theme itself was already set before the
   first paint by the snippet in <head>. */
import { initTheme, setPreference, getPreference } from "./theme.js";

initTheme();

const group = document.querySelector(".theme-toggle");
if (group) {
  const buttons = group.querySelectorAll("button[data-pref]");
  const sync = () => buttons.forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.pref === getPreference())));
  buttons.forEach((b) => b.addEventListener("click", () => { setPreference(b.dataset.pref); sync(); }));
  addEventListener("storage", (e) => { if (e.key === "ds-theme") sync(); });   // other tabs
  sync();
  group.hidden = false;
}
