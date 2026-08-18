/**
 * admin_pattern_scanner.js — Pattern Scanner (automated angle miner) for the
 * admin Betting tab. Fetches GET /api/admin/betting/scan and renders two
 * walk-forward-validated leaderboards (ATS, straight-up). Each row's "Apply
 * to filters" button hands its conditions to admin_betting.js's
 * BettingScreener so the admin can immediately see which of the current
 * week's games it flags.
 */

import { AuthService } from './auth_service.js';
import { getBettingScreener } from './admin_betting.js';

class PatternScanner {
    constructor() {
        this._minSample = document.getElementById('scanner-min-sample');
        this._testSeasons = document.getElementById('scanner-test-seasons');
        this._includePairs = document.getElementById('scanner-include-pairs');
        this._heldUpOnly = document.getElementById('scanner-held-up-only');
        this._runBtn = document.getElementById('scanner-run-btn');
        this._baselineEl = document.getElementById('scanner-baseline');
        this._atsEl = document.getElementById('scanner-ats-wrap');
        this._suEl = document.getElementById('scanner-su-wrap');
        this._atsResults = [];
        this._suResults = [];

        this._runBtn?.addEventListener('click', () => this._run());
        this._heldUpOnly?.addEventListener('change', () => this._render());
    }

    _formatCondition(c) {
        if (c.min != null) return `${c.label} ≥ ${c.min}`;
        return `${c.label} ≤ ${c.max}`;
    }

    _formatConditions(conditions) {
        return conditions.map(c => this._formatCondition(c)).join(' AND ');
    }

    _formatRate(rate) {
        return rate == null ? '—' : `${(rate * 100).toFixed(1)}%`;
    }

    _formatHeldUp(v) {
        if (v === true) return '✅';
        if (v === false) return '❌';
        return '—';
    }

    async _run() {
        this._baselineEl.innerHTML = '<p style="color: var(--text-secondary);">Scanning…</p>';
        this._atsEl.innerHTML = '';
        this._suEl.innerHTML = '';

        try {
            const token = AuthService.getToken();
            const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
            const params = new URLSearchParams();
            if (this._minSample.value !== '') params.set('min_sample', this._minSample.value);
            if (this._testSeasons.value !== '') params.set('test_seasons', this._testSeasons.value);
            params.set('include_pairs', this._includePairs.checked ? 'true' : 'false');

            const resp = await fetch(`/api/admin/betting/scan?${params.toString()}`, { headers });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                this._baselineEl.innerHTML = `<p style="color: var(--accent-red);">${err.error || 'Failed to load.'}</p>`;
                return;
            }
            const data = await resp.json();
            this._atsResults = data.ats_leaderboard || [];
            this._suResults = data.su_leaderboard || [];
            this._renderBaseline(data);
            this._render();
        } catch (e) {
            this._baselineEl.innerHTML = `<p style="color: var(--accent-red);">Failed to load: ${e.message}</p>`;
        }
    }

    _renderBaseline(data) {
        const b = data.baseline || {};
        const fmt = x => (x && x.rate != null) ? `${(x.rate * 100).toFixed(1)}% (n=${x.n})` : '—';
        const train = data.train_seasons || [];
        const test = data.test_seasons || [];
        const trainLabel = train.length ? `${train[0]}–${train[train.length - 1]}` : '—';
        const testLabel = test.length ? test.join(', ') : 'none';
        this._baselineEl.innerHTML = `
            <div style="display: flex; gap: 1.5rem; flex-wrap: wrap; align-items: center; font-size: 0.85rem; color: var(--text-secondary);">
                <div>Favorite ATS cover: <strong>${fmt(b.favorite_ats_cover_pct)}</strong></div>
                <div>Favorite SU win rate: <strong>${fmt(b.favorite_su_win_pct)}</strong></div>
                <div>Home SU win rate: <strong>${fmt(b.home_su_win_pct)}</strong></div>
                <div>Train seasons: ${trainLabel} &middot; Test seasons: ${testLabel}</div>
            </div>`;
    }

    _renderTable(el, results, rateLabel) {
        const heldUpOnly = this._heldUpOnly.checked;
        const filtered = heldUpOnly ? results.filter(r => r.held_up === true) : results;
        if (!filtered.length) {
            el.innerHTML = '<p style="color: var(--text-secondary);">No combos found.</p>';
            return;
        }
        const rows = filtered.map((r, i) => `
            <tr>
                <td>${this._formatConditions(r.conditions)}</td>
                <td>${r.train_n}</td>
                <td>${this._formatRate(r.train_rate)}</td>
                <td>${r.test_n}</td>
                <td>${this._formatRate(r.test_rate)}</td>
                <td>${this._formatHeldUp(r.held_up)}</td>
                <td><button class="btn-secondary scanner-apply-btn" data-idx="${i}" style="padding: 0.2rem 0.6rem; font-size: 0.75rem;">Apply to filters</button></td>
            </tr>`).join('');
        el.innerHTML = `<table class="admin-table">
            <thead><tr>
                <th>Conditions</th><th>Train N</th><th>Train ${rateLabel}</th>
                <th>Test N</th><th>Test ${rateLabel}</th><th>Held Up</th><th></th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
        el.querySelectorAll('.scanner-apply-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const idx = parseInt(btn.dataset.idx, 10);
                getBettingScreener()?.applyCombo(filtered[idx].conditions);
            });
        });
    }

    _render() {
        this._renderTable(this._atsEl, this._atsResults, 'Cover%');
        this._renderTable(this._suEl, this._suResults, 'Win%');
    }
}

document.addEventListener('DOMContentLoaded', () => new PatternScanner());
