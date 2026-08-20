(function () {
  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderInlineMarkdown(value) {
    return escapeHtml(value)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }

  function renderMarkdown(value) {
    const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let listOpen = false;
    let paragraph = [];

    function closeList() {
      if (listOpen) {
        html.push("</ul>");
        listOpen = false;
      }
    }

    function flushParagraph() {
      if (paragraph.length) {
        html.push("<p>" + renderInlineMarkdown(paragraph.join(" ")) + "</p>");
        paragraph = [];
      }
    }

    for (const rawLine of lines) {
      const line = rawLine.trim();
      if (!line) {
        flushParagraph();
        closeList();
        continue;
      }

      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        closeList();
        const level = heading[1].length;
        html.push("<h" + level + ">" + renderInlineMarkdown(heading[2]) + "</h" + level + ">");
        continue;
      }

      const item = line.match(/^[-*]\s+(.+)$/);
      if (item) {
        flushParagraph();
        if (!listOpen) {
          html.push("<ul>");
          listOpen = true;
        }
        html.push("<li>" + renderInlineMarkdown(item[1]) + "</li>");
        continue;
      }

      paragraph.push(line);
    }

    flushParagraph();
    closeList();
    return html.join("\n");
  }

  async function readError(response) {
    try {
      const data = await response.json();
      return data.detail || JSON.stringify(data);
    } catch {
      return await response.text();
    }
  }

  function initThemeToggle() {
    const toggle = document.getElementById("dark-mode-toggle");
    if (!toggle) return;

    const root = document.documentElement;
    const saved = localStorage.getItem("dark-mode");
    if (saved === "true" || (!saved && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
      root.classList.add("dark");
      toggle.textContent = "☀️";
    }

    toggle.addEventListener("click", () => {
      root.classList.toggle("dark");
      const isDark = root.classList.contains("dark");
      localStorage.setItem("dark-mode", isDark);
      toggle.textContent = isDark ? "☀️" : "🌙";
    });
  }

  window.MioSite = {
    escapeHtml,
    renderMarkdown,
    readError,
    initThemeToggle,
  };

  initThemeToggle();
})();
