(() => {
"use strict";

// State Management
let allCases = [];
let cases = [];
let currentLabFilter = 'ALL';
let currentSortMode = 'date-asc';
let docxRowCount = 0;

// DOM Elements
const sourcePanel = document.getElementById('source-panel');
const editorPanel = document.getElementById('editor-panel');
const tabNotionBtn = document.getElementById('tab-notion-btn');
const tabWordBtn = document.getElementById('tab-word-btn');
const notionView = document.getElementById('notion-view');
const wordView = document.getElementById('word-view');
const fetchNotionBtn = document.getElementById('fetch-notion-btn');
const editorRefreshNotionBtn = document.getElementById('editor-refresh-notion-btn');
const notionStatusIndicator = document.getElementById('notion-status-indicator');
const labFilterSelect = document.getElementById('lab-filter-select');
const sortSelect = document.getElementById('sort-select');
const autoDispatchLabel = document.getElementById('auto-dispatch-label');
const autoDispatchCheckbox = document.getElementById('auto-dispatch-checkbox');

// Upload Elements (Legacy Word parser only)
const dropZone = document.getElementById('manifest-drop-zone') || document.getElementById('drop-zone-legacy');
const fileInput = document.getElementById('manifest-file-input') || document.getElementById('file-input');

// Editor Elements
const casesGrid = document.getElementById('cases-grid');
const casesCountBadge = document.getElementById('cases-count-badge');
const loadingOverlay = document.getElementById('loading-overlay');
const toastContainer = document.getElementById('toast-container');
const statusBadge = document.getElementById('status-badge');

// Global Action Buttons
const addCaseBtn = document.getElementById('add-case-btn');
const exportExcelBtn = document.getElementById('export-excel-btn');
const uploadAnotherBtn = document.getElementById('upload-another-btn');

/* -------------------------------------------------------------
 * Initialization & Notion Status
 * ------------------------------------------------------------- */

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkNotionStatus);
} else {
    checkNotionStatus();
}

function checkNotionStatus() {
    fetch('/api/notion/status')
        .then(res => res.json())
        .then(data => {
            if (data.configured) {
                notionStatusIndicator.className = 'notion-status-pill';
                notionStatusIndicator.innerHTML = '<i class="fa-solid fa-circle-check"></i> Connected';
            } else {
                notionStatusIndicator.className = 'notion-status-pill not-configured';
                notionStatusIndicator.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Credentials Needed';
            }
        })
        .catch(() => {
            notionStatusIndicator.className = 'notion-status-pill not-configured';
            notionStatusIndicator.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Offline';
        });
}

/* -------------------------------------------------------------
 * Source Tab Navigation
 * ------------------------------------------------------------- */

if (tabNotionBtn && tabWordBtn) {
    tabNotionBtn.addEventListener('click', () => {
        tabNotionBtn.classList.add('active');
        tabWordBtn.classList.remove('active');
        if (notionView) notionView.style.display = 'block';
        if (wordView) wordView.style.display = 'none';
    });

    tabWordBtn.addEventListener('click', () => {
        tabWordBtn.classList.add('active');
        tabNotionBtn.classList.remove('active');
        if (wordView) wordView.style.display = 'block';
        if (notionView) notionView.style.display = 'none';
    });
}

/* -------------------------------------------------------------
 * Notion Fetch Action & Cache Shield
 * ------------------------------------------------------------- */

function fetchNotionData(forceRefresh = true) {
    showLoader(true, 'Querying Notion Live...', 'Querying database, consolidating multi-part cases (>4 participants), and arranging parents before children.');
    updateStatus('Connecting to Notion...', 'fa-circle-notch fa-spin');

    fetch('/api/notion/fetch', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ filter_mode: 'manifest_batch', force_refresh: true })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.error || 'Failed to fetch Notion database') });
        }
        return response.json();
    })
    .then(data => {
        allCases = sortCases(data.cases || [], currentSortMode);
        docxRowCount = 0;

        if (allCases.length === 0) {
            showToast('No manifest cases found in the Notion batch.', 'error');
            showLoader(false);
            updateStatus('Ready', 'fa-check');
            return;
        }

        // Populate Receiving Lab filter dropdown
        populateLabDropdown(data.available_labs || []);

        // Filter and display
        filterCasesByLab(currentLabFilter || 'ALL');
        if (autoDispatchLabel) autoDispatchLabel.style.display = 'inline-flex';
        if (autoDispatchCheckbox) autoDispatchCheckbox.checked = true;
        renderWorkspace();

        const multipartCount = allCases.filter(c => c.is_multipart).length;
        const msg = multipartCount > 0 
            ? `Loaded ${allCases.length} cases from Notion (${multipartCount} multi-part cases merged)!`
            : `Loaded ${allCases.length} cases from Notion!`;
        showToast(msg, 'success');
    })
    .catch(error => {
        console.error('Notion fetch error:', error);
        showToast(error.message || 'Error connecting to Notion', 'error');
        showLoader(false);
        updateStatus('Error', 'fa-triangle-exclamation');
    });
}

