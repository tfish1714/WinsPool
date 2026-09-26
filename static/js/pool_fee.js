// Pool fee / prize pot banner on the wins pool page.
// Same-origin fetch sends the httpOnly session_token cookie; the token in
// localStorage (api.js convention) is also sent as a Bearer header when present.
(function () {
    const el = document.getElementById('pool-fee-banner');
    if (!el) return;

    function money(n) {
        return '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    }
    function ordinal(n) {
        const s = ['th', 'st', 'nd', 'rd'], v = n % 100;
        return n + (s[(v - 20) % 10] || s[v] || s[0]);
    }
    function add(parent, tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        node.textContent = text;
        parent.appendChild(node);
        return node;
    }

    async function load() {
        try {
            const headers = {};
            try {
                const token = localStorage.getItem('nfl_wins_token');
                if (token) headers['Authorization'] = 'Bearer ' + token;
            } catch (e) { /* storage unavailable */ }
            const year = el.dataset.year;
            const res = await fetch('/api/pool/status?season=' + encodeURIComponent(year), { headers });
            if (!res.ok) return;
            const d = await res.json();
            if (!d || !(d.entry_fee > 0)) return;

            el.textContent = '';
            const left = add(el, 'div', '', '');
            add(left, 'div', 'pool-fee-pot', 'Prize pot: ' + money(d.total_pot));
            add(left, 'div', 'pool-fee-sub', 'Paid: ' + d.paid_count + ' of ' + d.total_count +
                ' (' + money(d.entry_fee) + ' entry)');

            const list = add(el, 'ul', 'pool-fee-split', '');
            d.payouts.forEach(function (p) {
                // Server supplies the label ("1st", "Last place"); pot balance is admin-only, never shown here.
                const label = p.label || (typeof p.place === 'number' ? ordinal(p.place) : 'Last place');
                add(list, 'li', '', label + ': ' + money(p.amount));
            });

            if (d.my_paid !== null && d.my_paid !== undefined) {
                add(el, 'div', 'pool-fee-mine' + (d.my_paid ? ' is-paid' : ''),
                    'Your entry: ' + (d.my_paid ? 'Paid' : 'Not yet paid'));
            }
            el.hidden = false;
        } catch (e) {
            // Leave the banner hidden on any failure.
        }
    }
    load();
})();
