(function () {
  "use strict";
  function setMode(mode) {
    var root = document.documentElement;
    root.dataset.mode = mode;
    root.dataset.theme = mode;
    try {
      localStorage.setItem("mode", mode);
      localStorage.setItem("theme", mode);
    } catch (e) {}
  }
  function init() {
    var btn = document.querySelector(".conah-theme-toggle");
    if (!btn) return;
    btn.addEventListener("click", function () {
      var isDark = document.documentElement.dataset.theme === "dark";
      setMode(isDark ? "light" : "dark");
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