if (fetchNotionBtn) {
    fetchNotionBtn.addEventListener('click', () => fetchNotionData(true));
}
if (editorRefreshNotionBtn) {
    editorRefreshNotionBtn.addEventListener('click', () => fetchNotionData(true));
}

/* -------------------------------------------------------------
 * Receiving Lab Filtering
 * ------------------------------------------------------------- */

function populateLabDropdown(availableLabs) {
    labFilterSelect.innerHTML = '';
    
    // Default option
    const allOpt = document.createElement('option');
    allOpt.value = 'ALL';
    allOpt.textContent = `All Receiving Labs (${allCases.length})`;
    labFilterSelect.appendChild(allOpt);

    // Populate each lab
    availableLabs.forEach(lab => {
        const count = allCases.filter(c => (c.receiving_lab || '').toUpperCase() === lab.toUpperCase()).length;
        const opt = document.createElement('option');
        opt.value = lab;
        opt.textContent = `${lab} (${count})`;
        labFilterSelect.appendChild(opt);
    });

    currentLabFilter = 'ALL';
}

if (labFilterSelect) {
    labFilterSelect.addEventListener('change', (e) => {
        filterCasesByLab(e.target.value);
    });
}

function filterCasesByLab(lab) {
    currentLabFilter = lab;
    const baseList = (lab === 'ALL')
        ? [...allCases]
        : allCases.filter(c => (c.receiving_lab || '').toUpperCase() === lab.toUpperCase());
    cases = sortCases(baseList, currentSortMode);
    renderCards();
    showToast(`Displaying ${cases.length} case(s) for ${lab === 'ALL' ? 'All Labs' : lab}`, 'info');
}

/* -------------------------------------------------------------
 * Case Sorting (Date Ascending / Descending / Reference)
 * ------------------------------------------------------------- */

function extractCaseDateAndSeq(tag) {
    if (!tag) return { dateVal: Infinity, seqNum: 999999, tag: '' };
    const str = String(tag).trim().toUpperCase();
    const parts = str.split('-');
    let parsedDate = null;
    let dateIdx = -1;

    for (let i = 0; i < parts.length; i++) {
        const p = parts[i];
        if (p.length === 6 && /^\d{6}$/.test(p)) {
            const dd = parseInt(p.slice(0, 2), 10);
            const mm = parseInt(p.slice(2, 4), 10);
            const yy = parseInt(p.slice(4), 10);
            const fullYear = yy < 70 ? 2000 + yy : 1900 + yy;
            const dObj = new Date(fullYear, mm - 1, dd);
            if (
                dObj.getFullYear() === fullYear &&
                dObj.getMonth() === mm - 1 &&
                dObj.getDate() === dd
            ) {
                parsedDate = dObj.getTime();
                dateIdx = i;
                break;
            }
        }
    }

    if (parsedDate === null) {
        const match = str.match(/\b(\d{2})(\d{2})(\d{2})\b/);
        if (match) {
            const dd = parseInt(match[1], 10);
            const mm = parseInt(match[2], 10);
            const yy = parseInt(match[3], 10);
            const fullYear = yy < 70 ? 2000 + yy : 1900 + yy;
            const dObj = new Date(fullYear, mm - 1, dd);
            if (
                dObj.getFullYear() === fullYear &&
                dObj.getMonth() === mm - 1 &&
                dObj.getDate() === dd
            ) {
                parsedDate = dObj.getTime();
            }
        }
    }

    let seqNum = 999999;
    if (dateIdx !== -1 && dateIdx + 1 < parts.length) {
        const seqMatch = parts[dateIdx + 1].match(/\d+/);
        if (seqMatch) {
            seqNum = parseInt(seqMatch[0], 10);
        }
    }
    if (seqNum === 999999) {
        const allNums = str.match(/\d+/g);
        if (allNums && allNums.length > 0) {
            seqNum = parseInt(allNums[allNums.length - 1], 10);
        }
    }

    return {
        dateVal: parsedDate !== null ? parsedDate : Infinity,
        seqNum: seqNum,
        tag: str
    };
}

