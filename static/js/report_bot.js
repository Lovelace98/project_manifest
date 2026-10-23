(() => {
"use strict";

const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const defaultReviewMode = document.querySelector('meta[name="default-review-mode"]')?.content || "REVIEW";
const maxBatchFiles = Number(document.querySelector('meta[name="max-batch-files"]')?.content || 25);

const state = { job: null, selectedItemId: null, pollTimer: null, syncedNotionJobs: new Set() };
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
const syncNotionButton = document.getElementById("sync-notion-button");
const uploadNotionButton = document.getElementById("upload-notion-button");
const notionSyncBanner = document.getElementById("notion-sync-banner");
const autoSyncNotionCheckbox = document.getElementById("auto-sync-notion");
const autoUploadNotionCheckbox = document.getElementById("auto-upload-notion");
const globalAlert = document.getElementById("global-alert");
const settingsDialog = document.getElementById("settings-dialog");
const summaryPanel = document.getElementById("summary-panel");
const summaryTableBody = document.getElementById("summary-table-body");
const summaryTotalCount = document.getElementById("summary-total-count");
const summaryCleanCount = document.getElementById("summary-clean-count");
const summaryFlaggedCount = document.getElementById("summary-flagged-count");
const summaryNotionCount = document.getElementById("summary-notion-count");
const summaryBadge = document.getElementById("summary-badge");
const summaryDownloadBtn = document.getElementById("summary-download-btn");
const summarySyncNotionBtn = document.getElementById("summary-sync-notion-btn");
const summaryUploadNotionBtn = document.getElementById("summary-upload-notion-btn");
const summaryBackReviewBtn = document.getElementById("summary-back-review-btn");
const summaryNewBatchBtn = document.getElementById("summary-new-batch-btn");
const viewSummaryButton = document.getElementById("view-summary-button");
const loadingOverlay = document.getElementById("loading-overlay");
const extractionStepper = document.getElementById("extraction-stepper");
let extractionInterval = null;

const initialModeRadio = document.querySelector(`input[name="review_mode"][value="${defaultReviewMode}"]`);
if (initialModeRadio) initialModeRadio.checked = true;

function showExtractionLoader() {
  if (!loadingOverlay) return;
  const loaderTitle = document.getElementById("loader-title");
  const loaderDesc = document.getElementById("loader-desc");
  if (loaderTitle) loaderTitle.textContent = "Analyzing Report Batch...";
  if (loaderDesc) loaderDesc.textContent = "Auditing PDF layouts, verifying Notion database records, and auto-classifying report types.";

  if (extractionStepper) {
    extractionStepper.style.display = "flex";
    const steps = [
      document.getElementById("step-extract"),
      document.getElementById("step-layout"),
      document.getElementById("step-notion"),
      document.getElementById("step-classify"),
    ].filter(Boolean);

    steps.forEach((step, idx) => {
      step.className = `extract-step ${idx === 0 ? "is-active" : "is-pending"}`;
      const bullet = step.querySelector(".step-bullet");
      if (bullet) {
        bullet.innerHTML = idx === 0 ? '<i class="fa-solid fa-circle-notch fa-spin"></i>' : '<i class="fa-regular fa-circle"></i>';
      }
    });

    let currentStep = 0;
    window.clearInterval(extractionInterval);
    extractionInterval = window.setInterval(() => {
      if (currentStep < steps.length - 1) {
        steps[currentStep].className = "extract-step is-complete is-done";
        const bulletPrev = steps[currentStep].querySelector(".step-bullet");
        if (bulletPrev) bulletPrev.innerHTML = '<i class="fa-solid fa-check"></i>';

        currentStep++;
        steps[currentStep].className = "extract-step is-active";
        const bulletNext = steps[currentStep].querySelector(".step-bullet");
        if (bulletNext) bulletNext.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
      } else {
        if (loaderDesc) loaderDesc.textContent = "Finalizing Notion cross-checks and preparing review workspace...";
      }
    }, 550);
  }
  loadingOverlay.style.display = "flex";
}

async function finishExtractionLoader() {
  window.clearInterval(extractionInterval);
  const steps = [
    document.getElementById("step-extract"),
    document.getElementById("step-layout"),
    document.getElementById("step-notion"),
    document.getElementById("step-classify"),
  ].filter(Boolean);

  steps.forEach((step) => {
    step.className = "extract-step is-complete is-done";
    const bullet = step.querySelector(".step-bullet");
    if (bullet) bullet.innerHTML = '<i class="fa-solid fa-check"></i>';
  });

  const loaderTitle = document.getElementById("loader-title");
  const loaderDesc = document.getElementById("loader-desc");
  if (loaderTitle) loaderTitle.innerHTML = '<i class="fa-solid fa-circle-check" style="color: #10B981; margin-right: 6px;"></i> Batch Analysis Complete!';
  if (loaderDesc) loaderDesc.textContent = "All layout checks and Notion records verified. Loading workspace...";

  await new Promise((resolve) => setTimeout(resolve, 450));

  if (loadingOverlay) loadingOverlay.style.display = "none";
  if (extractionStepper) extractionStepper.style.display = "none";
}

function hideExtractionLoader() {
  window.clearInterval(extractionInterval);
  if (loadingOverlay) loadingOverlay.style.display = "none";
  if (extractionStepper) extractionStepper.style.display = "none";
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showAlert(message, kind = "danger") {
  if (globalAlert) {
    globalAlert.textContent = message;
    globalAlert.className = `alert alert-${kind}`;
  }
  if (typeof window.showToast === "function") {
    window.showToast(message, kind === "danger" ? "error" : kind === "success" ? "success" : "info");
  }
}

function clearAlert() {
  if (globalAlert) {
    globalAlert.textContent = "";
    globalAlert.className = "alert is-hidden";
  }
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
  selectedFiles.innerHTML = selectedFilesList.map((file, index) => `
    <div class="selected-file-row">
      <div class="selected-file-meta">
        <i class="fa-regular fa-file-pdf" style="color: #0284c7; margin-right: 8px;"></i>
        <strong>${escapeHtml(file.name)}</strong>
        <span class="file-tag-badge" style="margin-left: 8px; font-size: 0.72rem; padding: 2px 8px; background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; border-radius: 999px;">
          <i class="fa-solid fa-wand-magic-sparkles"></i> Auto-Detect Type
        </span>
      </div>
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

function railTagsHtml(item) {
  const tags = [];
  let typeBadgeClass = "badge-pill-neutral";
  let typeBadgeText = "Personal";
  if (item.case_type === "ISSUE") {
    typeBadgeClass = "badge-pill-danger";
    typeBadgeText = "⚠️ Issue";
  } else if (item.case_type === "CERTIFIED_LEGAL") {
    typeBadgeClass = "badge-pill-info";
    typeBadgeText = "Certified";
  }
  tags.push(`<span class="rail-mini-badge ${typeBadgeClass}">${typeBadgeText}</span>`);

  if (item.layout_warnings && item.layout_warnings.length > 0) {
    tags.push(`<span class="rail-mini-badge badge-pill-warning" title="${escapeHtml(item.layout_warnings.join('; '))}"><i class="fa-solid fa-triangle-exclamation"></i> Layout Alert</span>`);
  } else if (item.report) {
    tags.push(`<span class="rail-mini-badge badge-pill-success"><i class="fa-solid fa-check"></i> Layout OK</span>`);
  }

  if (item.needs_manual_review) {
    tags.push(`<span class="rail-mini-badge badge-pill-warning"><i class="fa-solid fa-circle-exclamation"></i> Review Needed</span>`);
  } else if (item.notion_verification?.has_notion_records && item.notion_verification?.verified) {
    tags.push(`<span class="rail-mini-badge badge-pill-success"><i class="fa-solid fa-cloud-check"></i> Notion Verified</span>`);
  } else if (item.classification_info?.verified) {
    tags.push(`<span class="rail-mini-badge badge-pill-success"><i class="fa-solid fa-check-double"></i> Confirmed</span>`);
  }

  return `<div class="rail-tags-row">${tags.join("")}</div>`;
}

function integrityCardHtml(item) {
  if (!item || !item.report) return "";
  const layoutOk = !item.layout_warnings || item.layout_warnings.length === 0;
  const classInfo = item.classification_info || {};
  const notionVer = item.notion_verification || {};

  const layoutBadgeClass = layoutOk ? "badge-pill-success" : "badge-pill-warning";
  const layoutBadgeIcon = layoutOk ? "fa-circle-check" : "fa-triangle-exclamation";
  const layoutBadgeText = layoutOk ? "Layout Verified" : `${item.layout_warnings.length} Layout Alert(s)`;

  const classBadgeClass = `badge-pill-${escapeHtml(classInfo.badge_status || (item.case_type === "PERSONAL" ? "neutral" : "info"))}`;
  const classBadgeText = classInfo.badge_label || (item.case_type === "PERSONAL" ? "Personal" : "Certified / Legal");

  let notionBadgeClass = "badge-pill-neutral";
  let notionBadgeIcon = "fa-cloud";
  let notionBadgeText = "Not in Notion DB";
  if (notionVer.has_notion_records) {
    if (notionVer.verified) {
      notionBadgeClass = "badge-pill-success";
      notionBadgeIcon = "fa-cloud-check";
      notionBadgeText = "Notion Records Match";
    } else {
      notionBadgeClass = "badge-pill-warning";
      notionBadgeIcon = "fa-triangle-exclamation";
      notionBadgeText = "Notion Discrepancies";
    }
  }

  let html = `
    <div class="integrity-card">
      <div class="integrity-card-header">
        <div class="integrity-card-title">
          <i class="fa-solid fa-shield-halved" style="color: var(--brand-primary, #0284c7);"></i>
          <span>Automated Layout &amp; Notion Verification</span>
        </div>
        <div class="integrity-badges-wrap">
          <span class="badge-pill ${layoutBadgeClass}"><i class="fa-solid ${layoutBadgeIcon}"></i> ${escapeHtml(layoutBadgeText)}</span>
          <span class="badge-pill ${classBadgeClass}"><i class="fa-solid fa-robot"></i> ${escapeHtml(classBadgeText)}</span>
          <span class="badge-pill ${notionBadgeClass}"><i class="fa-solid ${notionBadgeIcon}"></i> ${escapeHtml(notionBadgeText)}</span>
        </div>
      </div>`;

  if (item.layout_warnings && item.layout_warnings.length > 0) {
    html += `
      <div class="integrity-discrepancy-box">
        <strong><i class="fa-solid fa-triangle-exclamation"></i> Layout Shifts Detected in Motherlab Report:</strong>
        <ul style="margin: 4px 0 0 16px; padding: 0;">
          ${item.layout_warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}
        </ul>
      </div>`;
  }

  if (classInfo.mismatch || classInfo.needs_manual_review) {
    html += `
      <div class="integrity-discrepancy-box" style="background: #fffbeb; border-color: #fde68a;">
        <strong style="color: #92400e;"><i class="fa-solid fa-triangle-exclamation" style="color: #d97706; margin-right: 4px;"></i> Classification Alert:</strong>
        <p style="margin: 4px 0 0; color: #78350f;">${escapeHtml(classInfo.reason)}</p>
      </div>`;
  }

  if (notionVer.discrepancies && notionVer.discrepancies.length > 0) {
    html += `
      <div class="integrity-discrepancy-box">
        <strong><i class="fa-solid fa-circle-exclamation"></i> Notion Participant Cross-Check Discrepancies:</strong>
        <ul style="margin: 4px 0 0 16px; padding: 0;">
          ${notionVer.discrepancies.map(d => `<li>${escapeHtml(d)}</li>`).join("")}
        </ul>
      </div>`;
  }

  if (notionVer.comparison && notionVer.comparison.length > 0) {
    html += `
      <div class="table-scroll" style="margin-top: 4px;">
        <table class="integrity-comparison-table">
          <thead>
            <tr>
              <th>PARTICIPANT</th>
              <th>PDF REPORT DETAILS</th>
              <th>NOTION DB RECORD</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody>
            ${notionVer.comparison.map(comp => `
              <tr class="${comp.status !== 'MATCHED' ? 'row-diff-mismatch' : ''}">
                <td><strong>${escapeHtml(comp.pdf_name || comp.notion_name || 'Participant')}</strong></td>
                <td>${escapeHtml((comp.pdf_role || '-') + ' · DOB: ' + (comp.pdf_dob || '-'))}</td>
                <td>${escapeHtml((comp.notion_role || '-') + ' · DOB: ' + (comp.notion_dob || '-'))}</td>
                <td>
                  ${comp.status === 'MATCHED'
                    ? '<span class="badge-pill badge-pill-success"><i class="fa-solid fa-check"></i> Matched</span>'
                    : `<span class="badge-pill badge-pill-warning"><i class="fa-solid fa-triangle-exclamation"></i> ${escapeHtml(comp.status.replace(/_/g, ' '))}</span>`}
                </td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>`;
  }

  html += `</div>`;
  return html;
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
      ${item.needs_manual_review && item.case_type !== "ISSUE" ? `<div class="alert alert-warning" role="alert"><i class="fa-solid fa-circle-exclamation"></i> <strong>Manual Review Required:</strong> Automated integrity checks flagged this report. Verify the details below and save to confirm.</div>` : ""}
      ${integrityCardHtml(item)}
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
        showAlert("Cannot approve: Report type is set to Issue. Please select Personal or Certified / Legal first.", "error");
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
  if (summaryPanel && !summaryPanel.classList.contains("is-hidden")) {
    renderBatchSummary(job);
    return;
  }
  if (summaryPanel) summaryPanel.classList.add("is-hidden");
  uploadPanel.classList.add("is-hidden");
  jobPanel.classList.remove("is-hidden");
  const jobState = document.getElementById("job-state");
  jobState.textContent = stateLabel(job.state);
  jobState.className = `status-chip ${statusClass(job.state)}`;

  fileRail.innerHTML = job.items.map((item) => `
    <button class="file-row${item.id === state.selectedItemId ? " is-active" : ""}" type="button" data-item-id="${escapeHtml(item.id)}">
      <strong>${escapeHtml(item.original_filename)}</strong>
      <small>${escapeHtml(item.report?.case_code || "NOT EXTRACTED")}</small>
      ${railTagsHtml(item)}
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

  const approvedCount = job.items.filter((item) => item.approved).length;
  if (processButton) {
    processButton.disabled = approvedCount === 0 || ["PROCESSING", "COMPLETE"].includes(job.state);
  }
  const processing = job.state === "PROCESSING";
  document.getElementById("processing-summary").classList.toggle("is-hidden", !processing);
  document.getElementById("progress-value").textContent = `${job.progress}%`;
  document.getElementById("progress-bar").style.width = `${job.progress}%`;
  document.getElementById("batch-expiry").textContent = `Private files expire ${new Date(job.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.`;

  downloadButton.classList.toggle("is-hidden", !job.download_ready);
  if (job.download_ready) downloadButton.href = `/api/jobs/${job.id}/download`;

  if (viewSummaryButton) {
    viewSummaryButton.classList.toggle("is-hidden", !(job.download_ready || job.state === "COMPLETE"));
  }

  const hasCaseCodes = job.items.some((item) => item.report && item.report.case_code);
  if (syncNotionButton) {
    syncNotionButton.classList.toggle("is-hidden", !hasCaseCodes);
  }
  const hasCompletedItems = job.items.some((item) => item.state === "COMPLETE" && item.output_filename);
  if (uploadNotionButton) {
    uploadNotionButton.classList.toggle("is-hidden", !hasCompletedItems);
  }

  if (job.download_ready) setWorkflowStep("download");
  else if (processing) setWorkflowStep("process");
  else setWorkflowStep("review");
}

function renderBatchSummary(job) {
  if (!job) return;
  const totalCount = job.items.length;
  const flaggedCount = job.items.filter(i =>
    i.needs_manual_review ||
    (i.layout_warnings && i.layout_warnings.length > 0) ||
    (i.notion_verification?.discrepancies && i.notion_verification.discrepancies.length > 0)
  ).length;
  const notionDiscrepancyCount = job.items.filter(i =>
    i.notion_verification?.discrepancies && i.notion_verification.discrepancies.length > 0
  ).length;
  const cleanCount = Math.max(0, totalCount - flaggedCount);

  if (summaryTotalCount) summaryTotalCount.textContent = totalCount;
  if (summaryCleanCount) summaryCleanCount.textContent = cleanCount;
  if (summaryFlaggedCount) summaryFlaggedCount.textContent = flaggedCount;
  if (summaryNotionCount) summaryNotionCount.textContent = notionDiscrepancyCount;

  if (summaryBadge) {
    if (flaggedCount === 0) {
      summaryBadge.className = "status-chip status-success";
      summaryBadge.textContent = "ALL VERIFIED & CLEAN";
    } else {
      summaryBadge.className = "status-chip status-warning";
      summaryBadge.textContent = `${flaggedCount} FLAGGED FOR REVIEW`;
    }
  }

  if (summaryTableBody) {
    summaryTableBody.innerHTML = job.items.map((item) => {
      const caseCode = item.report?.case_code || item.original_filename;
      const outputFilename = item.output_filename || (item.state === "COMPLETE" ? `${caseCode}.pdf` : "Pending");
      let classificationHtml = `<span class="badge-pill badge-pill-neutral">Personal</span>`;
      if (item.case_type === "ISSUE") {
        classificationHtml = `<span class="badge-pill badge-pill-danger"><i class="fa-solid fa-triangle-exclamation"></i> Issue (Unclassified)</span>`;
      } else if (item.case_type === "CERTIFIED_LEGAL") {
        classificationHtml = `<span class="badge-pill badge-pill-info">Certified / Legal</span>`;
      }

      let layoutHtml = '<span class="badge-pill badge-pill-success"><i class="fa-solid fa-check"></i> Clean</span>';
      if (item.layout_warnings && item.layout_warnings.length > 0) {
        layoutHtml = `<span class="badge-pill badge-pill-warning" title="${escapeHtml(item.layout_warnings.join('; '))}"><i class="fa-solid fa-triangle-exclamation"></i> ${item.layout_warnings.length} Alert(s)</span>`;
      }

      let notionHtml = '<span class="badge-pill badge-pill-neutral">Not in DB</span>';
      if (item.notion_verification?.has_notion_records) {
        if (item.notion_verification.verified) {
          notionHtml = '<span class="badge-pill badge-pill-success"><i class="fa-solid fa-cloud-check"></i> Verified</span>';
        } else {
          const discCount = item.notion_verification.discrepancies?.length || 1;
          notionHtml = `<span class="badge-pill badge-pill-warning" title="${escapeHtml((item.notion_verification.discrepancies || []).join('; '))}"><i class="fa-solid fa-triangle-exclamation"></i> Discrepancy (${discCount})</span>`;
        }
      }

      let notionAttachmentHtml = '<span class="badge-pill badge-pill-neutral">Not Uploaded</span>';
      if (item.notion_upload_status === "UPLOADED") {
        notionAttachmentHtml = '<span class="badge-pill badge-pill-success" title="Attached to Custody Pics in Notion"><i class="fa-solid fa-cloud-check"></i> Attached</span>';
      } else if (item.notion_upload_status === "UPLOADING") {
        notionAttachmentHtml = '<span class="badge-pill badge-pill-info"><i class="fa-solid fa-spinner fa-spin"></i> Uploading...</span>';
      } else if (item.notion_upload_status === "FAILED") {
        notionAttachmentHtml = `
          <div style="display: flex; align-items: center; gap: 6px;">
            <span class="badge-pill badge-pill-danger" title="${escapeHtml(item.notion_upload_error || 'Upload failed')}"><i class="fa-solid fa-circle-exclamation"></i> Failed</span>
            <button type="button" class="button button-secondary single-upload-btn" data-item-id="${item.id}" style="padding: 2px 6px; font-size: 0.72rem; min-height: 24px;" title="Retry upload to Notion"><i class="fa-solid fa-rotate-right"></i> Retry</button>
          </div>`;
      } else if (item.state === "COMPLETE" && item.output_filename) {
        notionAttachmentHtml = `
          <button type="button" class="button button-secondary single-upload-btn" data-item-id="${item.id}" style="padding: 2px 8px; font-size: 0.75rem; min-height: 26px; background: #EFF6FF; color: #1D4ED8; border: 1px solid #BFDBFE;" title="Upload this report to Notion Custody Pics">
            <i class="fa-solid fa-cloud-arrow-up"></i> Upload
          </button>`;
      }

      let actionHtml = `<span class="status-chip ${statusClass(item.state)}">${escapeHtml(stateLabel(item.state))}</span>`;
      if (job.download_ready && item.output_filename) {
        actionHtml += ` <a href="/api/jobs/${job.id}/items/${item.id}/download" class="button button-secondary" style="padding: 4px 10px; font-size: 0.76rem; min-height: 28px;" download="${escapeHtml(item.output_filename)}"><i class="fa-solid fa-file-arrow-down"></i> PDF</a>`;
      }

      return `
        <tr>
          <td><strong>${escapeHtml(caseCode)}</strong><br><small style="color: var(--muted);">${escapeHtml(item.original_filename)}</small></td>
          <td><code>${escapeHtml(outputFilename)}</code></td>
          <td>${classificationHtml}</td>
          <td>${layoutHtml}</td>
          <td>${notionHtml}</td>
          <td>${notionAttachmentHtml}</td>
          <td><div style="display: flex; align-items: center; gap: 8px;">${actionHtml}</div></td>
        </tr>`;
    }).join("");
  }

  if (summaryDownloadBtn) {
    if (job.download_ready) {
      summaryDownloadBtn.classList.remove("is-hidden");
      summaryDownloadBtn.href = `/api/jobs/${job.id}/download`;
    } else {
      summaryDownloadBtn.classList.add("is-hidden");
    }
  }

  if (summarySyncNotionBtn) {
    const hasCaseCodes = job.items.some((item) => item.report && item.report.case_code);
    summarySyncNotionBtn.classList.toggle("is-hidden", !hasCaseCodes);
  }

  if (summaryUploadNotionBtn) {
    const hasCompleted = job.items.some((item) => item.state === "COMPLETE" && item.output_filename);
    summaryUploadNotionBtn.classList.toggle("is-hidden", !hasCompleted);
  }
}

function showSummaryView() {
  if (!state.job) return;
  jobPanel.classList.add("is-hidden");
  uploadPanel.classList.add("is-hidden");
  if (summaryPanel) summaryPanel.classList.remove("is-hidden");
  setWorkflowStep("download");
  renderBatchSummary(state.job);
}

function showReviewView() {
  if (!state.job) return;
  if (summaryPanel) summaryPanel.classList.add("is-hidden");
  uploadPanel.classList.add("is-hidden");
  jobPanel.classList.remove("is-hidden");
  setWorkflowStep("review");
  renderJob();
}

async function syncNotionStatus(jobId) {
  if (!jobId) return;
  if (syncNotionButton) {
    syncNotionButton.disabled = true;
    syncNotionButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Syncing Notion...';
  }
  if (notionSyncBanner) {
    notionSyncBanner.className = "alert alert-info";
    notionSyncBanner.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Updating matched cases to <strong>Result Review</strong> in Notion...';
    notionSyncBanner.classList.remove("is-hidden");
  }
  try {
    const response = await api(`/api/jobs/${jobId}/sync-notion-status`, { method: "POST" });
    const result = await response.json();
    const updatedCount = result.updated_count ?? result.updated ?? 0;
    const codesList = (result.case_codes && result.case_codes.length) ? result.case_codes : (result.unmatched_codes || []);
    const codes = codesList.join(", ");

    if (notionSyncBanner) {
      if (updatedCount > 0) {
        notionSyncBanner.className = "alert alert-success";
        notionSyncBanner.innerHTML = `<i class="fa-solid fa-circle-check"></i> Successfully updated <strong>${updatedCount}</strong> case(s) in Notion to <strong>Result Review</strong> (${escapeHtml(codes)}).`;
        showAlert(`Notion Sync: ${updatedCount} case(s) updated to Result Review.`, "success");
      } else {
        notionSyncBanner.className = "alert alert-warning";
        notionSyncBanner.innerHTML = `<i class="fa-solid fa-circle-exclamation"></i> No matching cases found in Notion database for codes: ${escapeHtml(codes || "none")}.`;
        showAlert("Notion Sync: No matching cases found.", "warning");
      }
      notionSyncBanner.classList.remove("is-hidden");
    }
  } catch (error) {
    if (notionSyncBanner) {
      notionSyncBanner.className = "alert alert-danger";
      notionSyncBanner.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Notion sync failed: ${escapeHtml(error.message)}`;
      notionSyncBanner.classList.remove("is-hidden");
    }
    showAlert(`Notion sync failed: ${error.message}`, "danger");
  } finally {
    if (syncNotionButton) {
      syncNotionButton.disabled = false;
      syncNotionButton.innerHTML = '<i class="fa-solid fa-arrows-rotate"></i> Sync Status Only';
    }
  }
}

async function uploadBatchToNotion(jobId) {
  if (!jobId) return;
  if (summaryUploadNotionBtn) {
    summaryUploadNotionBtn.disabled = true;
    summaryUploadNotionBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Uploading to Notion...';
  }
  if (uploadNotionButton) {
    uploadNotionButton.disabled = true;
    uploadNotionButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Uploading...';
  }
  if (notionSyncBanner) {
    notionSyncBanner.className = "alert alert-info";
    notionSyncBanner.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Uploading report PDFs directly to Notion <strong>Custody Pics</strong> &amp; setting status to <strong>Result Review</strong>...';
    notionSyncBanner.classList.remove("is-hidden");
  }

  // Immediately reflect UPLOADING status on all eligible rows in the UI
  if (state.job?.items) {
    state.job.items.forEach((item) => {
      if (item.state === "COMPLETE" && item.output_filename) {
        item.notion_upload_status = "UPLOADING";
      }
    });
    if (summaryPanel && !summaryPanel.classList.contains("is-hidden")) {
      renderBatchSummary(state.job);
    }
  }

  try {
    const response = await api(`/api/jobs/${jobId}/upload-notion-reports`, { method: "POST" });
    const result = await response.json();
    const uploaded = result.uploaded ?? 0;
    const failed = result.failed ?? 0;

    await refreshJob();
    if (summaryPanel && !summaryPanel.classList.contains("is-hidden")) {
      renderBatchSummary(state.job);
    }

    if (uploaded > 0 && failed === 0) {
      if (notionSyncBanner) {
        notionSyncBanner.className = "alert alert-success";
        notionSyncBanner.innerHTML = `<i class="fa-solid fa-circle-check"></i> Successfully attached <strong>${uploaded}</strong> report(s) to Notion (<strong>Custody Pics</strong>) and updated status to <strong>Result Review</strong>!`;
        notionSyncBanner.classList.remove("is-hidden");
      }
      showAlert(`Successfully uploaded ${uploaded} report(s) directly to Notion ("Custody Pics")!`, "success");
    } else if (uploaded > 0 && failed > 0) {
      if (notionSyncBanner) {
        notionSyncBanner.className = "alert alert-warning";
        notionSyncBanner.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Uploaded <strong>${uploaded}</strong> report(s) to Notion, but <strong>${failed}</strong> failed. Review individual items in summary table.`;
        notionSyncBanner.classList.remove("is-hidden");
      }
      showAlert(`Uploaded ${uploaded} report(s) to Notion, but ${failed} failed.`, "warning");
    } else {
      if (notionSyncBanner) {
        notionSyncBanner.className = "alert alert-danger";
        notionSyncBanner.innerHTML = `<i class="fa-solid fa-circle-exclamation"></i> Failed to upload reports: ${escapeHtml(result.message || result.error || "Unknown error")}`;
        notionSyncBanner.classList.remove("is-hidden");
      }
      showAlert(`Upload to Notion failed: ${result.message || result.error || "Unknown error"}`, "danger");
    }
  } catch (error) {
    if (notionSyncBanner) {
      notionSyncBanner.className = "alert alert-danger";
      notionSyncBanner.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Notion upload failed: ${escapeHtml(error.message)}`;
      notionSyncBanner.classList.remove("is-hidden");
    }
    showAlert(`Failed to upload reports to Notion: ${error.message}`, "danger");
  } finally {
    if (summaryUploadNotionBtn) {
      summaryUploadNotionBtn.disabled = false;
      summaryUploadNotionBtn.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> Upload Reports to Notion ("Custody Pics")';
    }
    if (uploadNotionButton) {
      uploadNotionButton.disabled = false;
      uploadNotionButton.innerHTML = '<i class="fa-solid fa-cloud-arrow-up"></i> Upload Reports to Notion';
    }
  }
}

async function uploadSingleReportToNotion(jobId, itemId, buttonEl) {
  if (!jobId || !itemId) return;
  if (buttonEl) {
    buttonEl.disabled = true;
    buttonEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
  }
  const item = state.job?.items?.find((i) => i.id === itemId);
  if (item) {
    item.notion_upload_status = "UPLOADING";
    if (summaryPanel && !summaryPanel.classList.contains("is-hidden")) {
      renderBatchSummary(state.job);
    }
  }
  try {
    const response = await api(`/api/jobs/${jobId}/items/${itemId}/upload-notion-report`, { method: "POST" });
    const result = await response.json();
    if (result.success) {
      showAlert(`Report attached to Notion ("Custody Pics") successfully.`, "success");
    } else {
      showAlert(`Upload failed: ${result.error || "Unknown error"}`, "danger");
    }
    await refreshJob();
    if (summaryPanel && !summaryPanel.classList.contains("is-hidden")) {
      renderBatchSummary(state.job);
    }
  } catch (error) {
    showAlert(`Failed to upload report to Notion: ${error.message}`, "danger");
    await refreshJob();
    if (summaryPanel && !summaryPanel.classList.contains("is-hidden")) {
      renderBatchSummary(state.job);
    }
  }
}

function itemPayload() {
  const form = document.getElementById("item-form");
  const data = new FormData(form);
  const participants = Array.from(form.querySelectorAll(".participant-table tbody tr")).map((row) => ({
    id: row.dataset.participantId,
    source_order: Number(row.querySelector('[name="source_order"]')?.value ?? 0),
    sample_number: row.querySelector('[name="sample_number"]')?.value ?? "",
    role: row.querySelector('[name="role"]')?.value ?? "",
    full_name: row.querySelector('[name="full_name"]')?.value ?? "",
    date_of_birth: row.querySelector('[name="date_of_birth"]')?.value ?? "",
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
  if (summaryPanel && !summaryPanel.classList.contains("is-hidden")) {
    renderBatchSummary(state.job);
  } else {
    renderJob();
  }
}

function startPolling() {
  window.clearInterval(state.pollTimer);
  state.pollTimer = window.setInterval(async () => {
    try {
      await refreshJob();
      if (state.job?.download_ready && autoSyncNotionCheckbox?.checked && !state.syncedNotionJobs.has(state.job.id)) {
        state.syncedNotionJobs.add(state.job.id);
        syncNotionStatus(state.job.id);
      }

      const hasPendingNotionUploads = state.job?.items?.some((i) => i.notion_upload_status === "UPLOADING");

      if (state.job?.state === "COMPLETE") {
        if (summaryPanel && summaryPanel.classList.contains("is-hidden")) {
          showSummaryView();
        } else {
          renderBatchSummary(state.job);
        }
        // ONLY stop polling if background Notion upload has also finished!
        if (!hasPendingNotionUploads) {
          window.clearInterval(state.pollTimer);
        }
      } else if (!["READY", "PROCESSING"].includes(state.job.state) && !hasPendingNotionUploads) {
        window.clearInterval(state.pollTimer);
      }
    } catch (error) {
      window.clearInterval(state.pollTimer);
      showAlert(error.message);
    }
  }, 900);
}

if (fileInput) {
  fileInput.addEventListener("change", () => handleNewFiles(fileInput.files));
}

if (selectedFiles) {
  selectedFiles.addEventListener("click", (event) => {
    const removeBtn = event.target.closest(".file-remove-button");
    if (!removeBtn) return;
    const index = Number(removeBtn.dataset.index);
    if (!Number.isNaN(index) && index >= 0 && index < selectedFilesList.length) {
      selectedFilesList.splice(index, 1);
      displaySelectedFiles();
    }
  });
}


if (dropZone) {
  dropZone.addEventListener("dragover", (event) => {
    event.preventDefault();
    dropZone.classList.add("is-dragging");
  });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("is-dragging"));
  dropZone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropZone.classList.remove("is-dragging");
    handleNewFiles(event.dataTransfer.files);
  });
}

if (uploadForm) {
  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearAlert();
    if (notionSyncBanner) {
      notionSyncBanner.textContent = "";
      notionSyncBanner.className = "alert is-hidden";
    }
    if (!selectedFilesList.length || selectedFilesList.length > maxBatchFiles) {
      showAlert(`Choose between 1 and ${maxBatchFiles} PDF files.`);
      return;
    }
    const button = document.getElementById("upload-button");
    if (button) {
      button.disabled = true;
      button.textContent = "Extracting…";
    }
    showExtractionLoader();

    const labSourceRadio = document.querySelector('input[name="lab_source"]:checked');
    const reviewModeRadio = document.querySelector('input[name="review_mode"]:checked');
    const defaultCaseTypeInput = document.getElementById("upload-default-case-type");

    const formData = new FormData();
    formData.append("lab_source", labSourceRadio ? labSourceRadio.value : "EUROFINS");
    formData.append("review_mode", reviewModeRadio ? reviewModeRadio.value : "REVIEW");
    formData.append("default_case_type", defaultCaseTypeInput ? defaultCaseTypeInput.value : "ISSUE");
    formData.append("auto_upload_notion", autoUploadNotionCheckbox?.checked ? "true" : "false");

    for (const file of selectedFilesList) {
      formData.append("files", file);
    }


    try {
      const response = await api("/api/jobs", { method: "POST", body: formData });
      state.job = await response.json();
      state.selectedItemId = state.job.items[0]?.id || null;
      await finishExtractionLoader();
      renderJob();
      if (["READY", "PROCESSING"].includes(state.job.state)) startPolling();
    } catch (error) {
      hideExtractionLoader();
      showAlert(error.message);
    } finally {
      hideExtractionLoader();
      if (button) {
        button.disabled = false;
        button.textContent = "Extract reports";
      }
    }
  });
}

if (processButton) {
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
}

if (syncNotionButton) {
  syncNotionButton.addEventListener("click", () => {
    if (state.job?.id) {
      syncNotionStatus(state.job.id);
    }
  });
}

const newBatchBtn = document.getElementById("new-batch-button");
if (newBatchBtn) {
  newBatchBtn.addEventListener("click", () => {
    window.clearInterval(state.pollTimer);
    state.job = null;
    state.selectedItemId = null;
    selectedFilesList = [];
    if (uploadForm) uploadForm.reset();
    const modeRadio = document.querySelector(`input[name="review_mode"][value="${defaultReviewMode}"]`);
    if (modeRadio) modeRadio.checked = true;
    if (selectedFiles) selectedFiles.textContent = "";
    if (summaryPanel) summaryPanel.classList.add("is-hidden");
    if (jobPanel) jobPanel.classList.add("is-hidden");
    if (uploadPanel) uploadPanel.classList.remove("is-hidden");
    setWorkflowStep("upload");
    clearAlert();
    if (notionSyncBanner) {
      notionSyncBanner.textContent = "";
      notionSyncBanner.className = "alert is-hidden";
    }
  });
}

if (summaryBackReviewBtn) {
  summaryBackReviewBtn.addEventListener("click", () => {
    showReviewView();
  });
}

if (summaryNewBatchBtn) {
  summaryNewBatchBtn.addEventListener("click", () => {
    if (newBatchBtn) newBatchBtn.click();
  });
}

if (summarySyncNotionBtn) {
  summarySyncNotionBtn.addEventListener("click", () => {
    if (state.job?.id) {
      syncNotionStatus(state.job.id);
    }
  });
}

if (summaryUploadNotionBtn) {
  summaryUploadNotionBtn.addEventListener("click", () => {
    if (state.job?.id) {
      uploadBatchToNotion(state.job.id);
    }
  });
}

if (uploadNotionButton) {
  uploadNotionButton.addEventListener("click", () => {
    if (state.job?.id) {
      uploadBatchToNotion(state.job.id);
    }
  });
}

if (summaryTableBody) {
  summaryTableBody.addEventListener("click", async (event) => {
    const btn = event.target.closest(".single-upload-btn");
    if (!btn) return;
    const itemId = btn.dataset.itemId;
    if (itemId && state.job?.id) {
      await uploadSingleReportToNotion(state.job.id, itemId, btn);
    }
  });
}

if (viewSummaryButton) {
  viewSummaryButton.addEventListener("click", () => {
    showSummaryView();
  });
}

document.querySelectorAll(".stepper li").forEach((li) => {
  li.addEventListener("click", () => {
    const step = li.dataset.step;
    if (step === "review" && state.job) {
      showReviewView();
    } else if (step === "download" && state.job && (state.job.state === "COMPLETE" || state.job.download_ready)) {
      showSummaryView();
    }
  });
});

const openSettingsBtn = document.querySelector("[data-open-settings]");
if (openSettingsBtn) {
  openSettingsBtn.addEventListener("click", () => {
    const navBtnSettings = document.getElementById("nav-btn-settings");
    if (navBtnSettings) navBtnSettings.click();
  });
}

const savePageSettingsBtn = document.getElementById("save-page-settings-btn");
if (savePageSettingsBtn) {
  savePageSettingsBtn.addEventListener("click", async () => {
    const selectedReview = document.querySelector('input[name="page_settings_review_mode"]:checked');
    const selectedCase = document.querySelector('input[name="page_settings_case_type"]:checked');
    if (!selectedReview) return;
    savePageSettingsBtn.disabled = true;
    savePageSettingsBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving...';
    try {
      const payload = {
        default_review_mode: selectedReview.value,
        default_case_type: selectedCase ? selectedCase.value : "ISSUE",
      };
      await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      const modeRadio = document.querySelector(`input[name="review_mode"][value="${selectedReview.value}"]`);
      if (modeRadio) modeRadio.checked = true;
      const hiddenDefault = document.getElementById("upload-default-case-type");
      if (hiddenDefault && selectedCase) hiddenDefault.value = selectedCase.value;
      const indicator = document.getElementById("settings-save-indicator");
      if (indicator) {
        indicator.style.display = "inline-flex";
        setTimeout(() => { indicator.style.display = "none"; }, 3000);
      }
      if (typeof window.showToast === "function") {
        window.showToast("Default settings saved successfully.", "success");
      }
    } catch (error) {
      if (typeof window.showToast === "function") {
        window.showToast(error.message || "Failed to save settings.", "error");
      }
    } finally {
      savePageSettingsBtn.disabled = false;
      savePageSettingsBtn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Save Default Settings';
    }
  });
}

const logoutBtn = document.querySelector("[data-logout]");
if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    try {
      await api("/logout", { method: "POST" });
    } finally {
      window.location.assign("/login");
    }
  });
}

})();
