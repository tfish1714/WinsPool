/**
 * admin_elo.js — Elo Ratings Explorer for the Admin panel.
 *
 * Fetches /api/admin/elo_history, renders a team-selection grid,
 * and draws a Chart.js line chart comparing selected teams over time.
 * Chart auto-redraws on any filter change.
 */

import { AuthService } from './auth_service.js';

class EloExplorer {
    constructor() {
        this._data = null;          // raw API response
        this._selected = new Set(); // selected team abbrs
        this._chart = null;
        this._drawPending = false;

        this._seasonFrom = document.getElementById('elo-season-from');
        this._seasonTo = document.getElementById('elo-season-to');
        this._resolution = document.getElementById('elo-resolution');
        this._teamGrid = document.getElementById('elo-team-grid');
        this._clearBtn = document.getElementById('elo-clear-btn');
        this._legend = document.getElementById('elo-legend');
        this._canvas = document.getElementById('elo-chart');
        this._empty = document.getElementById('elo-empty');

        this._clearBtn.addEventListener('click', () => { this._clearSelection(); this._scheduleDraw(); });

        document.querySelectorAll('.elo-conf-btn').forEach(btn => {
            btn.addEventListener('click', () => { this._selectByConf(btn.dataset.conf); this._scheduleDraw(); });
        });
        document.querySelectorAll('.elo-div-btn').forEach(btn => {
            btn.addEventListener('click', () => { this._selectByDiv(btn.dataset.div); this._scheduleDraw(); });
        });

        this._seasonFrom.addEventListener('change', () => this._scheduleDraw());
        this._seasonTo.addEventListener('change', () => this._scheduleDraw());
        this._resolution.addEventListener('change', () => this._scheduleDraw());

        // Lazy-load data when the tab is first activated
        const tabBtn = document.querySelector('[data-tab="elo-section"]');
        if (tabBtn) {
            tabBtn.addEventListener('click', () => {
                if (!this._data) this._load();
            });
        }
    }

    // Debounce rapid successive changes (e.g. selecting whole division)
    _scheduleDraw() {
        if (this._drawPending) return;
        this._drawPending = true;
        requestAnimationFrame(() => {
            this._drawPending = false;
            this._drawChart();
        });
    }

    async _load() {
        const { playerId } = AuthService.getCredentials();
        if (!playerId) return;

        try {
            const _token = AuthService.getToken();
            const _headers = _token ? { 'Authorization': `Bearer ${_token}` } : {};
            const resp = await fetch(`/api/admin/elo_history?playerId=${encodeURIComponent(playerId)}`, { headers: _headers });
            this._data = await resp.json();
            this._buildSeasonDropdowns();
            this._buildTeamGrid();
            this._showEmpty();
        } catch (e) {
            console.error('Failed to load Elo history', e);
        }
    }