function sortCases(casesList, sortMode = 'date-asc') {
    return [...casesList].sort((a, b) => {
        const infoA = extractCaseDateAndSeq(a.bdo_tag || a.case_code || '');
        const infoB = extractCaseDateAndSeq(b.bdo_tag || b.case_code || '');

        if (sortMode === 'date-asc') {
            if (infoA.dateVal !== infoB.dateVal) return infoA.dateVal - infoB.dateVal;
            if (infoA.seqNum !== infoB.seqNum) return infoA.seqNum - infoB.seqNum;
            return infoA.tag.localeCompare(infoB.tag);
        } else if (sortMode === 'date-desc') {
            if (infoA.dateVal !== infoB.dateVal) return infoB.dateVal - infoA.dateVal;
            if (infoA.seqNum !== infoB.seqNum) return infoB.seqNum - infoA.seqNum;
            return infoB.tag.localeCompare(infoA.tag);
        } else if (sortMode === 'tag-asc') {
            return (a.bdo_tag || a.case_code || '').localeCompare(b.bdo_tag || b.case_code || '');
        } else if (sortMode === 'tag-desc') {
            return (b.bdo_tag || b.case_code || '').localeCompare(a.bdo_tag || a.case_code || '');
        }
        return 0;
    });
}

if (sortSelect) {
    sortSelect.addEventListener('change', (e) => {
        currentSortMode = e.target.value;
        cases = sortCases(cases, currentSortMode);
        renderCards();
        const label = e.target.options[e.target.selectedIndex].text;
        showToast(`Manifest sorted by: ${label}`, 'info');
    });
}

/* -------------------------------------------------------------
 * Word Document Upload Fallback
 * ------------------------------------------------------------- */

if (dropZone && fileInput) {
    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
        }, false);
    });

    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        if (files.length > 0) {
            handleFileSelect(files[0]);
        }
    });

    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileSelect(e.target.files[0]);
        }
    });
}

function handleFileSelect(file) {
    if (!file.name.toLowerCase().endsWith('.docx')) {
        showToast('Please upload a valid .docx file', 'error');
        return;
    }

    const formData = new FormData();
    formData.append('file', file);

    showLoader(true, 'Parsing Word document...', 'Extracting tables and participant details.');
    updateStatus('Parsing document...', 'fa-circle-notch fa-spin');

    fetch('/api/parse', {
        method: 'POST',
        body: formData
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.error || 'Unknown parsing error') });
        }
        return response.json();
    })
    .then(data => {
        allCases = sortCases(data.cases || [], currentSortMode);
        docxRowCount = data.docx_rows || 0;
        cases = [...allCases];

        if (cases.length === 0) {
            showToast('No cases found in the uploaded file.', 'error');
            showLoader(false);
            updateStatus('Ready', 'fa-check');
            return;
        }

        // Reset lab filter dropdown for manual upload
        labFilterSelect.innerHTML = `<option value="ALL">All Cases (${cases.length})</option>`;

        if (docxRowCount && docxRowCount !== cases.length) {
            showToast(`Mismatch detected: ${docxRowCount} rows in Word but ${cases.length} cases parsed. Review before exporting.`, 'error');
            updateStatus('Mismatch detected', 'fa-triangle-exclamation');
        } else {
            showToast(`Successfully parsed ${cases.length} cases`, 'success');
        }
        if (autoDispatchLabel) autoDispatchLabel.style.display = 'none';
        if (autoDispatchCheckbox) autoDispatchCheckbox.checked = false;
        renderWorkspace();
    })
    .catch(error => {
        console.error('Parsing error:', error);
        showToast(error.message || 'Server error while parsing docx', 'error');
        showLoader(false);
        updateStatus('Error', 'fa-triangle-exclamation');
    });
}

/* -------------------------------------------------------------
 * Manifest Verification & Export Action
 * ------------------------------------------------------------- */

function getUnverifiedManifestIssues() {
    const issues = [];
    cases.forEach((c, idx) => {
        const caseNum = idx + 1;
        const tag = c.bdo_tag || `Case #${caseNum}`;
        const reasons = [];

        if (c.ready_for_manifest === false) {
            reasons.push("Review Pending in Notion (Consent forms or review incomplete)");
        }
        if (c.test_type && (c.test_type.includes("⚠️") || c.test_type.includes("CHECK:"))) {
            reasons.push(`Notion Mismatch: ${c.test_type}`);
        }
        if (!c.bdo_tag || !c.bdo_tag.trim()) {
            reasons.push("Missing BDO Tag / Reference Number");
        }
        if (!c.participants || c.participants.length === 0) {
            reasons.push("No participants entered for case");
        }

        if (reasons.length > 0) {
            issues.push({
                caseIdx: idx,
                caseNum,
                tag,
                reasons
            });
        }
    });
    return issues;
}

