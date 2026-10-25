/* Blue / White theme switch. Loaded in <head> so the saved theme applies before first paint. */
(function () {
  var KEY = "utho-theme";
  function get() {
    try { var v = localStorage.getItem(KEY); if (v === "white" || v === "blue" || v === "black") return v; } catch (e) {}
    return "blue";
  }
  function apply(t) {
    document.documentElement.setAttribute("data-theme", t);
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute("content", t === "white" ? "#ffffff" : (t === "black" ? "#000000" : "#0b2342"));
    var btns = document.querySelectorAll("[data-theme-set]");
    for (var i = 0; i < btns.length; i++) {
      btns[i].setAttribute("aria-pressed", String(btns[i].getAttribute("data-theme-set") === t));
    }
  }
  apply(get());
  document.addEventListener("DOMContentLoaded", function () { apply(get()); });
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-theme-set]");
    if (!b) return;
    var t = b.getAttribute("data-theme-set");
    try { localStorage.setItem(KEY, t); } catch (x) {}
    apply(t);
  });
})();
