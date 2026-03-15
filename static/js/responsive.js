/**
 * responsive.js -- Responsive helpers for mobile UX.
 *
 * 1. Navigation Drawer controller:
 *    Opens/closes the mobile side drawer with backdrop.
 *
 * 2. Bottom Sheet controller:
 *    Converts hover-only title/tooltip attributes on .history-chip elements
 *    into a tap-to-open Bottom Sheet on touch devices (<= 768px).
 *
 * 3. Weekbyweek label injector:
 *    For pandas-generated tables inside .weekbyweek-wrap, injects data-label
 *    attributes on each <td> so CSS can render them as mobile row labels via
 *    the ::before pseudo-element.
 */

(function () {
    'use strict';

    /* ------------------------------------------------------------------
       Navigation Drawer Controller
       ------------------------------------------------------------------ */

    function initDrawer() {
        var toggle = document.getElementById('drawer-toggle');
        var drawer = document.getElementById('nav-drawer');
        var overlay = document.getElementById('nav-drawer-overlay');
        var closeBtn = document.getElementById('drawer-close');

        if (!toggle || !drawer || !overlay) return;

        function openDrawer() {
            drawer.classList.add('open');
            overlay.classList.add('open');
            document.body.style.overflow = 'hidden';
            // Re-initialize lucide icons inside the drawer
            if (typeof lucide !== 'undefined') {
                lucide.createIcons({ nodes: [drawer] });
            }
        }

        function closeDrawer() {
            drawer.classList.remove('open');
            overlay.classList.remove('open');
            document.body.style.overflow = '';
        }

        toggle.addEventListener('click', openDrawer);
        overlay.addEventListener('click', closeDrawer);
        if (closeBtn) {
            closeBtn.addEventListener('click', closeDrawer);
        }

        // Sync admin link visibility from desktop nav to drawer
        var desktopAdmin = document.getElementById('admin-nav-link');
        var drawerAdmin = document.getElementById('admin-nav-link-drawer');
        if (desktopAdmin && drawerAdmin) {
            var observer = new MutationObserver(function () {
                drawerAdmin.style.display = desktopAdmin.style.display;
            });
            observer.observe(desktopAdmin, { attributes: true, attributeFilter: ['style'] });
        }

        // Sync user identity to drawer footer
        syncDrawerUser();
    }

    function syncDrawerUser() {
        var desktopNickname = document.getElementById('user-nickname-display');
        var drawerFooter = document.getElementById('drawer-user-identity');
        if (!desktopNickname || !drawerFooter) return;

        var observer = new MutationObserver(function () {
            var name = desktopNickname.textContent.trim();
            if (name) {
                drawerFooter.innerHTML =
                    '<div class="drawer-user-name">' + name + '</div>' +
                    '<button class="drawer-logout-btn" id="drawer-logout-btn">Logout</button>';
                var btn = document.getElementById('drawer-logout-btn');
                if (btn) {
                    btn.addEventListener('click', function () {
                        var mainLogout = document.getElementById('logout-btn');
                        if (mainLogout) mainLogout.click();
                    });
                }
            }
        });
        observer.observe(desktopNickname, { childList: true, characterData: true, subtree: true });
    }

    /* ------------------------------------------------------------------
       Bottom Sheet Controller
       ------------------------------------------------------------------ */

    /** Create the Bottom Sheet DOM elements (overlay + panel) once. */
    function createBottomSheet() {
        if (document.querySelector('.bottom-sheet')) return;

        var overlay = document.createElement('div');
        overlay.className = 'bottom-sheet-overlay';
        overlay.id = 'bs-overlay';
        overlay.addEventListener('click', closeBottomSheet);

        var sheet = document.createElement('div');
        sheet.className = 'bottom-sheet';
        sheet.id = 'bs-sheet';
        sheet.innerHTML =
            '<div class="bottom-sheet__handle"></div>' +
            '<div class="bottom-sheet__title" id="bs-title"></div>' +
            '<div class="bottom-sheet__content" id="bs-content"></div>';

        document.body.appendChild(overlay);
        document.body.appendChild(sheet);
    }

    /** Open the Bottom Sheet with the given title and pipe-delimited content. */
    function openBottomSheet(title, rawContent) {
        var overlay = document.getElementById('bs-overlay');
        var sheet = document.getElementById('bs-sheet');
        var titleEl = document.getElementById('bs-title');
        var contentEl = document.getElementById('bs-content');

        if (!overlay || !sheet) return;

        titleEl.textContent = title || '';

        // Parse pipe-delimited content into detail rows
        var lines = rawContent.split('|').filter(function (l) { return l.trim(); });
        var html = '';
        for (var i = 0; i < lines.length; i++) {
            html += '<div class="detail-row">' + lines[i].trim() + '</div>';
        }
        contentEl.innerHTML = html;

        overlay.classList.add('active');
        sheet.classList.add('active');

        // Prevent body scroll while sheet is open
        document.body.style.overflow = 'hidden';
    }

    /** Close the Bottom Sheet. */
    function closeBottomSheet() {
        var overlay = document.getElementById('bs-overlay');
        var sheet = document.getElementById('bs-sheet');

        if (overlay) overlay.classList.remove('active');
        if (sheet) sheet.classList.remove('active');

        document.body.style.overflow = '';
    }

    /** Bind tap events on all [data-bs-content] elements. */
    function bindBottomSheetTriggers() {
        var chips = document.querySelectorAll('[data-bs-content]');
        for (var i = 0; i < chips.length; i++) {
            chips[i].style.cursor = 'pointer';
            // Ensure 44px minimum touch target
            chips[i].style.minHeight = '44px';
            chips[i].style.display = 'inline-flex';
            chips[i].style.alignItems = 'center';
            chips[i].addEventListener('click', function () {
                var title = this.getAttribute('data-bs-title') || '';
                var content = this.getAttribute('data-bs-content') || '';
                if (window.innerWidth <= 768) {
                    openBottomSheet(title, content);
                }
            });
        }
    }

    /* ------------------------------------------------------------------
       Weekbyweek Label Injector
       ------------------------------------------------------------------ */

    /** Inject data-label attributes on pandas-generated table cells. */
    function injectWeekbyweekLabels() {
        var wrap = document.querySelector('.weekbyweek-wrap');
        if (!wrap) return;

        var table = wrap.querySelector('.table-container table');
        if (!table) return;

        var headers = table.querySelectorAll('thead th');
        var headerTexts = [];
        for (var i = 0; i < headers.length; i++) {
            headerTexts.push(headers[i].textContent.trim());
        }

        var rows = table.querySelectorAll('tbody tr');
        for (var r = 0; r < rows.length; r++) {
            var cells = rows[r].querySelectorAll('td');
            for (var c = 0; c < cells.length; c++) {
                if (c < headerTexts.length) {
                    cells[c].setAttribute('data-label', headerTexts[c]);
                }
            }
        }
    }

    /* ------------------------------------------------------------------
       Initialization
       ------------------------------------------------------------------ */

    document.addEventListener('DOMContentLoaded', function () {
        initDrawer();
        createBottomSheet();
        bindBottomSheetTriggers();
        injectWeekbyweekLabels();
    });

})();
