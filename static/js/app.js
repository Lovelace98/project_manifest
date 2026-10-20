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
const forceFetchNotionBtn = document.getElementById('force-fetch-notion-btn');
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

function fetchNotionData(forceRefresh = false) {
    const title = forceRefresh ? 'Querying Notion Live...' : 'Fetching Manifest Batch from Notion...';
    showLoader(true, title, 'Querying database, consolidating multi-part cases (>4 participants), and arranging parents before children.');
    updateStatus('Connecting to Notion...', 'fa-circle-notch fa-spin');

    fetch('/api/notion/fetch', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ filter_mode: 'manifest_batch', force_refresh: forceRefresh })
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
        filterCasesByLab('ALL');
        if (autoDispatchLabel) autoDispatchLabel.style.display = 'inline-flex';
        if (autoDispatchCheckbox) autoDispatchCheckbox.checked = true;
        renderWorkspace();

        const multipartCount = allCases.filter(c => c.is_multipart).length;
        const cacheNote = data.from_cache ? ' (cached)' : '';
        const msg = multipartCount > 0 
            ? `Loaded ${allCases.length} cases from Notion${cacheNote} (${multipartCount} multi-part cases merged)!`
            : `Loaded ${allCases.length} cases from Notion${cacheNote}!`;
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
    fetchNotionBtn.addEventListener('click', () => fetchNotionData(false));
}
if (forceFetchNotionBtn) {
    forceFetchNotionBtn.addEventListener('click', () => fetchNotionData(true));
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
 * Export to Excel Action
 * ------------------------------------------------------------- */

if (exportExcelBtn) {
exportExcelBtn.addEventListener('click', () => {
    if (cases.length === 0) {
        showToast('No case data to export', 'error');
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

function renderCards() {
    const grid = document.getElementById('cases-grid') || casesGrid;
    const badge = document.getElementById('cases-count-badge') || casesCountBadge;
    if (grid) grid.innerHTML = '';
    if (badge) badge.textContent = cases.length;

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
                        <option value="PRIVATE PATERNITY" ${caseItem.test_type === 'PRIVATE PATERNITY' ? 'selected' : ''}>PRIVATE PATERNITY</option>
                        <option value="LEGAL PATERNITY" ${caseItem.test_type === 'LEGAL PATERNITY' ? 'selected' : ''}>LEGAL PATERNITY</option>
                        <option value="PRIVATE MATERNITY" ${caseItem.test_type === 'PRIVATE MATERNITY' ? 'selected' : ''}>PRIVATE MATERNITY</option>
                        <option value="LEGAL MATERNITY" ${caseItem.test_type === 'LEGAL MATERNITY' ? 'selected' : ''}>LEGAL MATERNITY</option>
                        <option value="Y CHROMOSOME TEST" ${caseItem.test_type === 'Y CHROMOSOME TEST' ? 'selected' : ''}>Y CHROMOSOME TEST</option>
                        <option value="LEGAL Y CHROMOSOME TEST" ${caseItem.test_type === 'LEGAL Y CHROMOSOME TEST' ? 'selected' : ''}>LEGAL Y CHROMOSOME TEST</option>
                        <option value="GRANDPARENTAGE TEST" ${caseItem.test_type === 'GRANDPARENTAGE TEST' ? 'selected' : ''}>GRANDPARENTAGE TEST</option>
                        <option value="LEGAL GRANDPARENTAGE TEST" ${caseItem.test_type === 'LEGAL GRANDPARENTAGE TEST' ? 'selected' : ''}>LEGAL GRANDPARENTAGE TEST</option>
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
        
        if (testTypeUpper.includes("Y CHROM")) {
            defaultRole = `MALE ${numParts + 1}`;
        } else if (testTypeUpper.includes("GRAND")) {
            defaultRole = numParts === 0 ? "ALLEGED GRANDPARENT" : "GRANDCHILD";
        } else if (numParts === 0) {
            defaultRole = testTypeUpper.includes("MATERNITY") ? "ALLEGED MOTHER" : "ALLEGED FATHER";
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
