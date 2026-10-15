/* =============================================================
   Blueprint DNA Operations Super App - Navigation & Hub Controller
   ============================================================= */

"use strict";

document.addEventListener('DOMContentLoaded', () => {
    initAppNavigation();
    initMobileSidebar();
    initSystemStatusMonitor();
});

function initAppNavigation() {
    const navButtons = document.querySelectorAll('.nav-item-btn[data-target]');
    const panels = document.querySelectorAll('.workspace-panel');
    const headerTitle = document.getElementById('active-app-title');
    const headerIcon = document.getElementById('active-app-icon');

    const appMeta = {
        'manifest': {
            title: 'Manifest Portal — Notion Sync & Export',
            icon: 'fa-solid fa-table-list'
        },
        'reports': {
            title: 'Report Bot — Eurofins, UFC & HK Rebranding',
            icon: 'fa-solid fa-file-shield'
        },
        'settings': {
            title: 'System Settings & Service Health',
            icon: 'fa-solid fa-sliders'
        }
    };

    function activateApp(appName, updateUrl = true) {
        if (!appMeta[appName]) appName = 'manifest';

        // Update nav items
        navButtons.forEach(btn => {
            if (btn.dataset.target === appName) {
                btn.classList.add('active');
            } else {
                btn.classList.remove('active');
            }
        });

        // Toggle workspace panels
        panels.forEach(panel => {
            if (panel.id === `panel-${appName}`) {
                panel.classList.add('active');
                panel.style.display = 'block';
            } else {
                panel.classList.remove('active');
                panel.style.display = 'none';
            }
        });

        // Update header details
        if (headerTitle && appMeta[appName]) {
            headerTitle.textContent = appMeta[appName].title;
        }
        if (headerIcon && appMeta[appName]) {
            headerIcon.className = appMeta[appName].icon;
        }

        // Close mobile drawer if open
        const sidebar = document.getElementById('app-sidebar');
        if (sidebar && sidebar.classList.contains('mobile-open')) {
            sidebar.classList.remove('mobile-open');
        }

        // Update browser URL
        if (updateUrl) {
            const targetPath = appName === 'manifest' ? '/' : `/${appName}`;
            if (window.location.pathname !== targetPath) {
                window.history.pushState({ app: appName }, '', targetPath);
            }
        }
    }

    // Attach click listeners to sidebar buttons
    navButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const target = btn.dataset.target;
            activateApp(target, true);
        });
    });

    // Handle browser back/forward buttons
    window.addEventListener('popstate', (e) => {
        const stateApp = e.state ? e.state.app : null;
        if (stateApp) {
            activateApp(stateApp, false);
        } else {
            const path = window.location.pathname.replace(/^\//, '');
            activateApp(path || 'manifest', false);
        }
    });

    // Check initial route on load
    const initialPath = window.location.pathname.replace(/^\//, '');
    if (initialPath === 'reports') {
        activateApp('reports', false);
    } else if (initialPath === 'settings') {
        activateApp('settings', false);
    } else {
        activateApp('manifest', false);
    }
}

function initMobileSidebar() {
    const toggleBtn = document.getElementById('mobile-toggle-btn');
    const sidebar = document.getElementById('app-sidebar');

    if (toggleBtn && sidebar) {
        toggleBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            sidebar.classList.toggle('mobile-open');
        });

        // Close on outside click
        document.addEventListener('click', (e) => {
            if (sidebar.classList.contains('mobile-open') && !sidebar.contains(e.target) && e.target !== toggleBtn) {
                sidebar.classList.remove('mobile-open');
            }
        });
    }
}

function initSystemStatusMonitor() {
    async function checkStatus() {
        try {
            const res = await fetch('/api/system/status');
            if (!res.ok) return;
            const data = await res.json();

            const notionBadge = document.getElementById('notion-status-badge');
            if (notionBadge && data.notion) {
                const isReady = data.notion.status === 'ready';
                notionBadge.className = `service-pill ${isReady ? 'pill-ready' : 'pill-issue'}`;
                notionBadge.innerHTML = `<span class="pill-dot"></span> ${isReady ? 'Ready' : 'Issue'}`;
            }

            const loBadge = document.getElementById('libreoffice-status-badge');
            if (loBadge && data.libreoffice) {
                const isReady = data.libreoffice.status === 'ready';
                loBadge.className = `service-pill ${isReady ? 'pill-ready' : 'pill-issue'}`;
                loBadge.innerHTML = `<span class="pill-dot"></span> ${isReady ? 'Ready' : 'Issue'}`;
            }
        } catch {
            // Retain existing state on network hiccup
        }
    }

    checkStatus();
    setInterval(checkStatus, 30000);
}
