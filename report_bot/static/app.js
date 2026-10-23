"use strict";

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const defaultReviewMode = document.querySelector('meta[name="default-review-mode"]').content;
const maxBatchFiles = Number(document.querySelector('meta[name="max-batch-files"]').content);

const state = { job: null, selectedItemId: null, pollTimer: null };
let selectedFilesList = [];
const uploadForm = document.getElementById("upload-form");
const fileInput = document.getElementById("pdf-files");
const dropZone = document.getElementById("drop-zone");
const selectedFiles = document.getElementById("selected-files");
const uploadPanel = document.getElementById("upload-panel");
const jobPanel = document.getElementById("job-panel");
const fileRail = document.getElementById("file-rail");
const reviewDetail = document.getElementById("review-detail");
const processButton = document.getElementById("process-button");
const downloadButton = document.getElementById("download-button");
const globalAlert = document.getElementById("global-alert");
const settingsDialog = document.getElementById("settings-dialog");

document.querySelector(`input[name="review_mode"][value="${defaultReviewMode}"]`).checked = true;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showAlert(message, kind = "danger") {
  globalAlert.textContent = message;
  globalAlert.className = `alert alert-${kind}`;
}

function clearAlert() {
  globalAlert.textContent = "";
  globalAlert.className = "alert is-hidden";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== "GET") headers.set("X-CSRF-Token", csrfToken);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Your session has expired.");
  }
  if (!response.ok) {
    let message = "The request could not be completed.";
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
    } catch (_error) {
      // Keep the privacy-safe fallback message.
    }
    throw new Error(message);
  }
  return response;
}

function displaySelectedFiles() {
  if (!selectedFilesList.length) {
    selectedFiles.textContent = "No files selected.";
    return;
  }
  const batchDefault = document.querySelector('input[name="default_case_type"]:checked').value;
  selectedFiles.innerHTML = selectedFilesList.map((file, index) => `
    <div class="selected-file-row">
      <span>${escapeHtml(file.name)}</span>
      <select name="case_types" aria-label="Report type for ${escapeHtml(file.name)}">
        <option value="PERSONAL"${batchDefault === "PERSONAL" ? " selected" : ""}>PERSONAL</option>
        <option value="CERTIFIED_LEGAL"${batchDefault === "CERTIFIED_LEGAL" ? " selected" : ""}>CERTIFIED / LEGAL</option>
      </select>
      <button type="button" class="file-remove-button" data-index="${index}" title="Remove file" aria-label="Remove ${escapeHtml(file.name)}">×</button>
    </div>`).join("");
}

function handleNewFiles(incomingFiles) {
  const currentNames = new Set(selectedFilesList.map((f) => f.name));
  for (const file of Array.from(incomingFiles || [])) {
    if (!currentNames.has(file.name) && selectedFilesList.length < maxBatchFiles) {
      selectedFilesList.push(file);
      currentNames.add(file.name);
    }
  }
  fileInput.value = "";
  displaySelectedFiles();
}

function stateLabel(value) {
  return String(value).replaceAll("_", " ");
}

function statusClass(value) {
  if (["COMPLETE"].includes(value)) return "status-success";
  if (["FAILED"].includes(value)) return "status-danger";
  if (["NEEDS_REVIEW", "PARTIAL"].includes(value)) return "status-warning";
  return "status-neutral";
}

function setWorkflowStep(step) {
  const order = ["upload", "review", "process", "download"];
  const activeIndex = order.indexOf(step);
  document.querySelectorAll(".stepper li").forEach((node, index) => {
    node.classList.toggle("is-current", index === activeIndex);
    node.classList.toggle("is-complete", index < activeIndex);
  });
}

function reportWarnings(item) {
  if (!item.report) return "";
  const warnings = item.report.warnings || [];
  const errors = item.report.blocking_errors || [];
  return [
    ...errors.map((message) => `<div class="alert alert-danger" role="alert">${escapeHtml(message)}</div>`),
    ...warnings.map((message) => `<div class="alert alert-warning">${escapeHtml(message)}</div>`),
  ].join("");
}

function typeOptions(current) {
  const options = [
    "PATERNITY",
    "SINGLE PROFILE",
    "PRENATAL PATERNITY",
    "PRENATAL GENDER",
    "GENDER",
    "MATERNITY",
    "AVUNCULAR",
    "GRANDPARENTAGE",
    "SIBLINGSHIP",
    "Y-CHROMOSOME",
    "MITOCHONDRIAL",
    "ZYGOSITY",
  ];
  if (!options.includes(current)) options.push(current);
  return options.map((value) => `<option value="${escapeHtml(value)}"${value === current ? " selected" : ""}>${escapeHtml(value)}</option>`).join("");
}

