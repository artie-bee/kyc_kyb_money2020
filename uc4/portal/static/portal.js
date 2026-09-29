/* The portal's only script: the theme switch, a sign of life while a file
   uploads, the demo selector's filter-as-you-type, and the new-message card
   and unread count kept up to date while a page is open. Like the console's app.js, it adds and never decides - every page is
   complete as the server sends it, and every upload is a plain form post, so
   with the script blocked nothing is lost but the two conveniences. */
(function () {
  "use strict";

  // ---- theme: the button stays hidden unless this script runs -------------
  var KEY = "wal-uc4-theme";
  try {
    var saved = localStorage.getItem(KEY);
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (e) { /* private window, blocked storage: the default theme is fine */ }

  var themeBtn = document.getElementById("theme");
  if (themeBtn) {
    themeBtn.hidden = false;
    themeBtn.addEventListener("click", function () {
      var root = document.documentElement;
      var now = root.getAttribute("data-theme");
      var dark = now === "dark" ||
        (now !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      var next = dark ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem(KEY, next); } catch (e) { /* nothing to do */ }
    });
  }

  // ---- drag and drop: an extra on a normal file input ---------------------
  // Without the script the input is an ordinary "choose a file" control and the
  // hint stays hidden, so the page never promises something it cannot do.
  Array.prototype.forEach.call(document.querySelectorAll(".dropzone"), function (zone) {
    var input = zone.querySelector("input[type=file]");
    var hint = zone.querySelector(".dropzone__hint");
    var text = zone.querySelector(".dropzone__text");
    if (!input) return;
    if (hint) hint.hidden = false;
    function show() {
      if (input.files && input.files.length && text) text.textContent = input.files[0].name;
    }
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (ev) {
        ev.preventDefault(); zone.classList.add("dropzone--over");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function () { zone.classList.remove("dropzone--over"); });
    });
    zone.addEventListener("drop", function (ev) {
      ev.preventDefault();
      if (ev.dataTransfer && ev.dataTransfer.files.length) {
        input.files = ev.dataTransfer.files;
        show();
      }
    });
  });

  // ---- new-message card: dismiss it, and pop one up when a message arrives --
  // The card and the unread count are in the page as the server sends it;
  // this only adds a dismiss button and checks for new messages while the
  // page is open, so a message sent from the console appears within seconds.
  var DISMISSED = "wal-uc4-toast-dismissed";
  function dismissed() {
    try { return sessionStorage.getItem(DISMISSED) || ""; } catch (e) { return ""; }
  }
  function wireToast(card) {
    if (card.getAttribute("data-toast") === dismissed()) { card.remove(); return; }
    var close = card.querySelector(".toast__close");
    if (!close) return;
    close.hidden = false;
    close.addEventListener("click", function () {
      try { sessionStorage.setItem(DISMISSED, card.getAttribute("data-toast")); } catch (e) {}
      card.remove();
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll(".toast"), wireToast);

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text) n.textContent = text;
    return n;
  }
  function showToast(newest, count) {
    var box = document.getElementById("toasts");
    if (!box || !newest || newest.id === dismissed()) return;
    var old = box.querySelector(".toast");
    if (old && old.getAttribute("data-toast") === newest.id) return;
    if (old) old.remove();
    var card = el("aside", "toast toast--arrived");
    card.setAttribute("role", "status");
    card.setAttribute("aria-live", "polite");
    card.setAttribute("data-toast", newest.id);
    var dot = el("span", "toast__dot");
    dot.setAttribute("aria-hidden", "true");
    var body = el("div", "toast__body");
    body.appendChild(el("strong", "toast__title", "New message from Wallester"
      + (count > 1 ? " and " + (count - 1) + " more" : "")));
    body.appendChild(el("span", "toast__when", newest.when));
    body.appendChild(el("p", "toast__text", newest.text));
    var go = el("a", "btn btn--primary toast__go", "Read message");
    go.href = "/messages";
    body.appendChild(go);
    var close = el("button", "toast__close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "Dismiss");
    card.appendChild(dot); card.appendChild(body); card.appendChild(close);
    box.appendChild(card);
    wireToast(card);
  }
  function setBadge(count) {
    Array.prototype.forEach.call(document.querySelectorAll(".pnav__badge"), function (b) {
      b.textContent = count;
      b.setAttribute("data-unread", count);
      b.setAttribute("aria-label", count + " unread");
      b.hidden = count === 0;
    });
  }
  var onMessages = location.pathname === "/messages";
  function check() {
    if (!window.fetch) return;
    fetch("/api/unread", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.signed_in) return;
        if (!onMessages) { setBadge(d.unread); if (d.unread) showToast(d.newest, d.unread); }
        else if (d.unread) { setBadge(d.unread); showToast(d.newest, d.unread); }
      })
      .catch(function () { /* offline for a moment: try again next time */ });
  }
  if (document.body.getAttribute("data-poll") === "1") setInterval(check, 15000);

  // ---- demo selector: filter the customers as you type -------------------
  // The search box is also a plain GET form, so without this it still works
  // on Search; this just saves the round trip.
  var q = document.getElementById("pick-q");
  if (q) {
    var groups = document.querySelectorAll("[data-group]");
    var empty = document.getElementById("pick-empty");
    q.addEventListener("input", function () {
      var term = q.value.trim().toLowerCase();
      var total = 0;
      Array.prototype.forEach.call(groups, function (g) {
        var n = 0;
        Array.prototype.forEach.call(g.querySelectorAll(".pick"), function (card) {
          var hit = !term || card.getAttribute("data-search").indexOf(term) !== -1;
          card.hidden = !hit;
          if (hit) n++;
        });
        g.hidden = n === 0;
        var count = g.querySelector("[data-count]");
        if (count) count.textContent = n;
        total += n;
      });
      if (empty) empty.hidden = total !== 0;
    });
  }

  // ---- upload: say it is happening, and stop a second click resending it ---
  Array.prototype.forEach.call(document.querySelectorAll("form.upload"), function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector("button[type=submit]");
      if (!btn) return;
      btn.disabled = true;
      btn.textContent = "Uploading…";
    });
  });
})();