function showManifestVerificationModal(issues) {
    let modal = document.getElementById('manifest-verification-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'manifest-verification-modal';
        modal.className = 'modal-backdrop-custom';
        document.body.appendChild(modal);
    }

    const rowsHtml = issues.map(iss => `
        <tr style="border-bottom: 1px solid #F1F5F9;">
            <td style="padding: 12px 14px; font-weight: 600; color: #1E293B; vertical-align: top;">Case #${iss.caseNum}</td>
            <td style="padding: 12px 14px; font-family: monospace; font-size: 0.9rem; color: #0284C7; font-weight: 600; vertical-align: top;">${escapeHtml(iss.tag)}</td>
            <td style="padding: 12px 14px; color: #B91C1C; vertical-align: top;">
                ${iss.reasons.map(r => `<div style="margin-bottom: 4px; display: flex; align-items: flex-start; gap: 6px;"><i class="fa-solid fa-triangle-exclamation" style="margin-top: 3px; font-size: 0.85rem; color: #DC2626;"></i> <span>${escapeHtml(r)}</span></div>`).join('')}
            </td>
        </tr>
    `).join('');

    modal.innerHTML = `
        <div class="modal-card" style="max-width: 720px; width: 92%; background: #ffffff; border-radius: 14px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.25); overflow: hidden;">
            <div style="background: #FEF3C7; border-bottom: 1px solid #FDE68A; padding: 18px 24px; display: flex; align-items: center; justify-content: space-between;">
                <div style="display: flex; align-items: center; gap: 14px;">
                    <div style="width: 42px; height: 42px; border-radius: 50%; background: #F59E0B; color: white; display: flex; align-items: center; justify-content: center; font-size: 1.25rem;">
                        <i class="fa-solid fa-lock"></i>
                    </div>
                    <div>
                        <h3 style="margin: 0; font-size: 1.18rem; font-weight: 700; color: #92400E; font-family: 'Outfit', sans-serif;">Export Locked: Unverified Cases Detected</h3>
                        <p style="margin: 2px 0 0; font-size: 0.85rem; color: #B45309;">${issues.length} case${issues.length > 1 ? 's require' : ' requires'} verification in Notion before generating Excel.</p>
                    </div>
                </div>
                <button type="button" onclick="closeManifestVerificationModal()" style="background: none; border: none; font-size: 1.35rem; color: #92400E; cursor: pointer; padding: 4px 8px; border-radius: 6px;">
                    <i class="fa-solid fa-xmark"></i>
                </button>
            </div>
            
            <div style="padding: 22px 24px;">
                <div style="background: #FFFBEB; border-left: 4px solid #F59E0B; padding: 14px 18px; border-radius: 8px; margin-bottom: 20px; font-size: 0.9rem; color: #92400E; line-height: 1.5;">
                    <i class="fa-solid fa-circle-info" style="color: #F59E0B; margin-right: 6px;"></i>
                    <strong>Action Required in Notion:</strong> Please open your Notion database and ensure <strong>Ready for Manifest</strong> is checked (consent reviewed) and case reference codes match the service. Afterwards, return here and click <strong>Fetch Notion Cases</strong> to refresh and export.
                </div>

                <div style="max-height: 280px; overflow-y: auto; border: 1px solid #E2E8F0; border-radius: 8px;">
                    <table style="width: 100%; border-collapse: collapse; font-size: 0.88rem; text-align: left;">
                        <thead style="background: #F8FAFC; border-bottom: 1px solid #CBD5E1; position: sticky; top: 0; z-index: 2;">
                            <tr>
                                <th style="padding: 10px 14px; font-weight: 600; color: #475569; width: 90px;">Case #</th>
                                <th style="padding: 10px 14px; font-weight: 600; color: #475569; width: 220px;">Reference Tag</th>
                                <th style="padding: 10px 14px; font-weight: 600; color: #475569;">Required Action / Issue</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${rowsHtml}
                        </tbody>
                    </table>
                </div>
            </div>

            <div style="background: #F8FAFC; border-top: 1px solid #E2E8F0; padding: 14px 24px; display: flex; justify-content: flex-end; gap: 10px;">
                <button type="button" class="btn btn-secondary" onclick="closeManifestVerificationModal()" style="padding: 9px 24px; font-weight: 600; font-size: 0.92rem;">
                    Understood
                </button>
            </div>
        </div>
    `;

    modal.style.display = 'flex';
}

function closeManifestVerificationModal() {
    const modal = document.getElementById('manifest-verification-modal');
    if (modal) modal.style.display = 'none';
}
window.closeManifestVerificationModal = closeManifestVerificationModal;