function renderDetail(item) {
  if (!item) {
    reviewDetail.innerHTML = '<div class="empty-state"><strong>Select a report</strong><span>Extracted details will appear here.</span></div>';
    return;
  }
  if (!item.report) {
    reviewDetail.innerHTML = `<div class="parse-failure"><h3>Report quarantined</h3><p>${escapeHtml(item.parse_error || "The report could not be parsed.")}</p></div>`;
    return;
  }

  const report = item.report;
  const locked = ["PROCESSING", "COMPLETE"].includes(item.state);
  const participantRows = report.participants.map((participant, index) => `
    <tr data-participant-id="${escapeHtml(participant.id)}">
      <td><input name="source_order" class="order-input" type="number" min="0" value="${Number(participant.source_order)}" aria-label="Order for participant ${index + 1}" ${locked ? "disabled" : ""}></td>
      <td><input name="sample_number" value="${escapeHtml(participant.sample_number)}" aria-label="Sample number for participant ${index + 1}" ${locked ? "disabled" : ""}></td>
      <td><input name="role" value="${escapeHtml(participant.role)}" aria-label="Role for participant ${index + 1}" ${locked ? "disabled" : ""}></td>
      <td><input name="full_name" value="${escapeHtml(participant.full_name)}" aria-label="Full name for participant ${index + 1}" ${locked ? "disabled" : ""}></td>
      <td><input name="date_of_birth" value="${escapeHtml(participant.date_of_birth)}" aria-label="Date of birth for participant ${index + 1}" ${locked ? "disabled" : ""}></td>
    </tr>`).join("");

  reviewDetail.innerHTML = `
    <form id="item-form">
      <div class="detail-heading">
        <div><h3>${escapeHtml(item.original_filename)}</h3><p>Dynamic values are stored and generated in uppercase.</p></div>
        <span class="status-chip ${statusClass(item.state)}">${escapeHtml(stateLabel(item.state))}</span>
      </div>
      ${reportWarnings(item)}
      ${item.processing_error ? `<div class="alert alert-danger" role="alert">${escapeHtml(item.processing_error)}</div>` : ""}
      ${item.case_type === "ISSUE" ? `<div id="issue-case-type-banner" class="alert alert-danger" role="alert" style="display: flex; align-items: center; gap: 8px;"><i class="fa-solid fa-triangle-exclamation"></i> <span><strong>Action Required:</strong> Report type is set to <strong>Issue</strong> (unrecognized case code prefix and not found in Notion). Please change the Report Type below to <strong>Personal</strong> or <strong>Certified / Legal</strong> before approving.</span></div>` : ""}
      <div class="detail-grid">
        <label class="field">Case reference<input name="case_code" value="${escapeHtml(report.case_code)}" required ${locked ? "disabled" : ""}></label>
        <label class="field">Report type
          <select name="case_type" id="item-case-type-select" ${locked ? "disabled" : ""}>
            ${item.case_type === "ISSUE" ? '<option value="ISSUE" selected>⚠️ ISSUE (Select Report Type)</option>' : ''}
            <option value="PERSONAL"${item.case_type === "PERSONAL" ? " selected" : ""}>PERSONAL</option>
            <option value="CERTIFIED_LEGAL"${item.case_type === "CERTIFIED_LEGAL" ? " selected" : ""}>CERTIFIED / LEGAL</option>
          </select>
        </label>
        <label class="field">Test type<select name="test_type" ${locked ? "disabled" : ""}>${typeOptions(report.test_type)}</select></label>
        <label class="field">Collection date<input name="collection_date" type="date" value="${escapeHtml(report.collection_date)}" required ${locked ? "disabled" : ""}></label>
        <label class="field">Laboratory report date<input name="report_date" type="date" value="${escapeHtml(report.report_date || "")}" ${locked ? "disabled" : ""}></label>
      </div>
      <div class="participants-heading"><h3>Participants</h3><span>PRIMARY ADULT → PARENT → RELATIVES → CHILDREN</span></div>
      <div class="table-scroll">
        <table class="participant-table">
          <thead><tr><th>ORDER</th><th>SAMPLE</th><th>ROLE</th><th>FULL NAME</th><th>DOB</th></tr></thead>
          <tbody>${participantRows}</tbody>
        </table>
      </div>
      ${locked ? "" : `<div class="detail-actions"><button class="button button-secondary" type="submit">Save changes</button><button id="approve-button" class="button button-primary ${item.case_type === 'ISSUE' ? 'btn-locked-issue' : ''}" type="button" ${item.case_type === 'ISSUE' ? 'style="opacity: 0.65; cursor: not-allowed;" title="Cannot approve: Report type is set to Issue"' : ''}>Save &amp; approve</button></div>`}
    </form>`;

  if (!locked) {
    document.getElementById("item-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await saveItem(item.id, false);
    });
    document.getElementById("approve-button").addEventListener("click", async () => {
      const caseTypeSelect = document.getElementById("item-case-type-select");
      if (caseTypeSelect && caseTypeSelect.value === "ISSUE") {
        showAlert("Cannot approve: Report type is set to Issue. Please select Personal or Certified / Legal first.");
        caseTypeSelect.focus();
        caseTypeSelect.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
      }
      await saveItem(item.id, true);
    });

    const caseTypeSelect = document.getElementById("item-case-type-select");
    if (caseTypeSelect) {
      caseTypeSelect.addEventListener("change", () => {
        const approveBtn = document.getElementById("approve-button");
        const banner = document.getElementById("issue-case-type-banner");
        if (caseTypeSelect.value !== "ISSUE") {
          if (approveBtn) {
            approveBtn.classList.remove("btn-locked-issue");
            approveBtn.style.opacity = "1";
            approveBtn.style.cursor = "pointer";
            approveBtn.removeAttribute("title");
          }
          if (banner) {
            banner.style.display = "none";
          }
        } else {
          if (approveBtn) {
            approveBtn.classList.add("btn-locked-issue");
            approveBtn.style.opacity = "0.65";
            approveBtn.style.cursor = "not-allowed";
            approveBtn.title = "Cannot approve: Report type is set to Issue";
          }
          if (banner) {
            banner.style.display = "flex";
          }
        }
      });
    }
  }
}

