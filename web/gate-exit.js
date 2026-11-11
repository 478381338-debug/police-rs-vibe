/* 访问口令退出入口：仅在服务端启用访问口令时显示，未启用时保持隐藏。 */
(function () {
  "use strict";

  function showLinks() {
    document.querySelectorAll("[data-gate-exit]").forEach(function (el) {
      el.style.display = "";
    });
  }

  function probe() {
    fetch("/__gate/status", { cache: "no-store", credentials: "same-origin" })
      .then(function (resp) { return resp.ok ? resp.json() : null; })
      .then(function (state) {
        if (state && state.enabled) showLinks();
      })
      .catch(function () { /* 状态探测失败时保持隐藏，不影响页面本身 */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", probe);
  } else {
    probe();
  }
})();