/**
 * admin_betting.js — Betting Angle Screener for the Admin panel.
 *
 * Backtests Elo/spread/model filters (angles) against history and shows which
 * games in a chosen week currently match -- one row per game. Fetches
 * GET /api/admin/betting/screen. Self-contained, like admin_elo.js -- lazy-loads
 * its data on first click of the Betting tab.
 *
 * FEATURE_LABELS below must stay in sync with services/betting_screener_service.py's
 * FILTERABLE_FEATURES (feature key -> display label). Kept as a client-side
 * mirror rather than fetched from the server so the filter-row builder has
 * something to populate before the first request ever goes out.
 */

import { AuthService } from './auth_service.js';

const FEATURE_LABELS = {
    spread_line:          'Vegas Spread',
    elo_diff:              'Elo Diff',
    model_spread:          'Model Spread',
    edge_vs_vegas:         'Edge vs Vegas',
    pass_epa_matchup:      'Pass EPA Matchup',
    rush_epa_matchup:      'Rush EPA Matchup',
    early_down_matchup:    'Early-Down Matchup',
    roster_delta:          'Roster Talent Delta',
    turnover_margin:       'Turnover Margin',
    point_diff_advantage:  'Point Diff Advantage',
    rest_advantage:        'Rest Advantage',
    travel_disadvantage:   'Travel Disadvantage',
    trench_dominance:      'Trench Dominance',
    off_roster_value:      'Off Roster Value',
    def_roster_value:      'Def Roster Value',
};

const PREBUILT_ANGLES = {
    home_dog:      { side: 'home', fav_dog: 'dog',       filters: [] },
    away_favorite: { side: 'away', fav_dog: 'favorite',  filters: [] },
    big_favorite:  { side: 'any',  fav_dog: 'favorite',  filters: [{ feature: 'spread_line', min: 10 }] },
    big_underdog:  { side: 'any',  fav_dog: 'dog',       filters: [{ feature: 'spread_line', max: -10 }] },
};

const CORE_COLUMNS = [
    { key: 'matchup', label: 'Matchup', sortable: false,
      render: c => `${c.away_team} @ ${c.home_team}` },
    { key: 'favorite', label: 'Favorite', sortable: false,
      render: c => {
          if (c.spread_line == null) return '—';
          const favTeam = c.spread_line > 0 ? c.home_team : c.away_team;
          return `${favTeam} -${Math.abs(c.spread_line).toFixed(1)}`;
      } },
    { key: 'elo_diff', label: 'Elo Diff', sortable: true,
      render: c => c.elo_diff == null ? '—' : c.elo_diff.toFixed(1) },
    { key: 'model_spread', label: 'Model Spread', sortable: true,
      render: c => c.model_spread == null ? '—' : c.model_spread.toFixed(1) },
    { key: 'edge_vs_vegas', label: 'Edge vs Vegas', sortable: true,
      render: c => c.edge_vs_vegas == null ? '—' : c.edge_vs_vegas.toFixed(1) },
    { key: 'qb_status', label: 'QB Status', sortable: false,
      render: c => {
          const flags = [];
          if (c.home_qb_out) flags.push(`${c.home_team} OUT`);
          if (c.away_qb_out) flags.push(`${c.away_team} OUT`);
          return flags.length ? flags.join(', ') : '—';
      } },
    { key: 'match', label: 'Match', sortable: false,
      render: c => {
          if (c.matched_sides.length === 2) return 'Both';
          const s = c.matched_sides[0];
          return s === 'home' ? `${c.home_team} (home)` : `${c.away_team} (away)`;
      } },
    { key: 'already_played', label: 'Status', sortable: true,
      render: c => c.already_played ? 'Played' : 'Upcoming' },
];