if (exportExcelBtn) {
exportExcelBtn.addEventListener('click', () => {
    if (cases.length === 0) {
        showToast('No case data to export', 'error');
        return;
    }

    const issues = getUnverifiedManifestIssues();
    if (issues.length > 0) {
        showManifestVerificationModal(issues);
        return;
    }

    const invalidCase = cases.find(c => !c.bdo_tag || !c.bdo_tag.trim());
    if (invalidCase) {
        showToast('All cases must have a valid BDO Tag / Reference Number', 'error');
        return;
    }

    const originalBtnHtml = exportExcelBtn.innerHTML;
    exportExcelBtn.disabled = true;
    exportExcelBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Converting to Excel...';

    showLoader(true, 'Generating Excel worksheet...', 'Building case sheets and formatting participant rows.');
    updateStatus('Exporting...', 'fa-circle-notch fa-spin');

    fetch('/api/export', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ cases: cases })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(err => { throw new Error(err.error || 'Export failed') });
        }
        return response.blob();
    })
    .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        const labPrefix = currentLabFilter !== 'ALL' ? `${currentLabFilter.toLowerCase()}_` : '';
        a.download = `manifest_${labPrefix}${new Date().toISOString().slice(0, 10)}.xlsx`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
        
        const shouldAutoDispatch = autoDispatchCheckbox && autoDispatchCheckbox.checked;
        const eligibleCases = cases.filter(c => 
            Array.isArray(c.case_page_ids) && 
            c.case_page_ids.length > 0 &&
            (!c.case_status || c.case_status.toLowerCase().includes('awaiting'))
        );

        if (shouldAutoDispatch && eligibleCases.length > 0) {
            showLoader(true, 'Excel downloaded!', `Updating ${eligibleCases.length} case(s) to 'Dispatched to Laboratory' in Notion...`);
            updateStatus('Updating Notion...', 'fa-circle-notch fa-spin');
            
            fetch('/api/notion/update-status', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    cases: eligibleCases,
                    target_status: 'Dispatched to Laboratory'
                })
            })
            .then(res => res.json())
            .then(updateData => {
                showLoader(false);
                exportExcelBtn.disabled = false;
                exportExcelBtn.innerHTML = originalBtnHtml;
                updateStatus('Ready', 'fa-check');
                if (updateData.success || (updateData.updated && updateData.updated > 0)) {
                    // Update status on local case objects
                    eligibleCases.forEach(c => {
                        c.case_status = 'Dispatched to Laboratory';
                    });
                    renderCards();
                    const failMsg = updateData.failed > 0 ? ` (${updateData.failed} failed)` : '';
                    showToast(`Excel downloaded! Updated ${updateData.updated} case(s) to 'Dispatched to Laboratory' in Notion${failMsg}!`, 'success');
                } else {
                    showToast(`Excel downloaded, but Notion status update failed: ${updateData.error || updateData.message || 'Check connection'}`, 'error');
                }
            })
            .catch(err => {
                showLoader(false);
                exportExcelBtn.disabled = false;
                exportExcelBtn.innerHTML = originalBtnHtml;
                updateStatus('Ready', 'fa-check');
                console.error('Notion update error:', err);
                showToast(`Excel downloaded, but failed to connect to Notion: ${err.message}`, 'error');
            });
        } else {
            showToast(`Excel manifest (${cases.length} cases) downloaded successfully!`, 'success');
            showLoader(false);
            exportExcelBtn.disabled = false;
            exportExcelBtn.innerHTML = originalBtnHtml;
            updateStatus('Ready', 'fa-check');
        }
    })
    .catch(error => {
        console.error('Export error:', error);
        showToast(error.message || 'Error exporting to Excel', 'error');
        showLoader(false);
        exportExcelBtn.disabled = false;
        exportExcelBtn.innerHTML = originalBtnHtml;
        updateStatus('Ready', 'fa-check');
    });
});
}

/* -------------------------------------------------------------
 * UI Workspaces & Rendering
 * ------------------------------------------------------------- */

function renderWorkspace() {
    const src = document.getElementById('source-panel') || sourcePanel;
    const ed = document.getElementById('editor-panel') || editorPanel;
    if (src) src.style.display = 'none';
    if (ed) ed.style.display = 'block';
    
    renderCards();
    showLoader(false);
    updateStatus('Ready', 'fa-check');
}

function renderTestTypeOptions(currentTestType) {
    const standardOptions = [
        "PRIVATE PATERNITY",
        "LEGAL PATERNITY",
        "PRIVATE PATERNITY + MATERNITY",
        "LEGAL PATERNITY + MATERNITY",
        "PRIVATE MATERNITY",
        "LEGAL MATERNITY",
        "PRIVATE PRENATAL PATERNITY",
        "LEGAL PRENATAL PATERNITY",
        "PRIVATE PRENATAL PATERNITY + GENDER TEST",
        "PRENATAL GENDER TEST",
        "Y CHROMOSOME TEST",
        "LEGAL Y CHROMOSOME TEST",
        "GRANDPARENTAGE TEST",
        "LEGAL GRANDPARENTAGE TEST",
        "SIBLINGSHIP",
        "LEGAL SIBLINGSHIP",
        "AVUNCULAR",
        "LEGAL AVUNCULAR",
        "SINGLE PROFILE",
        "LEGAL SINGLE PROFILE",
        "ANCESTRY",
        "LEGAL ANCESTRY"
    ];

    const current = (currentTestType || "").trim().toUpperCase();
    const options = [...standardOptions];
    if (current && !options.includes(current)) {
        options.push(current);
    }

    return options.map(opt => {
        const isSelected = opt === current;
        return `<option value="${escapeHtml(opt)}"${isSelected ? ' selected' : ''}>${escapeHtml(opt)}</option>`;
    }).join('\n');
}

