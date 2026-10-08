// Loaded synchronously in <head> so the theme is applied before first paint.
// The attribute lives on <html> so it exists before <body> is parsed.
(function () {
  var THEMES = ["green", "slate", "warm", "mono"];
  var t = "green";
  try {
    var saved = localStorage.getItem("theme");
    if (THEMES.indexOf(saved) !== -1) t = saved;
  } catch (e) { /* private mode or blocked storage */ }
  document.documentElement.setAttribute("data-theme", t);

  function syncThemeColor() {
    // Match the browser toolbar to the page background
    var bg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    if (!bg) return;
    document.querySelectorAll('meta[name="theme-color"]').forEach(function (m) {
      m.setAttribute("content", bg);
      m.removeAttribute("media");
    });
  }

  function markActive(theme) {
    document.querySelectorAll(".theme-btn").forEach(function (b) {
      var on = b.dataset.theme === theme;
      b.classList.toggle("active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    markActive(t);
    syncThemeColor();
    document.querySelectorAll(".theme-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var theme = btn.dataset.theme;
        document.documentElement.setAttribute("data-theme", theme);
        try { localStorage.setItem("theme", theme); } catch (e) {}
        markActive(theme);
        syncThemeColor();
        document.dispatchEvent(new Event("themechange"));
      });
    });
  });

  // iOS switches light/dark automatically at sunset; follow it live
  var mq = window.matchMedia("(prefers-color-scheme: dark)");
  var onScheme = function () {
    syncThemeColor();
    document.dispatchEvent(new Event("themechange"));
  };
  if (mq.addEventListener) mq.addEventListener("change", onScheme);
  else if (mq.addListener) mq.addListener(onScheme);
})();