function renderJob() {
  const job = state.job;
  if (!job) return;
  uploadPanel.classList.add("is-hidden");
  jobPanel.classList.remove("is-hidden");
  const jobState = document.getElementById("job-state");
  jobState.textContent = stateLabel(job.state);
  jobState.className = `status-chip ${statusClass(job.state)}`;

  fileRail.innerHTML = job.items.map((item) => `
    <button class="file-row${item.id === state.selectedItemId ? " is-active" : ""}" type="button" data-item-id="${escapeHtml(item.id)}">
      <strong>${escapeHtml(item.original_filename)}</strong>
      <small>${escapeHtml(item.report?.case_code || "NOT EXTRACTED")}</small>
      <span class="status-chip ${statusClass(item.state)}">${escapeHtml(stateLabel(item.state))}</span>
    </button>`).join("");
  fileRail.querySelectorAll("[data-item-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedItemId = button.dataset.itemId;
      renderJob();
    });
  });

  if (!state.selectedItemId || !job.items.some((item) => item.id === state.selectedItemId)) {
    state.selectedItemId = job.items[0]?.id || null;
  }
  renderDetail(job.items.find((item) => item.id === state.selectedItemId));

  const approved = job.items.filter((item) => item.approved).length;
  processButton.disabled = approved === 0 || ["PROCESSING", "COMPLETE"].includes(job.state);
  const processing = job.state === "PROCESSING";
  document.getElementById("processing-summary").classList.toggle("is-hidden", !processing);
  document.getElementById("progress-value").textContent = `${job.progress}%`;
  document.getElementById("progress-bar").style.width = `${job.progress}%`;
  document.getElementById("batch-expiry").textContent = `Private files expire ${new Date(job.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.`;

  downloadButton.classList.toggle("is-hidden", !job.download_ready);
  if (job.download_ready) downloadButton.href = `/api/jobs/${job.id}/download`;

  if (job.download_ready) setWorkflowStep("download");
  else if (processing) setWorkflowStep("process");
  else setWorkflowStep("review");
}

function itemPayload() {
  const form = document.getElementById("item-form");
  const data = new FormData(form);
  const participants = Array.from(form.querySelectorAll("tbody tr")).map((row) => ({
    id: row.dataset.participantId,
    source_order: Number(row.querySelector('[name="source_order"]').value),
    sample_number: row.querySelector('[name="sample_number"]').value,
    role: row.querySelector('[name="role"]').value,
    full_name: row.querySelector('[name="full_name"]').value,
    date_of_birth: row.querySelector('[name="date_of_birth"]').value,
  }));
  return {
    case_type: data.get("case_type"),
    case_code: data.get("case_code"),
    test_type: data.get("test_type"),
    collection_date: data.get("collection_date"),
    report_date: data.get("report_date") || null,
    participants,
  };
}

async function saveItem(itemId, approve) {
  clearAlert();
  try {
    await api(`/api/jobs/${state.job.id}/items/${itemId}`, {
      method: "PATCH",
      body: JSON.stringify(itemPayload()),
    });
    if (approve) {
      await api(`/api/jobs/${state.job.id}/items/${itemId}/approve`, { method: "POST" });
      showAlert("Report approved and ready to process.", "success");
    } else {
      showAlert("Corrections saved. Confirm the report when it is ready.", "success");
    }
    await refreshJob();
  } catch (error) {
    showAlert(error.message);
    await refreshJob();
  }
}

