(function () {
  document.querySelectorAll(".nav").forEach(function (nav) {
    var toggle = nav.querySelector(".nav-toggle");
    var links = nav.querySelector(".nav-links");
    if (!toggle || !links) {
      return;
    }

    toggle.addEventListener("click", function () {
      var expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
      links.classList.toggle("is-open", !expanded);
    });
  });
})();
