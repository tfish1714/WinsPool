// Unpaid entry notices on the wins pool page, driven entirely by GET /api/pool/unpaid.
// The server returns names only for admins or in the public/banner stages, so nothing
// here can reveal data by being edited. Failure or the "off" stage renders nothing.
import { getAuthHeaders } from './auth_service.js';

(function () {
    const chip = document.getElementById('pool-fee-banner');
    if (!chip) return;
    const DISMISS_KEY = 'nfl_wins_unpaid_dismissed';
    const NAME_SELECTORS = ['.wp-leader-name', '.wp-row-name', '.standings-stacked-card__name'];
    let unpaidIds = new Set();

    function money(n) {
        return '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    }

    function dismissed() {
        try { return sessionStorage.getItem(DISMISS_KEY) === '1'; } catch (e) { return false; }
    }

    function rememberDismiss() {
        try { sessionStorage.setItem(DISMISS_KEY, '1'); } catch (e) { /* storage unavailable */ }
    }

    // Idempotent: a name box that already holds a pill is skipped, so re-running this after
    // the 30s standings refresh (or from the observer below) never duplicates pills.
    function applyPills() {
        if (!unpaidIds.size) return;
        document.querySelectorAll('[data-player-id]').forEach(card => {
            if (!unpaidIds.has(String(card.dataset.playerId))) return;
            NAME_SELECTORS.forEach(sel => {
                const nameBox = card.querySelector(sel);
                if (!nameBox || nameBox.querySelector('.unpaid-pill')) return;
                const pill = document.createElement('span');
                pill.className = 'unpaid-pill';
                pill.textContent = 'Unpaid';
                nameBox.appendChild(pill);
            });
        });
    }

    function noticeHost() {
        let host = document.getElementById('unpaid-notice-host');
        if (!host) {
            host = document.createElement('div');
            host.id = 'unpaid-notice-host';
            const header = chip.closest('header');
            (header || chip).insertAdjacentElement('afterend', host);
        }
        return host;
    }

    function showNudge(amount) {
        if (dismissed()) return;
        const host = noticeHost();
        host.textContent = '';
        const strip = document.createElement('div');
        strip.className = 'unpaid-notice';
        strip.setAttribute('role', 'status');
        const text = document.createElement('span');
        text.textContent = 'Your ' + money(amount) + ' entry is still unpaid.';
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'unpaid-notice__close';
        close.setAttribute('aria-label', 'Dismiss reminder');
        close.textContent = 'Dismiss';
        close.addEventListener('click', () => { rememberDismiss(); host.textContent = ''; });
        strip.appendChild(text);
        strip.appendChild(close);
        host.appendChild(strip);
    }

    function showBanner(names) {
        if (!names.length) return;
        const host = noticeHost();
        host.textContent = '';
        const line = document.createElement('div');
        line.className = 'unpaid-notice';
        line.textContent = 'Still owed: ' + names.join(', ');
        host.appendChild(line);
    }

    async function load() {
        try {
            const res = await fetch('/api/pool/unpaid?season=' + encodeURIComponent(chip.dataset.year),
                { headers: getAuthHeaders(), credentials: 'same-origin' });
            if (!res.ok) return;
            const d = await res.json();
            if (!d || d.stage === 'off') return;
            if (d.stage === 'nudge' && d.me_unpaid) showNudge(d.amount);
            if (d.stage === 'public' || d.stage === 'banner') {
                unpaidIds = new Set((d.unpaid || []).map(u => String(u.playerId)));
                applyPills();
                new MutationObserver(applyPills).observe(document.body, { childList: true, subtree: true });
            }
            if (d.stage === 'banner') showBanner((d.unpaid || []).map(u => u.name));
        } catch (e) {
            // Render nothing on any failure.
        }
    }
    load();
})();
