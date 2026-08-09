(() => {
  const uploaders = document.querySelectorAll("[data-uploader]");

  uploaders.forEach((section) => {
    const endpoint = section.dataset.endpoint;
    const dropZone = section.querySelector("[data-drop-zone]");
    const dropLabel = section.querySelector("[data-drop-label]");
    const fileInput = section.querySelector("[data-file-input]");
    const uploadBtn = section.querySelector("[data-upload-btn]");
    const btnLabel = section.querySelector("[data-btn-label]");
    const spinner = section.querySelector("[data-spinner]");
    const statusBanner = section.querySelector("[data-status]");

    let selectedFile = null;

    function showStatus(html, kind) {
      statusBanner.innerHTML = html;
      statusBanner.className = `status-banner visible ${kind}`;
    }

    function hideStatus() {
      statusBanner.className = "status-banner";
    }

    function setFile(file) {
      selectedFile = file;
      if (file) {
        dropLabel.textContent = file.name;
        dropZone.classList.add("has-file");
        uploadBtn.disabled = false;
      } else {
        dropLabel.textContent = "Click to choose a file, or drag one here";
        dropZone.classList.remove("has-file");
        uploadBtn.disabled = true;
      }
    }

    fileInput.addEventListener("change", () => {
      hideStatus();
      setFile(fileInput.files && fileInput.files[0] ? fileInput.files[0] : null);
    });

    dropZone.addEventListener("dragover", (event) => {
      event.preventDefault();
      dropZone.classList.add("has-file");
    });

    dropZone.addEventListener("dragleave", () => {
      if (!selectedFile) dropZone.classList.remove("has-file");
    });

    dropZone.addEventListener("drop", (event) => {
      event.preventDefault();
      const file = event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) {
        fileInput.files = event.dataTransfer.files;
        hideStatus();
        setFile(file);
      }
    });

    function setUploading(isUploading) {
      uploadBtn.disabled = isUploading || !selectedFile;
      spinner.classList.toggle("visible", isUploading);
      btnLabel.textContent = isUploading ? "Uploading..." : btnLabel.dataset.defaultLabel || btnLabel.textContent;
    }

    const defaultLabel = btnLabel.textContent;
    btnLabel.dataset.defaultLabel = defaultLabel;

    uploadBtn.addEventListener("click", async () => {
      if (!selectedFile) return;
      hideStatus();
      setUploading(true);

      const formData = new FormData();
      formData.append("file", selectedFile);

      try {
        const response = await fetch(endpoint, { method: "POST", body: formData });
        const body = await response.json().catch(() => null);

        if (!response.ok) {
          const detail = (body && body.detail) || "Upload failed.";
          throw new Error(detail);
        }

        showStatus(
          `<table class="results-table">` +
            `<tr><td>Rows parsed</td><td>${body.rows_parsed}</td></tr>` +
            `<tr><td>New rows added</td><td>${body.rows_inserted}</td></tr>` +
            `<tr><td>Duplicates skipped</td><td>${body.rows_skipped_duplicate}</td></tr>` +
            `</table>`,
          "success"
        );

        fileInput.value = "";
        setFile(null);
      } catch (err) {
        showStatus(err.message || "Upload failed.", "error");
      } finally {
        setUploading(false);
      }
    });
  });
})();