export class BettingScreener {
    constructor() {
        this._loaded = false;
        this._sortKey = 'week';
        this._sortDir = 1;
        this._candidates = [];

        this._side = document.getElementById('betting-side');
        this._favDog = document.getElementById('betting-fav-dog');
        this._season = document.getElementById('betting-season');
        this._week = document.getElementById('betting-week');
        this._runBtn = document.getElementById('betting-run-btn');
        this._summaryEl = document.getElementById('betting-backtest-summary');
        this._candidatesEl = document.getElementById('betting-candidates-wrap');
        this._filterRowsEl = document.getElementById('betting-filter-rows');
        this._addFilterBtn = document.getElementById('betting-add-filter-btn');

        this._runBtn?.addEventListener('click', () => this._run());
        this._addFilterBtn?.addEventListener('click', () => this._addFilterRow());

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

    _addFilterRow(feature = '', min = '', max = '') {
        const row = document.createElement('div');
        row.className = 'betting-filter-row';
        row.style.cssText = 'display: flex; gap: 0.5rem; align-items: center;';

        const select = document.createElement('select');
        select.className = 'admin-input betting-filter-feature';
        select.style.width = '200px';
        for (const [key, label] of Object.entries(FEATURE_LABELS)) {
            const opt = document.createElement('option');
            opt.value = key;
            opt.textContent = label;
            if (key === feature) opt.selected = true;
            select.appendChild(opt);
        }

        const minInput = document.createElement('input');
        minInput.type = 'number';
        minInput.step = '0.1';
        minInput.placeholder = 'min';
        minInput.className = 'admin-input betting-filter-min';
        minInput.style.width = '90px';
        minInput.value = min;

        const maxInput = document.createElement('input');
        maxInput.type = 'number';
        maxInput.step = '0.1';
        maxInput.placeholder = 'max';
        maxInput.className = 'admin-input betting-filter-max';
        maxInput.style.width = '90px';
        maxInput.value = max;

        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn-secondary';
        removeBtn.style.cssText = 'padding: 0.25rem 0.6rem; font-size: 0.8rem;';
        removeBtn.textContent = '×';
        removeBtn.onclick = () => row.remove();

        row.append(select, minInput, maxInput, removeBtn);
        this._filterRowsEl.appendChild(row);
    }

    _clearFilterRows() {
        this._filterRowsEl.innerHTML = '';
    }

    _readFilterRows() {
        const filters = [];
        this._filterRowsEl.querySelectorAll('.betting-filter-row').forEach(row => {
            const feature = row.querySelector('.betting-filter-feature').value;
            const minVal = row.querySelector('.betting-filter-min').value;
            const maxVal = row.querySelector('.betting-filter-max').value;
            if (minVal === '' && maxVal === '') return;
            const filter = { feature };
            if (minVal !== '') filter.min = parseFloat(minVal);
            if (maxVal !== '') filter.max = parseFloat(maxVal);
            filters.push(filter);
        });
        return filters;
    }

    _applyAngle(name) {
        const angle = PREBUILT_ANGLES[name];
        if (!angle) return;
        this._side.value = angle.side || 'any';
        this._favDog.value = angle.fav_dog || 'any';
        this._clearFilterRows();
        angle.filters.forEach(f => this._addFilterRow(f.feature, f.min ?? '', f.max ?? ''));
        this._run();
    }

    /** Loads a Pattern Scanner leaderboard combo's conditions (each a
     * {feature, label, min?, max?}) into the filter builder and runs it. Combos
     * are side-agnostic by construction (they match whichever side satisfies
     * the thresholds), so side/favorite-dog reset to "any". */
    applyCombo(conditions) {
        this._side.value = 'any';
        this._favDog.value = 'any';
        this._clearFilterRows();
        conditions.forEach(c => this._addFilterRow(c.feature, c.min ?? '', c.max ?? ''));
        this._run();
        document.getElementById('betting-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    _buildQuery() {
        const params = new URLSearchParams();
        if (this._side.value !== 'any') params.set('side', this._side.value);
        if (this._favDog.value !== 'any') params.set('favorite_or_dog', this._favDog.value);
        if (this._season.value !== '') params.set('season', this._season.value);
        if (this._week.value !== '') params.set('week', this._week.value);
        const filters = this._readFilterRows();
        if (filters.length) params.set('filters', JSON.stringify(filters));
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

    _activeColumns() {
        const coreKeys = new Set(CORE_COLUMNS.map(c => c.key));
        const extraFeatures = [...new Set(this._readFilterRows().map(f => f.feature))]
            .filter(feature => !coreKeys.has(feature));
        const extraColumns = extraFeatures.map(feature => ({
            key: feature,
            label: FEATURE_LABELS[feature] || feature,
            sortable: true,
            render: c => c[feature] == null ? '—' : Number(c[feature]).toFixed(2),
        }));
        return [...CORE_COLUMNS, ...extraColumns];
    }

    _renderCandidates() {
        if (!this._candidates.length) {
            this._candidatesEl.innerHTML = '<p style="color: var(--text-secondary);">No games in this week match the filter.</p>';
            return;
        }

        const columns = this._activeColumns();

        const sorted = [...this._candidates].sort((a, b) => {
            const av = a[this._sortKey], bv = b[this._sortKey];
            if (av === bv) return 0;
            if (av === null || av === undefined) return 1;
            if (bv === null || bv === undefined) return -1;
            return (av > bv ? 1 : -1) * this._sortDir;
        });

        const head = columns.map(c => {
            if (!c.sortable) return `<th>${c.label}</th>`;
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

let _instance = null;
document.addEventListener('DOMContentLoaded', () => { _instance = new BettingScreener(); });

/** The page's single BettingScreener instance -- used by admin_pattern_scanner.js
 * to load a scan result's combo into the filter builder ("Apply to filters"). */
export function getBettingScreener() {
    return _instance;
}
