/* Green & Mint head snippet (design-docs/theme-kit/head-snippet.html), as a
   file: the portal's Content-Security-Policy allows no inline script. Loaded in
   <head> before the stylesheets, without defer, so the first paint is already
   in the chosen theme: the stored choice, else the operating system's. */
(function () {
  var pref = 'system';
  try { var v = localStorage.getItem('ds-theme'); if (v === 'light' || v === 'dark') pref = v; } catch (e) {}
  var light = pref === 'light' || (pref === 'system' && matchMedia('(prefers-color-scheme: light)').matches);
  document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
})();