function renderCards() {
    const grid = document.getElementById('cases-grid') || casesGrid;
    const badge = document.getElementById('cases-count-badge') || casesCountBadge;
    if (grid) grid.innerHTML = '';
    if (badge) badge.textContent = cases.length;

    // Update Export button locked/unlocked state
    const exportBtn = document.getElementById('export-excel-btn') || exportExcelBtn;
    if (exportBtn) {
        const unverified = getUnverifiedManifestIssues();
        if (unverified.length > 0) {
            exportBtn.classList.add('btn-export-locked');
            exportBtn.innerHTML = `<i class="fa-solid fa-lock"></i> Export to Excel (${unverified.length} Unverified)`;
            exportBtn.title = `Export Locked: ${unverified.length} case(s) require verification in Notion. Click to view details.`;
        } else {
            exportBtn.classList.remove('btn-export-locked');
            exportBtn.innerHTML = `<i class="fa-solid fa-file-excel"></i> Export to Excel`;
            exportBtn.title = 'Export verified cases to Excel manifest';
        }
    }

    cases.forEach((caseItem, caseIdx) => {
        const card = document.createElement('div');
        card.className = 'case-card';
        card.dataset.idx = caseIdx;

        let partsHtml = '';
        caseItem.participants.forEach((part, partIdx) => {
            partsHtml += `
                <div class="participant-item" data-part-idx="${partIdx}">
                    <div class="participant-item-header">
                        <span class="part-number-badge">Participant #${partIdx + 1}</span>
                        <button class="remove-participant-btn" onclick="removeParticipant(${caseIdx}, ${partIdx})">
                            <i class="fa-solid fa-trash-can"></i> Remove
                        </button>
                    </div>
                    <div class="form-row-grid">
                        <div class="form-group">
                            <label>Name</label>
                            <input type="text" class="form-control" value="${escapeHtml(part.name)}" 
                                oninput="updateParticipantField(${caseIdx}, ${partIdx}, 'name', this.value)" placeholder="Enter name">
                        </div>
                        <div class="form-group">
                            <label>Date of Birth</label>
                            <input type="text" class="form-control" value="${escapeHtml(part.dob)}" 
                                oninput="updateParticipantField(${caseIdx}, ${partIdx}, 'dob', this.value)" placeholder="DDMMYYYY or -">
                        </div>
                    </div>
                    <div class="form-row-grid">
                        <div class="form-group">
                            <label>Role</label>
                            <select class="form-control" onchange="updateParticipantField(${caseIdx}, ${partIdx}, 'role', this.value)">
                                <option value="ALLEGED FATHER" ${part.role === 'ALLEGED FATHER' || part.role === 'FATHER' ? 'selected' : ''}>ALLEGED FATHER</option>
                                <option value="ALLEGED MOTHER" ${part.role === 'ALLEGED MOTHER' ? 'selected' : ''}>ALLEGED MOTHER</option>
                                <option value="MOTHER" ${part.role === 'MOTHER' ? 'selected' : ''}>MOTHER</option>
                                <option value="MOTHER (NOT ALLEGED)" ${part.role === 'MOTHER (NOT ALLEGED)' ? 'selected' : ''}>MOTHER (NOT ALLEGED)</option>
                                <option value="CHILD" ${part.role === 'CHILD' ? 'selected' : ''}>CHILD</option>
                                <option value="ALLEGED GRANDPARENT" ${part.role === 'ALLEGED GRANDPARENT' ? 'selected' : ''}>ALLEGED GRANDPARENT</option>
                                <option value="GRANDCHILD" ${part.role === 'GRANDCHILD' ? 'selected' : ''}>GRANDCHILD</option>
                                <option value="MALE 1" ${part.role === 'MALE 1' ? 'selected' : ''}>MALE 1</option>
                                <option value="MALE 2" ${part.role === 'MALE 2' ? 'selected' : ''}>MALE 2</option>
                                <option value="MALE 3" ${part.role === 'MALE 3' ? 'selected' : ''}>MALE 3</option>
                                <option value="MALE 4" ${part.role === 'MALE 4' ? 'selected' : ''}>MALE 4</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Ethnicity</label>
                            <input type="text" class="form-control" value="${escapeHtml(part.ethnicity || 'AFRICAN')}" 
                                oninput="updateParticipantField(${caseIdx}, ${partIdx}, 'ethnicity', this.value)">
                        </div>
                    </div>
                </div>
            `;
        });

        // Badges for multi-part & lab
        const multipartBadge = caseItem.is_multipart 
            ? `<span class="badge-multipart" title="Merged from ${caseItem.parts_merged.join(' & ')}"><i class="fa-solid fa-link"></i> Merged: ${caseItem.parts_merged.join(' + ')} (${caseItem.participants.length} participants)</span>`
            : '';
        const labBadge = caseItem.receiving_lab && caseItem.receiving_lab !== 'UNKNOWN'
            ? `<span class="badge-lab"><i class="fa-solid fa-flask"></i> ${escapeHtml(caseItem.receiving_lab)}</span>`
            : '';

        // Status badge (e.g. Manifest Awaiting vs Dispatched to Laboratory)
        let statusBadge = '';
        if (caseItem.case_status) {
            const isDispatched = caseItem.case_status.toLowerCase().includes('dispatched');
            const badgeClass = isDispatched ? 'badge-status-dispatched' : 'badge-status-awaiting';
            const iconClass = isDispatched ? 'fa-truck-fast' : 'fa-clock';
            statusBadge = `<span class="${badgeClass}" title="Case status in Notion: ${escapeHtml(caseItem.case_status)}"><i class="fa-solid ${iconClass}"></i> ${escapeHtml(caseItem.case_status)}</span>`;
        }

        // Ready for Manifest review indicator (Amber warning if false, Verified check if true)
        let reviewBadge = '';
        if (caseItem.ready_for_manifest === false) {
            reviewBadge = `<span class="badge-review-pending" title="Review incomplete in Notion: Verify consent forms and review status"><i class="fa-solid fa-triangle-exclamation"></i> Review Pending</span>`;
        } else if (caseItem.ready_for_manifest === true) {
            reviewBadge = `<span class="badge-review-complete" title="Verified & Ready in Notion"><i class="fa-solid fa-circle-check"></i> Verified</span>`;
        }

        card.innerHTML = `
            <div class="case-card-header">
                <div class="case-tag-area">
                    <div style="display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.25rem; flex-wrap: wrap;">
                        <span class="section-label">${escapeHtml(caseItem.section || 'MAIN TABLE')}</span>
                        ${statusBadge}
                        ${reviewBadge}
                        ${multipartBadge}
                        ${labBadge}
                    </div>
                    <h3>Case #${caseIdx + 1}</h3>
                </div>
                <button class="delete-case-btn" onclick="deleteCase(${caseIdx})" title="Delete entire case">
                    <i class="fa-solid fa-trash"></i>
                </button>
            </div>
            
            <div class="form-row-grid">
                <div class="form-group">
                    <label>BDO Tag / Reference Number</label>
                    <input type="text" class="form-control" value="${escapeHtml(caseItem.bdo_tag)}" 
                        oninput="updateCaseField(${caseIdx}, 'bdo_tag', this.value)" placeholder="e.g. CD-OMC-260526-001">
                </div>
                <div class="form-group">
                    <label>Test Type</label>
                    <select class="form-control" onchange="updateCaseField(${caseIdx}, 'test_type', this.value)">
                        ${renderTestTypeOptions(caseItem.test_type)}
                    </select>
                </div>
            </div>
            
            <div class="form-group">
                <label>Special Requests</label>
                <input type="text" class="form-control" value="${escapeHtml(caseItem.special_requests || 'None')}" 
                    oninput="updateCaseField(${caseIdx}, 'special_requests', this.value)">
            </div>
            
            <div class="participants-section">
                <div class="participants-title-row">
                    <h4>Participants (${caseItem.participants.length})</h4>
                </div>
                <div class="participants-list">
                    ${partsHtml}
                </div>
                <button class="add-part-card-btn" onclick="addParticipant(${caseIdx})">
                    <i class="fa-solid fa-user-plus"></i> Add Participant
                </button>
            </div>
        `;
        casesGrid.appendChild(card);
    });
}