    _buildSeasonDropdowns() {
        const seasons = this._data.seasons;
        [this._seasonFrom, this._seasonTo].forEach(sel => {
            sel.innerHTML = '';
            seasons.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s;
                opt.textContent = s;
                sel.appendChild(opt);
            });
        });
        // Default: last 5 seasons
        const last = seasons[seasons.length - 1];
        const fifth = seasons[Math.max(0, seasons.length - 5)];
        this._seasonFrom.value = fifth;
        this._seasonTo.value = last;
    }

    _buildTeamGrid() {
        const { divisions, colors } = this._data;
        const allTeams = Object.keys(this._data.teams).sort();
        this._teamGrid.innerHTML = '';

        allTeams.forEach(team => {
            const rawColor = colors[team] || '#888';
            const color = this._visibleColor(rawColor);
            const btn = document.createElement('button');
            btn.className = 'elo-team-btn';
            btn.dataset.team = team;
            btn.dataset.color = color;
            btn.textContent = team;
            btn.title = divisions[team] || '';
            btn.style.cssText = `
                padding: 0.3rem 0.4rem;
                border: 2px solid ${color};
                border-radius: 4px;
                background: transparent;
                color: rgba(255,255,255,0.5);
                font-size: 0.75rem;
                font-weight: 600;
                cursor: pointer;
                transition: background 0.15s, color 0.15s;
            `;
            btn.addEventListener('click', () => { this._toggleTeam(team, btn, color); this._scheduleDraw(); });
            this._teamGrid.appendChild(btn);
        });
    }

    _toggleTeam(team, btn, color) {
        if (this._selected.has(team)) {
            this._selected.delete(team);
            btn.style.background = 'transparent';
            btn.style.color = 'rgba(255,255,255,0.5)';
        } else {
            this._selected.add(team);
            btn.style.background = color + '55'; // tinted, not fully filled
            btn.style.color = '#fff';
            btn.style.borderColor = color;
        }
    }

    _selectByConf(conf) {
        const { conferences } = this._data;
        Object.keys(this._data.teams).forEach(team => {
            if (conferences[team] === conf) {
                this._selected.add(team);
                const btn = this._teamGrid.querySelector(`[data-team="${team}"]`);
                if (btn) { this._applySelectedStyle(btn); }
            }
        });
    }

    _selectByDiv(div) {
        const { divisions } = this._data;
        Object.keys(this._data.teams).forEach(team => {
            if (divisions[team] === div) {
                this._selected.add(team);
                const btn = this._teamGrid.querySelector(`[data-team="${team}"]`);
                if (btn) { this._applySelectedStyle(btn); }
            }
        });
    }

    _applySelectedStyle(btn) {
        const color = btn.dataset.color || '#888';
        btn.style.background = color + '55';
        btn.style.color = '#fff';
        btn.style.borderColor = color;
    }

    _clearSelection() {
        this._selected.clear();
        this._teamGrid.querySelectorAll('.elo-team-btn').forEach(btn => {
            btn.style.background = 'transparent';
            btn.style.color = 'rgba(255,255,255,0.5)';
        });
    }

    _showEmpty() {
        if (this._empty) this._empty.style.display = 'flex';
        if (this._canvas) this._canvas.style.display = 'none';
    }

    _hideEmpty() {
        if (this._empty) this._empty.style.display = 'none';
        if (this._canvas) this._canvas.style.display = 'block';
    }

    _drawChart() {
        if (!this._data) return;

        if (this._selected.size === 0) {
            if (this._chart) { this._chart.destroy(); this._chart = null; }
            this._legend.innerHTML = '';
            this._showEmpty();
            return;
        }

        this._hideEmpty();

        const fromSeason = parseInt(this._seasonFrom.value);
        const toSeason = parseInt(this._seasonTo.value);
        const resolution = this._resolution.value;
        const { teams, colors } = this._data;

        const labels = [];
        const datasets = [];

        if (resolution === 'season') {
            for (let s = fromSeason; s <= toSeason; s++) labels.push(String(s));

            this._selected.forEach(team => {
                const teamData = teams[team] || {};
                const dataPoints = labels.map(s => {
                    const weeks = teamData[s];
                    if (!weeks || weeks.length === 0) return null;
                    return weeks[weeks.length - 1].elo;
                });
                datasets.push(this._makeDataset(team, dataPoints, colors[team]));
            });

        } else {
            const pointMap = {};

            for (let s = fromSeason; s <= toSeason; s++) {
                const allWeeks = new Set();
                this._selected.forEach(team => {
                    const weeks = (teams[team] || {})[String(s)] || [];
                    weeks.forEach(w => allWeeks.add(w.week));
                });

                Array.from(allWeeks).sort((a, b) => a - b).forEach(wk => {
                    labels.push(`${s} W${wk}`);
                    this._selected.forEach(team => {
                        if (!pointMap[team]) pointMap[team] = [];
                        const weeks = (teams[team] || {})[String(s)] || [];
                        const entry = weeks.find(w => w.week === wk);
                        pointMap[team].push(entry ? entry.elo : null);
                    });
                });
            }

            this._selected.forEach(team => {
                datasets.push(this._makeDataset(team, pointMap[team] || [], colors[team]));
            });
        }

        if (this._chart) this._chart.destroy();

        this._chart = new Chart(this._canvas, {
            type: 'line',
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                animation: { duration: 200 },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        itemSort: (a, b) => (b.parsed.y ?? 0) - (a.parsed.y ?? 0),
                        backgroundColor: 'rgba(0,0,0,0.85)',
                        titleColor: 'rgba(255,255,255,0.9)',
                        bodyColor: 'rgba(255,255,255,0.75)',
                        borderColor: 'rgba(255,255,255,0.1)',
                        borderWidth: 1,
                        callbacks: {
                            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1) ?? 'N/A'}`
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: {
                            color: 'rgba(255,255,255,0.7)',
                            maxRotation: resolution === 'week' ? 45 : 0,
                            autoSkip: true,
                            maxTicksLimit: resolution === 'week' ? 36 : 20,
                            font: { size: 11 }
                        },
                        grid: { color: 'rgba(255,255,255,0.1)' }
                    },
                    y: {
                        ticks: { color: 'rgba(255,255,255,0.7)', font: { size: 11 } },
                        grid: { color: 'rgba(255,255,255,0.1)' },
                        title: { display: true, text: 'Elo Rating', color: 'rgba(255,255,255,0.5)', font: { size: 12 } }
                    }
                }
            }
        });

        // Legend
        this._legend.innerHTML = '';
        const sorted = [...datasets].sort((a, b) => {
            const aLast = a.data.filter(v => v !== null).at(-1) ?? 0;
            const bLast = b.data.filter(v => v !== null).at(-1) ?? 0;
            return bLast - aLast;
        });
        sorted.forEach(ds => {
            const lastVal = ds.data.filter(v => v !== null).at(-1);
            const item = document.createElement('div');
            item.style.cssText = 'display:flex;align-items:center;gap:0.4rem;font-size:0.8rem;padding:0.2rem 0.5rem;border-radius:4px;background:rgba(255,255,255,0.04);';
            item.innerHTML = `
                <span style="width:12px;height:12px;border-radius:2px;background:${ds.borderColor};border:1px solid rgba(255,255,255,0.25);flex-shrink:0;display:inline-block;"></span>
                <span style="color:rgba(255,255,255,0.85);font-weight:600;">${ds.label}</span>
                <span style="color:rgba(255,255,255,0.45);margin-left:auto;font-variant-numeric:tabular-nums;">${lastVal != null ? lastVal.toFixed(0) : '—'}</span>
            `;
            this._legend.appendChild(item);
        });
    }

    _makeDataset(team, data, color) {
        const c = this._visibleColor(color || '#888');
        return {
            label: team,
            data,
            borderColor: c,
            backgroundColor: c + '18',
            borderWidth: 2,
            pointRadius: data.length > 80 ? 0 : 3,
            pointHoverRadius: 5,
            spanGaps: true,
            tension: 0.3,
        };
    }

    /**
     * Lightens a hex color that would be invisible on a dark background.
     * Blends toward white until perceived brightness >= threshold.
     */
    _visibleColor(hex) {
        if (!hex || hex.length < 7) return '#888';
        let r = parseInt(hex.slice(1, 3), 16);
        let g = parseInt(hex.slice(3, 5), 16);
        let b = parseInt(hex.slice(5, 7), 16);
        // Perceived brightness (0–255)
        const brightness = (r * 299 + g * 587 + b * 114) / 1000;
        if (brightness >= 80) return hex;
        // Blend toward white until bright enough
        const factor = Math.min(0.7, (80 - brightness) / 120);
        r = Math.round(r + (255 - r) * factor);
        g = Math.round(g + (255 - g) * factor);
        b = Math.round(b + (255 - b) * factor);
        return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
    }
}

document.addEventListener('DOMContentLoaded', () => new EloExplorer());
