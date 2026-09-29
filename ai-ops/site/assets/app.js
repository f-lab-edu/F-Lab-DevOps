    "use strict";
    const buttons = [...document.querySelectorAll("[data-detail]")];
    const titleEl = document.getElementById("detail-title");
    const statusEl = document.getElementById("detail-status");
    const summaryEl = document.getElementById("detail-summary");
    const pointsEl = document.getElementById("detail-points");
    const limitEl = document.getElementById("detail-limit");
    const relatedEl = document.getElementById("detail-related");
    const relatedTitleEl = document.getElementById("related-title");
    function select(key, clicked) {
      const item = details[key] || details.overview;
      titleEl.textContent = item.title;
      statusEl.className = "status " + item.status;
      statusEl.textContent = { a0: "A0 문서", a1: "A1 구현", a2: "A2 구현", future: "후속 미구현" }[item.status];
      summaryEl.textContent = item.summary;
      pointsEl.replaceChildren(...item.points.map(point => { const li = document.createElement("li"); li.textContent = point; return li; }));
      limitEl.textContent = item.limit;
      relatedEl.replaceChildren(...item.files.map(({ label, href }) => { const a = document.createElement("a"); a.href = href; a.textContent = label; return a; }));
      relatedTitleEl.hidden = item.files.length === 0;
      buttons.forEach(button => { button.classList.toggle("active", button === clicked); button.setAttribute("aria-pressed", button === clicked ? "true" : "false"); });
      if (clicked) {
        history.replaceState(null, "", "#" + encodeURIComponent(key));
        if (window.matchMedia("(max-width: 760px)").matches) {
          document.getElementById("detail").scrollIntoView({ behavior: "smooth", block: "start" });
        }
      }
    }
    buttons.forEach(button => button.addEventListener("click", () => select(button.dataset.detail, button)));
    const initial = decodeURIComponent(location.hash.slice(1));
    const defaultDetail = document.body.dataset.defaultDetail || "overview";
    select(initial && details[initial] ? initial : defaultDetail, null);