/* -------------------------------------------------------------
 * Event Handlers for Live Updating State
 * ------------------------------------------------------------- */

function updateCaseField(caseIdx, field, value) {
    if (cases[caseIdx]) {
        cases[caseIdx][field] = value;
    }
}
window.updateCaseField = updateCaseField;

function updateParticipantField(caseIdx, partIdx, field, value) {
    if (cases[caseIdx] && cases[caseIdx].participants[partIdx]) {
        cases[caseIdx].participants[partIdx][field] = value;
    }
}
window.updateParticipantField = updateParticipantField;

function deleteCase(caseIdx) {
    cases.splice(caseIdx, 1);
    renderCards();
    showToast('Case removed', 'info');
}
window.deleteCase = deleteCase;

function removeParticipant(caseIdx, partIdx) {
    if (cases[caseIdx]) {
        cases[caseIdx].participants.splice(partIdx, 1);
        renderCards();
        showToast('Participant removed', 'info');
    }
}
window.removeParticipant = removeParticipant;

function addParticipant(caseIdx) {
    if (cases[caseIdx]) {
        const testTypeUpper = (cases[caseIdx].test_type || '').toUpperCase();
        const numParts = cases[caseIdx].participants.length;
        let defaultRole = "CHILD";
        
        if (testTypeUpper.includes("Y CHROM") || testTypeUpper.includes("Y-CHROM")) {
            defaultRole = `MALE ${numParts + 1}`;
        } else if (testTypeUpper.includes("GRAND")) {
            defaultRole = numParts === 0 ? "ALLEGED GRANDPARENT" : "GRANDCHILD";
        } else if (numParts === 0) {
            defaultRole = (testTypeUpper.includes("MATERNITY") && !testTypeUpper.includes("PATERNITY")) ? "ALLEGED MOTHER" : "ALLEGED FATHER";
        } else if (numParts === 1 && testTypeUpper.includes("MATERNITY") && testTypeUpper.includes("PATERNITY")) {
            defaultRole = "MOTHER";
        }
            
        cases[caseIdx].participants.push({
            name: '',
            dob: '',
            role: defaultRole,
            ethnicity: 'AFRICAN'
        });
        renderCards();
        showToast('Participant added', 'success');
    }
}
window.addParticipant = addParticipant;

