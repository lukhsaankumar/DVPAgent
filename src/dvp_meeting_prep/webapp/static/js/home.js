(() => {
  const input = document.getElementById("advisor-input");
  const list = document.getElementById("advisor-list");
  const selectedLabel = document.getElementById("selected-advisor");
  const generateBtn = document.getElementById("generate-btn");
  const generateBtnLabel = document.getElementById("generate-btn-label");
  const generateSpinner = document.getElementById("generate-spinner");
  const statusBanner = document.getElementById("status-banner");

  let selectedAdvisor = null;
  let activeIndex = -1;
  let currentMatches = [];
  let debounceHandle = null;
  let requestToken = 0;

  function clearSelection() {
    selectedAdvisor = null;
    selectedLabel.textContent = "";
    generateBtn.disabled = true;
  }

  function setSelection(name) {
    selectedAdvisor = name;
    input.value = name;
    selectedLabel.innerHTML = `Selected: <strong>${escapeHtml(name)}</strong>`;
    generateBtn.disabled = false;
    closeList();
  }

  function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value;
    return div.innerHTML;
  }

  function closeList() {
    list.classList.remove("open");
    input.setAttribute("aria-expanded", "false");
    activeIndex = -1;
  }

  function openList() {
    list.classList.add("open");
    input.setAttribute("aria-expanded", "true");
  }

  function renderMatches(matches) {
    currentMatches = matches;
    activeIndex = -1;
    list.innerHTML = "";

    if (matches.length === 0) {
      const li = document.createElement("li");
      li.className = "empty-state";
      li.textContent = input.value.trim() ? "No matching advisors" : "Start typing to search";
      list.appendChild(li);
      openList();
      return;
    }

    matches.forEach((name, index) => {
      const li = document.createElement("li");
      li.textContent = name;
      li.id = `advisor-option-${index}`;
      li.setAttribute("role", "option");
      li.addEventListener("mousedown", (event) => {
        event.preventDefault();
        setSelection(name);
      });
      list.appendChild(li);
    });
    openList();
  }

  function updateActiveDescendant() {
    Array.from(list.children).forEach((child, index) => {
      child.classList.toggle("active", index === activeIndex);
    });
    if (activeIndex >= 0) {
      input.setAttribute("aria-activedescendant", `advisor-option-${activeIndex}`);
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  async function fetchMatches(query) {
    const token = ++requestToken;
    try {
      const response = await fetch(`/api/advisors?q=${encodeURIComponent(query)}&limit=15`);
      if (!response.ok) throw new Error("search failed");
      const data = await response.json();
      if (token !== requestToken) return; // a newer request superseded this one
      renderMatches(data.advisors || []);
    } catch (err) {
      if (token !== requestToken) return;
      renderMatches([]);
    }
  }

  input.addEventListener("input", () => {
    clearSelection();
    const query = input.value.trim();
    window.clearTimeout(debounceHandle);
    if (!query) {
      closeList();
      return;
    }
    debounceHandle = window.setTimeout(() => fetchMatches(query), 200);
  });

  input.addEventListener("focus", () => {
    if (input.value.trim() && currentMatches.length) openList();
  });

  input.addEventListener("keydown", (event) => {
    if (!list.classList.contains("open") || currentMatches.length === 0) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeIndex = Math.min(activeIndex + 1, currentMatches.length - 1);
      updateActiveDescendant();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      updateActiveDescendant();
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (activeIndex >= 0 && currentMatches[activeIndex]) {
        setSelection(currentMatches[activeIndex]);
      } else if (currentMatches.length === 1) {
        setSelection(currentMatches[0]);
      }
    } else if (event.key === "Escape") {
      closeList();
    }
  });

  document.addEventListener("click", (event) => {
    if (!event.target.closest(".combobox")) closeList();
  });

  function showStatus(message, kind) {
    statusBanner.textContent = message;
    statusBanner.className = `status-banner visible ${kind}`;
  }

  function hideStatus() {
    statusBanner.className = "status-banner";
  }

  function setGenerating(isGenerating) {
    generateBtn.disabled = isGenerating || !selectedAdvisor;
    generateSpinner.classList.toggle("visible", isGenerating);
    generateBtnLabel.textContent = isGenerating ? "Generating..." : "Get meeting prep document";
  }

  function filenameFromContentDisposition(headerValue) {
    if (!headerValue) return "meeting_prep.docx";
    const match = /filename="?([^";]+)"?/.exec(headerValue);
    return match ? match[1] : "meeting_prep.docx";
  }

  generateBtn.addEventListener("click", async () => {
    if (!selectedAdvisor) return;
    hideStatus();
    setGenerating(true);

    try {
      const response = await fetch("/api/meeting-prep", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ advisor_name: selectedAdvisor }),
      });

      if (!response.ok) {
        let detail = "Meeting prep generation failed.";
        try {
          const errorBody = await response.json();
          if (errorBody.detail) detail = errorBody.detail;
        } catch (_) {
          // ignore parse failure, use default message
        }
        throw new Error(detail);
      }

      const blob = await response.blob();
      const fileName = filenameFromContentDisposition(response.headers.get("Content-Disposition"));
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);

      showStatus(`Meeting prep document ready: ${fileName}`, "success");
    } catch (err) {
      showStatus(err.message || "Something went wrong generating the document.", "error");
    } finally {
      setGenerating(false);
    }
  });
})();
