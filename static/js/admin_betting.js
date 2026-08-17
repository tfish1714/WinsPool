/**
 * admin_betting.js — Betting Angle Screener for the Admin panel.
 *
 * Backtests simple Elo/spread filters (angles) against history and shows
 * which games in a chosen week currently match. Fetches
 * GET /api/admin/betting/screen. Self-contained, like admin_elo.js --
 * lazy-loads its data on first click of the Betting tab.
 */

import { AuthService } from './auth_service.js';

const PREBUILT_ANGLES = {
    home_dog:      { side: 'home', fav_dog: 'dog' },
    away_favorite: { side: 'away', fav_dog: 'favorite' },
    big_favorite:  { side: 'any',  fav_dog: 'favorite', spread_min: 10 },
    big_underdog:  { side: 'any',  fav_dog: 'dog',       spread_min: 10 },
};

class BettingScreener {
    constructor() {
        this._loaded = false;
        this._sortKey = 'week';
        this._sortDir = 1;
        this._candidates = [];

        this._side = document.getElementById('betting-side');
        this._favDog = document.getElementById('betting-fav-dog');
        this._spreadMin = document.getElementById('betting-spread-min');
        this._spreadMax = document.getElementById('betting-spread-max');
        this._eloMin = document.getElementById('betting-elo-min');
        this._eloMax = document.getElementById('betting-elo-max');
        this._season = document.getElementById('betting-season');
        this._week = document.getElementById('betting-week');
        this._runBtn = document.getElementById('betting-run-btn');
        this._summaryEl = document.getElementById('betting-backtest-summary');
        this._candidatesEl = document.getElementById('betting-candidates-wrap');

        this._runBtn?.addEventListener('click', () => this._run());

        document.querySelectorAll('.betting-angle-btn').forEach(btn => {
            btn.addEventListener('click', () => this._applyAngle(btn.dataset.angle));
        });

        const tabBtn = document.querySelector('[data-tab="betting-section"]');
        if (tabBtn) {
            tabBtn.addEventListener('click', () => {
                if (!this._loaded) { this._loaded = true; this._run(); }
            });
        }
    }

    _applyAngle(name) {
        const angle = PREBUILT_ANGLES[name];
        if (!angle) return;
        this._side.value = angle.side || 'any';
        this._favDog.value = angle.fav_dog || 'any';
        this._spreadMin.value = angle.spread_min ?? '';
        this._spreadMax.value = angle.spread_max ?? '';
        this._eloMin.value = angle.elo_min ?? '';
        this._eloMax.value = angle.elo_max ?? '';
        this._run();
    }

    _buildQuery() {
        const params = new URLSearchParams();
        if (this._side.value !== 'any') params.set('side', this._side.value);
        if (this._favDog.value !== 'any') params.set('favorite_or_dog', this._favDog.value);
        if (this._spreadMin.value !== '') params.set('spread_min', this._spreadMin.value);
        if (this._spreadMax.value !== '') params.set('spread_max', this._spreadMax.value);
        if (this._eloMin.value !== '') params.set('elo_diff_min', this._eloMin.value);
        if (this._eloMax.value !== '') params.set('elo_diff_max', this._eloMax.value);
        if (this._season.value !== '') params.set('season', this._season.value);
        if (this._week.value !== '') params.set('week', this._week.value);
        return params.toString();
    }

    async _run() {
        this._summaryEl.innerHTML = '<p style="color: var(--text-secondary);">Loading…</p>';
        this._candidatesEl.innerHTML = '';

        try {
            const token = AuthService.getToken();
            const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
            const qs = this._buildQuery();
            const resp = await fetch(`/api/admin/betting/screen${qs ? `?${qs}` : ''}`, { headers });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                this._summaryEl.innerHTML = `<p style="color: var(--accent-red);">${err.error || 'Failed to load.'}</p>`;
                return;
            }
            const data = await resp.json();
            this._season.value = data.target_season;
            this._week.value = data.target_week;
            this._candidates = data.candidates;
            this._renderSummary(data);
            this._renderCandidates();
        } catch (e) {
            this._summaryEl.innerHTML = `<p style="color: var(--accent-red);">Failed to load: ${e.message}</p>`;
        }
    }

    _renderSummary(data) {
        const b = data.backtest;
        const pct = b.cover_pct != null ? `${(b.cover_pct * 100).toFixed(1)}%` : '—';
        this._summaryEl.innerHTML = `
            <div style="display: flex; gap: 1.5rem; flex-wrap: wrap; align-items: center;">
                <div><strong>${b.wins}-${b.losses}-${b.pushes}</strong> ATS record</div>
                <div>Cover rate <strong>${pct}</strong></div>
                <div><strong>${b.n}</strong> historical bets</div>
                <div style="color: var(--text-secondary); font-size: 0.85rem;">
                    Week ${data.target_week}, ${data.target_season}
                </div>
            </div>`;
    }

    _renderCandidates() {
        if (!this._candidates.length) {
            this._candidatesEl.innerHTML = '<p style="color: var(--text-secondary);">No games in this week match the filter.</p>';
            return;
        }

        const columns = [
            { label: 'Matchup', key: 'matchup', render: c => `${c.away_team} @ ${c.home_team}` },
            { label: 'Side', key: 'side', render: c => c.side === 'home' ? c.home_team : c.away_team },
            { label: 'Spread', key: 'spread_line', render: c => c.spread_line == null ? '—' : c.spread_line.toFixed(1) },
            { label: 'Elo Diff', key: 'elo_diff', render: c => c.elo_diff == null ? '—' : c.elo_diff.toFixed(1) },
            { label: 'Status', key: 'already_played', render: c => c.already_played ? 'Played' : 'Upcoming' },
        ];

        const sorted = [...this._candidates].sort((a, b) => {
            const av = a[this._sortKey], bv = b[this._sortKey];
            if (av === bv) return 0;
            if (av === null || av === undefined) return 1;
            if (bv === null || bv === undefined) return -1;
            return (av > bv ? 1 : -1) * this._sortDir;
        });

        const head = columns.map(c => {
            const arrow = c.key === this._sortKey ? (this._sortDir === 1 ? ' ▲' : ' ▼') : '';
            return `<th data-key="${c.key}" style="cursor:pointer; user-select:none;">${c.label}${arrow}</th>`;
        }).join('');
        const body = sorted.map(c => `<tr>${columns.map(col => `<td>${col.render(c)}</td>`).join('')}</tr>`).join('');

        this._candidatesEl.innerHTML = `<table class="admin-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;

        this._candidatesEl.querySelectorAll('th[data-key]').forEach(th => {
            th.onclick = () => {
                const key = th.dataset.key;
                this._sortDir = (this._sortKey === key) ? this._sortDir * -1 : 1;
                this._sortKey = key;
                this._renderCandidates();
            };
        });
    }
}

document.addEventListener('DOMContentLoaded', () => new BettingScreener());