if (addCaseBtn) {
    addCaseBtn.addEventListener('click', () => {
        cases.push({
            section: 'MAIN TABLE',
            bdo_tag: `CD-BDO-${new Date().toISOString().slice(0, 10).replace(/-/g, '')}-001`,
            test_type: 'PRIVATE PATERNITY',
            special_requests: 'None',
            receiving_lab: currentLabFilter !== 'ALL' ? currentLabFilter : 'UNKNOWN',
            is_multipart: false,
            parts_merged: ['Part 1'],
            participants: [
                { name: '', dob: '', role: 'ALLEGED FATHER', ethnicity: 'AFRICAN' },
                { name: '', dob: '', role: 'CHILD', ethnicity: 'AFRICAN' }
            ]
        });
        renderCards();
        showToast('Blank case appended to manifest', 'success');
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    });
}

if (uploadAnotherBtn) {
    uploadAnotherBtn.addEventListener('click', () => {
        cases = [];
        allCases = [];
        if (fileInput) fileInput.value = '';
        if (editorPanel) editorPanel.style.display = 'none';
        if (sourcePanel) sourcePanel.style.display = 'block';
        updateStatus('Ready', 'fa-check');
    });
}

/* -------------------------------------------------------------
 * Utility Helpers
 * ------------------------------------------------------------- */

function showLoader(show, title = 'Processing data...', desc = 'Analyzing participants and formatting manifest records.') {
    const titleEl = document.getElementById('loader-title');
    const descEl = document.getElementById('loader-desc');
    const overlay = document.getElementById('loading-overlay') || loadingOverlay;
    if (titleEl) titleEl.textContent = title;
    if (descEl) descEl.textContent = desc;
    if (overlay) overlay.style.display = show ? 'flex' : 'none';
}

function updateStatus(text, iconClass) {
    const badge = document.getElementById('status-badge') || statusBadge;
    if (badge) {
        badge.style.display = 'inline-flex';
        badge.innerHTML = `<i class="fa-solid ${iconClass}"></i> ${text}`;
    }
}

function showToast(message, type = 'success') {
    let container = document.getElementById('toast-container') || toastContainer;
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let icon = 'fa-circle-check';
    if (type === 'error') icon = 'fa-triangle-exclamation';
    if (type === 'info') icon = 'fa-circle-info';
    
    toast.innerHTML = `
        <i class="fa-solid ${icon} toast-icon"></i>
        <div class="toast-content">${escapeHtml(message)}</div>
    `;
    
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add('toast-fadeout');
        setTimeout(() => {
            if (container && container.contains(toast)) {
                container.removeChild(toast);
            }
        }, 300);
    }, 4500);
}

function escapeHtml(text) {
    if (typeof text !== 'string') return '';
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Window exports for HTML event attributes and cross-script calls
window.updateCaseField = updateCaseField;
window.updateParticipantField = updateParticipantField;
window.deleteCase = deleteCase;
window.removeParticipant = removeParticipant;
window.addParticipant = addParticipant;
window.showToast = showToast;
window.showLoader = showLoader;
window.updateStatus = updateStatus;
window.fetchNotionData = fetchNotionData;
window.filterCasesByLab = filterCasesByLab;
window.renderCards = renderCards;
window.extractCaseDateAndSeq = extractCaseDateAndSeq;
window.sortCases = sortCases;

})();
