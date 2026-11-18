(function () {
  "use strict";
  function init() {
    var navigation = document.querySelector(".bd-docs-nav");
    if (!navigation) return;
    navigation.querySelectorAll("li.toctree-l1.has-children > details").forEach(function (sec) {
      sec.setAttribute("open", "");
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