async function refreshJob() {
  if (!state.job) return;
  const response = await api(`/api/jobs/${state.job.id}`);
  state.job = await response.json();
  renderJob();
}

function startPolling() {
  window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(async () => {
    try {
      await refreshJob();
      if (!["READY", "PROCESSING"].includes(state.job.state)) window.clearInterval(state.pollTimer);
    } catch (error) {
      window.clearInterval(state.pollTimer);
      showAlert(error.message);
    }
  }, 900);
}

fileInput.addEventListener("change", () => handleNewFiles(fileInput.files));
selectedFiles.addEventListener("click", (event) => {
  const removeBtn = event.target.closest(".file-remove-button");
  if (!removeBtn) return;
  const index = Number(removeBtn.dataset.index);
  if (!Number.isNaN(index) && index >= 0 && index < selectedFilesList.length) {
    selectedFilesList.splice(index, 1);
    displaySelectedFiles();
  }
});

document.querySelectorAll('input[name="default_case_type"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    selectedFiles.querySelectorAll('select[name="case_types"]').forEach((select) => {
      select.value = radio.value;
    });
  });
});
dropZone.addEventListener("dragover", (event) => { event.preventDefault(); dropZone.classList.add("is-dragging"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("is-dragging"));
dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("is-dragging");
  handleNewFiles(event.dataTransfer.files);
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearAlert();
  if (!selectedFilesList.length || selectedFilesList.length > maxBatchFiles) {
    showAlert(`Choose between 1 and ${maxBatchFiles} PDF files.`);
    return;
  }
  const button = document.getElementById("upload-button");
  button.disabled = true;
  button.textContent = "Extracting…";

  const labSourceRadio = document.querySelector('input[name="lab_source"]:checked');
  const reviewModeRadio = document.querySelector('input[name="review_mode"]:checked');
  const defaultCaseTypeRadio = document.querySelector('input[name="default_case_type"]:checked');

  const formData = new FormData();
  formData.append("lab_source", labSourceRadio ? labSourceRadio.value : "EUROFINS");
  formData.append("review_mode", reviewModeRadio ? reviewModeRadio.value : "REVIEW");
  formData.append("default_case_type", defaultCaseTypeRadio ? defaultCaseTypeRadio.value : "ISSUE");

  for (const file of selectedFilesList) {
    formData.append("files", file);
  }

  const caseTypeSelects = selectedFiles.querySelectorAll('select[name="case_types"]');
  caseTypeSelects.forEach((select) => {
    formData.append("case_types", select.value);
  });

  try {
    const response = await api("/api/jobs", { method: "POST", body: formData });
    state.job = await response.json();
    state.selectedItemId = state.job.items[0]?.id || null;
    renderJob();
    if (["READY", "PROCESSING"].includes(state.job.state)) startPolling();
  } catch (error) {
    showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Extract reports";
  }
});

processButton.addEventListener("click", async () => {
  clearAlert();
  processButton.disabled = true;
  try {
    const response = await api(`/api/jobs/${state.job.id}/process`, { method: "POST" });
    state.job = await response.json();
    renderJob();
    startPolling();
  } catch (error) {
    showAlert(error.message);
    processButton.disabled = false;
  }
});

document.getElementById("new-batch-button").addEventListener("click", () => {
  window.clearInterval(state.pollTimer);
  state.job = null;
  state.selectedItemId = null;
  selectedFilesList = [];
  uploadForm.reset();
  document.querySelector(`input[name="review_mode"][value="${defaultReviewMode}"]`).checked = true;
  selectedFiles.textContent = "";
  jobPanel.classList.add("is-hidden");
  uploadPanel.classList.remove("is-hidden");
  setWorkflowStep("upload");
  clearAlert();
});

document.querySelector("[data-open-settings]").addEventListener("click", async () => {
  try {
    const response = await api("/api/settings");
    const preferences = await response.json();
    settingsDialog.querySelector(`input[value="${preferences.default_review_mode}"]`).checked = true;
    settingsDialog.showModal();
  } catch (error) {
    showAlert(error.message);
  }
});

document.getElementById("save-settings").addEventListener("click", async () => {
  const selected = settingsDialog.querySelector('input[name="settings_review_mode"]:checked');
  if (!selected) return;
  try {
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ default_review_mode: selected.value }),
    });
    document.querySelector(`input[name="review_mode"][value="${selected.value}"]`).checked = true;
    settingsDialog.close();
    showAlert("Team default updated.", "success");
  } catch (error) {
    showAlert(error.message);
  }
});

document.querySelector("[data-logout]").addEventListener("click", async () => {
  try {
    await api("/logout", { method: "POST" });
  } finally {
    window.location.assign("/login");
  }
});
