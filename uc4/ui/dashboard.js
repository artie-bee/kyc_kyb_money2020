/* Filtering and sorting. The table is complete in the HTML; this only narrows
   and reorders what is already there, so the page still reads with the script
   blocked. */
(function () {
  var table = document.getElementById("cases");
  if (!table) return;
  var tbody = table.tBodies[0];
  var rows  = Array.prototype.slice.call(tbody.rows);
  var shown = document.getElementById("shown");
  var empty = document.getElementById("empty");

  function checked(name) {
    var out = {};
    document.querySelectorAll('input[name="' + name + '"]:checked')
      .forEach(function (b) { out[b.value] = true; });
    return out;
  }

  function apply() {
    var st = checked("status"), ow = checked("owner"), n = 0;
    rows.forEach(function (tr) {
      var ok = st[tr.dataset.status] && (ow[tr.dataset.owner] || !tr.dataset.owner);
      tr.hidden = !ok;
      if (ok) n++;
    });
    shown.textContent = n;
    empty.hidden = n !== 0;
  }

  document.querySelectorAll('.filters input').forEach(function (b) {
    b.addEventListener("change", apply);
  });

  var dir = {};
  Array.prototype.forEach.call(table.tHead.rows[0].cells, function (th, i) {
    if (!th.dataset.sort) return;
    th.tabIndex = 0;
    function sort() {
      var desc = dir[i] === "ascending";
      Array.prototype.forEach.call(table.tHead.rows[0].cells, function (o) {
        o.removeAttribute("aria-sort");
      });
      th.setAttribute("aria-sort", desc ? "descending" : "ascending");
      dir[i] = desc ? "descending" : "ascending";
      var num = th.dataset.sort === "num";
      rows.sort(function (a, b) {
        var x = a.cells[i].textContent.trim(), y = b.cells[i].textContent.trim();
        if (num) {
          // an em dash is "not scored", which sorts below every real number
          var nx = parseFloat(x), ny = parseFloat(y);
          if (isNaN(nx)) nx = -1;
          if (isNaN(ny)) ny = -1;
          return desc ? ny - nx : nx - ny;
        }
        return desc ? y.localeCompare(x) : x.localeCompare(y);
      });
      rows.forEach(function (tr) { tbody.appendChild(tr); });
    }
    th.addEventListener("click", sort);
    th.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
    });
  });

  // The Light / Dark / System switch: the kit's theme.js, inlined so the page
  // also works opened straight from disk (file://), where modules do not load.
  var KEY = "ds-theme";
  var media = window.matchMedia("(prefers-color-scheme: light)");
  function getPreference() {
    try { var v = localStorage.getItem(KEY); if (v === "light" || v === "dark") return v; } catch (e) {}
    return "system";
  }
  function applyTheme(pref) {
    var theme = pref === "system" ? (media.matches ? "light" : "dark") : pref;
    var root = document.documentElement;
    if (root.getAttribute("data-theme") === theme) return;
    root.classList.add("ds-theme-switching");
    root.setAttribute("data-theme", theme);
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      root.classList.remove("ds-theme-switching"); }); });
  }
  var group = document.querySelector(".theme-toggle");
  var buttons = group ? group.querySelectorAll("button[data-pref]") : [];
  function sync() {
    Array.prototype.forEach.call(buttons, function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-pref") === getPreference()));
    });
  }
  Array.prototype.forEach.call(buttons, function (b) {
    b.addEventListener("click", function () {
      var pref = b.getAttribute("data-pref");
      try { pref === "system" ? localStorage.removeItem(KEY) : localStorage.setItem(KEY, pref); } catch (e) {}
      applyTheme(pref); sync();
    });
  });
  media.addEventListener("change", function () { if (getPreference() === "system") applyTheme("system"); });
  applyTheme(getPreference()); sync();
  if (group) group.hidden = false;

  apply();
})();
