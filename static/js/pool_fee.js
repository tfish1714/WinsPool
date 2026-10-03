// Pool fee chip on the wins pool page (slim single line: pot + the caller's own paid status).
// Same-origin fetch sends the httpOnly session_token cookie; the token in
// localStorage (api.js convention) is also sent as a Bearer header when present.
// Payout split and paid counts live on the owner's /player/{id} page, not here.
import { getAuthHeaders, STORAGE_KEYS } from './auth_service.js';

(function () {
    const el = document.getElementById('pool-fee-banner');
    if (!el) return;

    function money(n) {
        return '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    }

    async function load() {
        try {
            const headers = getAuthHeaders();
            let myId = null;
            try {
                myId = localStorage.getItem(STORAGE_KEYS.PLAYER_ID);
            } catch (e) { /* storage unavailable */ }
            const year = el.dataset.year;
            const res = await fetch('/api/pool/status?season=' + encodeURIComponent(year), { headers });
            if (!res.ok) return;
            const d = await res.json();
            if (!d || !(d.entry_fee > 0)) return;

            el.textContent = '';
            const pot = document.createElement('span');
            pot.className = 'pool-chip-pot';
            pot.textContent = 'Pot ' + money(d.total_pot);
            el.appendChild(pot);

            if (d.my_paid !== null && d.my_paid !== undefined) {
                const sep = document.createElement('span');
                sep.className = 'pool-chip-sep';
                sep.textContent = '|';
                el.appendChild(sep);
                const hasId = myId && myId !== 'null';
                const mine = document.createElement(hasId ? 'a' : 'span');
                mine.className = 'pool-chip-mine' + (d.my_paid ? ' is-paid' : '');
                mine.textContent = 'You: ' + (d.my_paid ? 'Paid' : 'Not yet paid');
                if (hasId) mine.href = '/player/' + encodeURIComponent(myId);
                el.appendChild(mine);
            }
            el.hidden = false;
        } catch (e) {
            // Leave the chip hidden on any failure.
        }
    }
    load();
})();
