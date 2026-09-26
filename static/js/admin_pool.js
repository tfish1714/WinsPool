/**
 * admin_pool.js - Pool tab for the Admin Panel.
 *
 * Per-season entry fee and dollar payouts (place 1..10 or "last"). All DOM is
 * built with createElement/textContent; server data is never fed to innerHTML.
 */

const POOL_MAX_PAYOUTS = 10;
let _poolReady = false;
let _poolMembers = 0;
let _poolLoadToken = 0;

function _poolHeaders(json) {
    const headers = {};
    try {
        const token = localStorage.getItem('nfl_wins_token');
        if (token) headers['Authorization'] = 'Bearer ' + token;
    } catch (e) { /* storage unavailable; cookie auth still applies */ }
    if (json) headers['Content-Type'] = 'application/json';
    return headers;
}

function _poolMoney(n) {
    return '$' + Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function _poolEl(id) { return document.getElementById(id); }

function _poolStatus(msg, isError) {
    const el = _poolEl('pool-status');
    if (!el) return;
    el.textContent = msg || '';
    el.style.color = isError ? 'var(--accent-red)' : 'var(--text-secondary)';
}

function _poolAddRow(place, amount) {
    const rows = _poolEl('pool-payout-rows');
    if (!rows) return;
    const row = document.createElement('div');
    row.className = 'pool-payout-row';

    const sel = document.createElement('select');
    sel.className = 'admin-input pool-place';
    sel.setAttribute('aria-label', 'Payout place');
    for (let i = 1; i <= POOL_MAX_PAYOUTS; i++) {
        const opt = document.createElement('option');
        opt.value = String(i);
        opt.textContent = 'Place ' + i;
        sel.appendChild(opt);
    }
    const lastOpt = document.createElement('option');
    lastOpt.value = 'last';
    lastOpt.textContent = 'Last place';
    sel.appendChild(lastOpt);
    sel.value = String(place);
    sel.addEventListener('change', _poolUpdateSummary);

    const amt = document.createElement('input');
    amt.type = 'number';
    amt.min = '0';
    amt.step = '0.01';
    amt.className = 'admin-input pool-amount';
    amt.setAttribute('aria-label', 'Payout amount in dollars');
    amt.value = amount === undefined || amount === null ? '' : String(amount);
    amt.addEventListener('input', _poolUpdateSummary);

    const rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'admin-tab-btn pool-remove';
    rm.textContent = 'Remove';
    rm.addEventListener('click', () => { row.remove(); _poolUpdateSummary(); });

    row.appendChild(sel);
    row.appendChild(amt);
    row.appendChild(rm);
    rows.appendChild(row);
}

function _poolCollect() {
    const fee = parseFloat(_poolEl('pool-entry-fee').value);
    const payouts = [];
    document.querySelectorAll('#pool-payout-rows .pool-payout-row').forEach(row => {
        const placeRaw = row.querySelector('.pool-place').value;
        const amount = parseFloat(row.querySelector('.pool-amount').value);
        payouts.push({
            place: placeRaw === 'last' ? 'last' : parseInt(placeRaw, 10),
            amount: isNaN(amount) ? 0 : amount,
        });
    });
    return { fee: isNaN(fee) ? 0 : fee, payouts };
}

function _poolUpdateSummary() {
    const el = _poolEl('pool-summary');
    if (!el) return;
    const { fee, payouts } = _poolCollect();
    const pot = fee * _poolMembers;
    const total = payouts.reduce((s, p) => s + p.amount, 0);
    const balance = Math.round((pot - total) * 100) / 100;
    let bal = 'Balanced';
    if (balance > 0) bal = 'Unallocated ' + _poolMoney(balance);
    else if (balance < 0) bal = 'Over-allocated ' + _poolMoney(Math.abs(balance));
    el.textContent = 'Members: ' + _poolMembers + ' | Pot: ' + _poolMoney(pot) +
        ' | Payouts: ' + _poolMoney(total) + ' | ' + bal;
}

function _poolRender(cfg) {
    _poolMembers = cfg.member_count || 0;
    _poolEl('pool-entry-fee').value = String(cfg.entry_fee);
    _poolEl('pool-payout-rows').textContent = '';
    (cfg.payouts || []).forEach(p => _poolAddRow(p.place, p.amount));
    const note = _poolEl('pool-default-note');
    if (note) note.textContent = cfg.is_default ? 'Using default settings (not yet saved for this season).' : '';
    _poolUpdateSummary();
}

async function _poolLoad(season) {
    // Token guards against a slow response for a previously selected season
    // overwriting the form after the admin has switched seasons.
    const token = ++_poolLoadToken;
    _poolStatus('Loading...');
    try {
        const res = await fetch('/api/admin/pool/config?season=' + encodeURIComponent(season),
            { headers: _poolHeaders(false), credentials: 'same-origin' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        if (token !== _poolLoadToken) return;
        _poolRender(data);
        _poolStatus('');
    } catch (e) {
        if (token !== _poolLoadToken) return;
        _poolStatus('Could not load pool settings.', true);
    }
}

function _poolFirstUnusedPlace() {
    const used = new Set();
    document.querySelectorAll('#pool-payout-rows .pool-place').forEach(s => used.add(s.value));
    for (let i = 1; i <= POOL_MAX_PAYOUTS; i++) {
        if (!used.has(String(i))) return i;
    }
    return used.has('last') ? POOL_MAX_PAYOUTS : 'last';
}

function _poolDuplicatePlace(payouts) {
    const seen = new Set();
    for (const p of payouts) {
        if (seen.has(p.place)) return p.place;
        seen.add(p.place);
    }
    return null;
}

async function _poolErrorMessage(res) {
    let detail = '';
    try {
        const body = await res.json();
        const d = body && body.detail;
        if (typeof d === 'string') detail = d;
        else if (Array.isArray(d) && d.length) detail = d.map(x => x.msg || '').filter(Boolean).join('; ');
        else if (body && typeof body.error === 'string') detail = body.error;
    } catch (e) { /* no JSON body */ }
    return 'Invalid settings' + (detail ? ': ' + detail : '. Check payouts and amounts.');
}

async function _poolSave() {
    const sel = _poolEl('pool-season-select');
    const season = parseInt(sel.value, 10);
    if (isNaN(season)) { _poolStatus('Choose a season first.', true); return; }
    const { fee, payouts } = _poolCollect();
    if (!payouts.length) { _poolStatus('Add at least one payout before saving.', true); return; }
    const dup = _poolDuplicatePlace(payouts);
    if (dup !== null) {
        _poolStatus('Each place can only be used once (duplicate: ' +
            (dup === 'last' ? 'Last place' : 'Place ' + dup) + ').', true);
        return;
    }
    const btn = _poolEl('pool-save-btn');
    btn.disabled = true;
    _poolStatus('Saving...');
    try {
        const res = await fetch('/api/admin/pool/config', {
            method: 'POST',
            headers: _poolHeaders(true),
            credentials: 'same-origin',
            body: JSON.stringify({ season, entryFee: fee, payouts }),
        });
        if (!res.ok) {
            _poolStatus(res.status === 422 || res.status === 400
                ? await _poolErrorMessage(res)
                : 'Save failed (HTTP ' + res.status + ').', true);
            return;
        }
        if (sel.value !== String(season)) return; // admin switched seasons mid-save
        _poolRender(await res.json());
        _poolStatus('Saved.');
    } catch (e) {
        _poolStatus('Save failed.', true);
    } finally {
        btn.disabled = false;
    }
}

async function _poolInit() {
    if (_poolReady) return;
    _poolReady = true;
    const sel = _poolEl('pool-season-select');
    try {
        const res = await fetch('/api/admin/seasons', { headers: _poolHeaders(false), credentials: 'same-origin' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const { seasons } = await res.json();
        sel.textContent = '';
        seasons.forEach(s => {
            const opt = document.createElement('option');
            opt.value = String(s);
            opt.textContent = s + ' Season';
            sel.appendChild(opt);
        });
        if (seasons.length) _poolLoad(seasons[0]);
        else _poolStatus('No seasons found.', true);
    } catch (e) {
        _poolReady = false;
        _poolStatus('Could not load seasons.', true);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const tabBtn = document.querySelector('.admin-tabs .tab-btn[data-tab="pool-section"]');
    if (!tabBtn || !_poolEl('pool-section')) return;
    tabBtn.addEventListener('click', _poolInit);
    _poolEl('pool-season-select').addEventListener('change', e => { if (e.target.value) _poolLoad(e.target.value); });
    _poolEl('pool-entry-fee').addEventListener('input', _poolUpdateSummary);
    _poolEl('pool-add-payout').addEventListener('click', () => {
        const rows = document.querySelectorAll('#pool-payout-rows .pool-payout-row').length;
        if (rows >= POOL_MAX_PAYOUTS) { _poolStatus('At most ' + POOL_MAX_PAYOUTS + ' payouts.', true); return; }
        _poolAddRow(_poolFirstUnusedPlace(), '');
        _poolUpdateSummary();
    });
    _poolEl('pool-save-btn').addEventListener('click', _poolSave);
});
