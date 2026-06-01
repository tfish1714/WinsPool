/**
 * admin_accuracy.js — ML Prediction Accuracy tab for the Admin Panel.
 *
 * Season rows are clickable to expand a week-by-week panel.
 * Week rows are clickable to expand an inline game list.
 * Each game row has a magnifying glass that opens a feature detail modal.
 */

let _accuracyData = null;
let _forecastData = null;
const _gameCache  = {};  // keyed by `${season}-${week}-${away}-${home}`

// ── Formatting helpers ─────────────────────────────────────────────────────────

function _colorForAccuracy(pct) {
    if (pct >= 70) return 'var(--accent-green)';
    if (pct >= 60) return 'var(--accent-gold)';
    return 'var(--accent-red)';
}

function _bar(pct) {
    const color = _colorForAccuracy(pct);
    return `<div style="display:flex; align-items:center; gap:8px; min-width:120px;">
        <div style="flex:1; height:6px; background:rgba(255,255,255,0.1); border-radius:3px; overflow:hidden;">
            <div style="width:${pct}%; height:100%; background:${color}; border-radius:3px;"></div>
        </div>
        <span style="color:${color}; font-weight:700; font-size:0.85rem; white-space:nowrap;">${pct}%</span>
    </div>`;
}

const _esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

function _pgFmtSpread(line, home, away) {
    if (line == null) return '<span style="color:var(--text-secondary);">—</span>';
    if (line === 0)   return "Pick'em";
    const fav = line > 0 ? _esc(home) : _esc(away);
    return `${fav} -${Math.abs(line).toFixed(1)}`;
}

function _pgCorrectIcon(isCorrect) {
    if (isCorrect === null || isCorrect === undefined)
        return '<span style="color:var(--text-secondary);">—</span>';
    return isCorrect
        ? '<span style="color:var(--accent-green); font-weight:700;">✓</span>'
        : '<span style="color:var(--accent-red);   font-weight:700;">✗</span>';
}

function _pgEdgeStr(ev, home, away) {
    if (ev == null) return '<span style="color:var(--text-secondary);">—</span>';
    const abs = Math.abs(ev);
    const dir = _esc(ev > 0 ? home : away);
    const cls = abs >= 3 ? 'edge-high' : abs >= 1.5 ? 'edge-mid' : 'edge-low';
    return `<span class="${cls}">${dir} +${abs.toFixed(1)}${abs >= 3 ? ' ⚡' : ''}</span>`;
}

// ── Modal helpers ──────────────────────────────────────────────────────────────

const _modal      = () => document.getElementById('acc-game-modal');
const _modalTitle = () => document.getElementById('acc-game-modal-title');
const _modalBody  = () => document.getElementById('acc-game-modal-body');

function _openModal(title, bodyHtml) {
    _modalTitle().textContent = title;
    _modalBody().innerHTML    = bodyHtml;
    _modal().classList.add('open');
    document.body.style.overflow = 'hidden';
}

function _closeModal() {
    _modal().classList.remove('open');
    document.body.style.overflow = '';
}

// ── Inline week expansion ──────────────────────────────────────────────────────

async function loadWeekGames(season, week, containerId) {
    const container = document.getElementById(containerId);
    if (!container || container.dataset.loaded === '1') return;
    container.innerHTML = '<div style="padding:8px;color:var(--text-secondary);font-size:0.8rem;">Loading…</div>';

    try {
        const token   = localStorage.getItem('nfl_wins_token');
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const resp = await fetch(
            `/api/admin/predictions/games?season=${season}&week=${week}`,
            { headers }
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        container.dataset.loaded = '1';

        // Cache game data for the feature detail modal
        data.games.forEach(g => {
            const k = `${season}-${week}-${g.away_team}-${g.home_team}`;
            _gameCache[k] = g;
        });

        if (!data.games || !data.games.length) {
            container.innerHTML = '<div style="color:var(--text-secondary);text-align:center;padding:0.5rem 0;">No predictions for this week.</div>';
            return;
        }

        const hasVegas = data.games.some(g => g.vegas_line != null);

        const gameRows = data.games.map(g => {
            const rowBg     = g.is_correct === false ? 'background:rgba(239,68,68,0.05);' : '';
            const pickColor = g.pred_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const actColor  = g.actual_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const vegasCols = hasVegas
                ? `<td style="padding:4px 8px;">${_pgFmtSpread(g.vegas_line, g.home_team, g.away_team)}</td>
                   <td style="padding:4px 8px;">${_pgEdgeStr(g.edge_vs_vegas, g.home_team, g.away_team)}</td>`
                : '';
            // onclick uses global function assigned below
            return `<tr style="${rowBg}">
                <td style="padding:4px 8px;">
                    <span style="font-weight:600;">${_esc(g.away_team)} @ ${_esc(g.home_team)}</span>
                    <button class="pred-explain-btn"
                        data-season="${season}"
                        data-week="${week}"
                        data-home="${g.home_team}"
                        data-away="${g.away_team}"
                        title="Why this prediction?"
                        style="background:none; border:1px solid rgba(251,191,36,0.3); border-radius:50%; width:22px; height:22px; cursor:pointer; color:var(--accent-gold); font-size:12px; padding:0; line-height:22px; text-align:center; flex-shrink:0; margin-left:6px; vertical-align:middle;">?</button>
                </td>
                <td style="padding:4px 8px;color:${pickColor};">
                    ${g.pred_winner != null ? _esc(g.pred_winner) : '—'}
                    ${g.pred_su_conf != null ? `<span style="color:var(--text-secondary);font-size:0.72rem;">${g.pred_su_conf}%</span>` : ''}
                </td>
                <td style="padding:4px 8px;color:${g.actual_winner ? actColor : 'var(--text-secondary)'};">
                    ${g.actual_winner != null
                        ? (() => {
                            if (g.home_score != null && g.away_score != null) {
                                const hs = `<span style="font-weight:${g.actual_winner === g.home_team ? '700' : '400'};">${_esc(g.home_team)} ${g.home_score}</span>`;
                                const as = `<span style="font-weight:${g.actual_winner === g.away_team ? '700' : '400'};">${_esc(g.away_team)} ${g.away_score}</span>`;
                                return `${as} – ${hs}`;
                            }
                            return _esc(g.actual_winner);
                          })()
                        : '—'}
                </td>
                <td style="padding:4px 8px;text-align:center;">${_pgCorrectIcon(g.is_correct)}</td>
                <td style="padding:4px 8px;">${_pgFmtSpread(g.model_spread, g.home_team, g.away_team)}</td>
                ${vegasCols}
            </tr>`;
        }).join('');

        const vegasHeaders = hasVegas
            ? '<th style="padding:4px 8px;text-align:left;">Vegas</th><th style="padding:4px 8px;text-align:left;">Edge</th>'
            : '';

        container.innerHTML = `
            <table style="width:100%;border-collapse:collapse;font-size:0.8rem;margin-top:0.25rem;">
                <thead>
                    <tr style="color:var(--text-secondary);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;">
                        <th style="padding:4px 8px;text-align:left;">Matchup</th>
                        <th style="padding:4px 8px;text-align:left;">Model Pick</th>
                        <th style="padding:4px 8px;text-align:left;">Actual</th>
                        <th style="padding:4px 8px;text-align:center;">✓/✗</th>
                        <th style="padding:4px 8px;text-align:left;">Model Line</th>
                        ${vegasHeaders}
                    </tr>
                </thead>
                <tbody>${gameRows}</tbody>
            </table>`;
    } catch (err) {
        container.innerHTML = `<div style="color:var(--accent-red);padding:0.5rem 0;font-size:0.8rem;">Error: ${_esc(err.message)}</div>`;
    }
}

// ── Per-game feature detail modal ──────────────────────────────────────────────

window._openGameFeatureModal = async function(season, week, away, home) {
    const cached = _gameCache[`${season}-${week}-${away}-${home}`];

    _openModal(
        `${_esc(away)} @ ${_esc(home)} — Feature Debug`,
        '<div style="padding:2rem;text-align:center;color:var(--text-secondary);font-size:0.85rem;">Loading…</div>'
    );

    // Build score + spread summary from cached game data
    function _scoreSummaryHtml(c) {
        if (!c) return '';
        const scoreStr = (c.home_score != null && c.away_score != null)
            ? `<span style="font-weight:${c.actual_winner===c.away_team?'700':'400'};">${_esc(c.away_team)} ${c.away_score}</span>
               <span style="color:var(--text-secondary);margin:0 6px;">–</span>
               <span style="font-weight:${c.actual_winner===c.home_team?'700':'400'};">${_esc(c.home_team)} ${c.home_score}</span>`
            : '';
        const modelSpread = _pgFmtSpread(c.model_spread, c.home_team, c.away_team);
        const vegasSpread = _pgFmtSpread(c.vegas_line,   c.home_team, c.away_team);
        const edge        = _pgEdgeStr(c.edge_vs_vegas,  c.home_team, c.away_team);
        const correct     = _pgCorrectIcon(c.is_correct);
        return `
            <div style="display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;
                        padding:0.75rem 0 1rem;border-bottom:1px solid var(--glass-border);margin-bottom:1rem;font-size:0.85rem;">
                ${scoreStr ? `<span style="font-size:1rem;">${scoreStr}</span>` : ''}
                ${scoreStr ? correct : ''}
                <span style="color:var(--text-secondary);">
                    Model: <strong style="color:var(--text-primary);">${modelSpread}</strong>
                </span>
                <span style="color:var(--text-secondary);">
                    Vegas: <strong style="color:var(--text-primary);">${vegasSpread}</strong>
                </span>
                <span style="color:var(--text-secondary);">Edge: ${edge}</span>
            </div>`;
    }

    try {
        const token   = localStorage.getItem('nfl_wins_token');
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const resp = await fetch(
            `/api/prediction_features/${season}/${week}/${encodeURIComponent(away)}/${encodeURIComponent(home)}`,
            { headers }
        );

        if (resp.status === 404) {
            _modalBody().innerHTML =
                _scoreSummaryHtml(cached) +
                `<div style="color:var(--text-secondary);text-align:center;padding:1.5rem 0;">
                    No feature audit data for this game.<br>
                    <code style="font-size:0.75rem;">python scripts/backfill_schedule_predictions.py --season ${season} --features</code>
                </div>`;
            return;
        }
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const g = await resp.json();

        const modelRows = [
            { label: 'NN',      weight: '45%',  prob: g.nn_prob },
            { label: 'XGB',     weight: '20%',  prob: g.xgb_prob },
            { label: 'LR',      weight: '35%',  prob: g.lr_prob },
            { label: 'Blended', weight: '100%', prob: g.blended_prob, bold: true },
        ];
        const modelHtml = modelRows.map(r =>
            `<tr>
                <td style="${r.bold ? 'font-weight:700;color:var(--accent-green)' : ''}">${r.label}</td>
                <td style="color:var(--text-secondary);">${r.weight}</td>
                <td style="${r.bold ? 'font-weight:700' : ''}">${r.prob != null ? (r.prob * 100).toFixed(1) + '%' : '—'}</td>
            </tr>`
        ).join('');

        const impMap   = {};
        const maxScore = Math.max(...(g.feature_importance || []).map(f => Math.abs(f.score)), 0.0001);
        (g.feature_importance || []).forEach(f => { impMap[f.feature] = f; });
        const features = (g.feature_importance || []).map(f => f.feature);
        Object.keys(g.features || {}).forEach(k => { if (!features.includes(k)) features.push(k); });

        const featHtml = features.map(feat => {
            const raw   = g.features?.[feat];
            const scl   = g.scaled_features?.[feat];
            const imp   = impMap[feat];
            const score = imp?.score ?? 0;
            const dir   = imp?.direction ?? '—';
            const pct   = Math.round((Math.abs(score) / maxScore) * 100);
            const col   = dir === 'home' ? 'var(--accent-green)' : 'var(--accent-gold)';
            const label = dir === 'home' ? _esc(home) : (dir === 'away' ? _esc(away) : '—');
            return `<tr>
                <td style="font-family:monospace;font-size:0.72rem;">${_esc(feat)}</td>
                <td>${raw != null ? Number(raw).toFixed(4) : '—'}</td>
                <td>${scl != null ? Number(scl).toFixed(4) : '—'}</td>
                <td>${score.toFixed(4)}</td>
                <td style="color:${col};">${label}</td>
                <td style="width:80px;">
                    <div style="height:4px;border-radius:2px;background:rgba(255,255,255,0.07);">
                        <div style="height:4px;border-radius:2px;width:${pct}%;background:${col};"></div>
                    </div>
                </td>
            </tr>`;
        }).join('');

        const version = g.ensemble_version
            ? `<span style="font-size:0.72rem;color:var(--text-secondary);margin-left:8px;">ensemble: ${_esc(g.ensemble_version)}</span>`
            : '';

        _modalBody().innerHTML =
            _scoreSummaryHtml(cached) +
            `<div style="margin-bottom:1.25rem;">
                <h4 style="margin:0 0 0.5rem;font-size:0.85rem;">Model Output${version}</h4>
                <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
                    <thead><tr>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Model</th>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Weight</th>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Home Win Prob</th>
                    </tr></thead>
                    <tbody>${modelHtml}</tbody>
                </table>
            </div>
            <div>
                <h4 style="margin:0 0 0.5rem;font-size:0.85rem;">Feature Breakdown</h4>
                <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
                    <thead><tr>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Feature</th>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Raw</th>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Scaled</th>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Importance</th>
                        <th style="padding:5px 8px;text-align:left;color:var(--text-secondary);font-size:0.7rem;border-bottom:1px solid var(--glass-border);">Direction</th>
                        <th style="border-bottom:1px solid var(--glass-border);"></th>
                    </tr></thead>
                    <tbody>${featHtml}</tbody>
                </table>
            </div>`;
    } catch (err) {
        _modalBody().innerHTML =
            `<div style="color:var(--accent-red);padding:1rem 0;font-size:0.85rem;">Error: ${_esc(err.message)}</div>`;
    }
};

// ── 2026 Forecast card ────────────────────────────────────────────────────────

function renderForecastCard(data) {
    const el = document.getElementById('acc-forecast-section');
    if (!el) return;

    const version = data.model_version
        ? `<span style="font-size:0.72rem;color:var(--text-secondary);font-family:monospace;margin-left:8px;">${_esc(data.model_version)}</span>`
        : '';

    let projHtml;
    if (!data.team_projections || data.team_projections.length === 0) {
        projHtml = `<p style="color:var(--text-secondary);font-size:0.85rem;margin:0;">No preseason projections found. Run <code>python scripts/predict_season.py --season 2026</code>.</p>`;
    } else {
        const items = data.team_projections.map(t => {
            const pct = Math.min((t.projected_wins / 17) * 100, 100).toFixed(1);
            return `<div style="display:flex;align-items:center;gap:8px;padding:3px 0;min-width:0;">
                <span style="width:36px;font-size:0.8rem;font-weight:700;color:var(--text-primary);flex-shrink:0;">${_esc(t.team)}</span>
                <div style="flex:1;height:6px;background:rgba(255,255,255,0.1);border-radius:3px;overflow:hidden;min-width:60px;">
                    <div style="width:${pct}%;height:100%;background:var(--accent-green);border-radius:3px;"></div>
                </div>
                <span style="font-size:0.8rem;color:var(--text-primary);white-space:nowrap;width:36px;">${t.projected_wins.toFixed(1)}W</span>
                <span style="font-size:0.72rem;color:var(--text-secondary);white-space:nowrap;">±${t.std_dev.toFixed(1)}</span>
            </div>`;
        }).join('');
        projHtml = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 2rem;">${items}</div>`;
    }

    const weekRows = (data.weeks || []).map(wk => {
        const cid = `forecast-games-${wk}`;
        return `<div style="border-bottom:1px solid rgba(255,255,255,0.04);">
            <div style="display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer;"
                 onclick="(function(row){
                    const exp = row.parentElement.querySelector('.fc-expand');
                    const open = exp.style.display !== 'none';
                    exp.style.display = open ? 'none' : '';
                    row.querySelector('.fc-chevron').innerHTML = open ? '&#9658;' : '&#9660;';
                    if (!open) loadWeekGames(2026, ${wk}, '${cid}');
                 })(this)">
                <span class="fc-chevron" style="font-size:0.65rem;color:var(--text-secondary);">&#9658;</span>
                <span style="font-weight:600;font-size:0.9rem;">Week ${wk}</span>
            </div>
            <div class="fc-expand" style="display:none;padding:0.5rem 1rem 0.75rem 1.5rem;background:rgba(0,0,0,0.2);">
                <div id="${cid}" style="font-size:0.82rem;"></div>
            </div>
        </div>`;
    }).join('');

    el.innerHTML = `
        <div style="border:1px solid var(--glass-border);border-radius:8px;padding:1.25rem;background:rgba(255,255,255,0.02);">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:0.5rem;">
                <h3 style="margin:0;font-size:1rem;">2026 Season Forecast${version}</h3>
                <span style="font-size:0.8rem;color:var(--text-secondary);">${data.game_count} games · ${(data.weeks || []).length} weeks</span>
            </div>
            <div style="margin-bottom:1.25rem;">
                <div style="font-size:0.75rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-secondary);margin-bottom:0.5rem;">Team Win Projections</div>
                ${projHtml}
            </div>
            <div>
                <div style="font-size:0.75rem;text-transform:uppercase;letter-spacing:0.06em;color:var(--text-secondary);margin-bottom:0.25rem;">Per-Game Predictions</div>
                ${weekRows}
            </div>
        </div>`;
}

async function loadForecastData() {
    const el = document.getElementById('acc-forecast-section');
    if (!el) return;
    el.innerHTML = '<div style="padding:1rem;color:var(--text-secondary);font-size:0.85rem;">Loading 2026 forecast…</div>';
    try {
        const token   = localStorage.getItem('nfl_wins_token');
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const resp = await fetch('/api/admin/forecast', { headers });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        _forecastData = await resp.json();
        renderForecastCard(_forecastData);
    } catch (err) {
        el.innerHTML = `<div style="padding:0.5rem 0;font-size:0.85rem;color:var(--text-secondary);">2026 forecast unavailable: ${_esc(err.message)}</div>`;
    }
}

// ── Season table ───────────────────────────────────────────────────────────────

function renderSeasonTable(seasons) {
    const table = document.getElementById('acc-season-table');
    if (!seasons || seasons.length === 0) {
        table.innerHTML = '<p style="color:var(--text-secondary);">No accuracy data found. Run the backfill script to generate locked predictions.</p>';
        return;
    }

    const rows = seasons.map(s => `
        <tr class="acc-season-row" data-season="${s.season}" style="cursor:pointer; transition: background 0.15s;"
            onmouseover="this.style.background='rgba(255,255,255,0.04)'"
            onmouseout="this.style.background=''">
            <td style="padding:10px 14px; font-weight:700; color:var(--accent-gold);">${s.season}</td>
            <td style="padding:10px 14px; text-align:right;">${s.correct}/${s.total}</td>
            <td style="padding:10px 14px; min-width:160px;">${_bar(s.accuracy)}</td>
            <td style="padding:10px 14px; font-size:0.72rem; color:var(--text-secondary); font-family:monospace; white-space:nowrap;">${s.model_version || '—'}</td>
            <td style="padding:10px 14px; font-size:0.8rem; color:var(--text-secondary);">↗ weekly</td>
        </tr>
    `).join('');

    table.innerHTML = `
        <table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
            <thead>
                <tr style="border-bottom:1px solid var(--glass-border); color:var(--text-secondary); font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em;">
                    <th style="padding:8px 14px; text-align:left;">Season</th>
                    <th style="padding:8px 14px; text-align:right;">Correct / Total</th>
                    <th style="padding:8px 14px; text-align:left;">SU Accuracy</th>
                    <th style="padding:8px 14px; text-align:left;">Ensemble</th>
                    <th style="padding:8px 14px;"></th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;

    document.querySelectorAll('.acc-season-row').forEach(row => {
        row.addEventListener('click', () => {
            const season = parseInt(row.dataset.season);
            const sData  = _accuracyData.seasons.find(s => s.season === season);
            if (sData) renderWeekPanel(sData);
        });
    });
}

// ── Week panel ────────────────────────────────────────────────────────────────

function renderWeekPanel(seasonData) {
    const panel = document.getElementById('acc-week-panel');
    const title = document.getElementById('acc-week-title');
    const table = document.getElementById('acc-week-table');

    title.textContent = `${seasonData.season} — Week-by-Week Accuracy`;

    const rows = seasonData.by_week.map(w => `
        <tr class="acc-week-row" data-season="${seasonData.season}" data-week="${w.week}"
            style="cursor:pointer;transition:background 0.15s;border-bottom:1px solid rgba(255,255,255,0.04);"
            onmouseover="this.style.background='rgba(255,255,255,0.04)'"
            onmouseout="this.style.background=''">
            <td style="padding:8px 14px;font-weight:600;">
                Week ${w.week}
                <span class="acc-chevron" style="font-size:0.65rem;color:var(--text-secondary);margin-left:4px;">&#9658;</span>
            </td>
            <td style="padding:8px 14px;text-align:right;">${w.correct}/${w.total}</td>
            <td style="padding:8px 14px;min-width:160px;">${_bar(w.accuracy)}</td>
        </tr>
        <tr class="acc-expansion-row" data-exp-season="${seasonData.season}" data-exp-week="${w.week}" style="display:none;">
            <td colspan="3" style="padding:0.5rem 1rem 0.75rem 2rem;background:rgba(0,0,0,0.2);">
                <div id="acc-games-${seasonData.season}-${w.week}" style="font-size:0.82rem;"></div>
            </td>
        </tr>
    `).join('');

    table.innerHTML = `
        <table style="width:100%;border-collapse:collapse;font-size:0.9rem;">
            <thead>
                <tr style="border-bottom:1px solid var(--glass-border);color:var(--text-secondary);font-size:0.75rem;text-transform:uppercase;letter-spacing:0.06em;">
                    <th style="padding:6px 14px;text-align:left;">Week</th>
                    <th style="padding:6px 14px;text-align:right;">Correct / Total</th>
                    <th style="padding:6px 14px;text-align:left;">SU Accuracy</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;

    table.querySelectorAll('.acc-week-row').forEach(row => {
        row.addEventListener('click', () => {
            const season = parseInt(row.dataset.season, 10);
            const week   = parseInt(row.dataset.week,   10);
            const expRow = table.querySelector(`[data-exp-season="${season}"][data-exp-week="${week}"]`);
            if (!expRow) return;
            const isOpen = expRow.style.display !== 'none';
            expRow.style.display = isOpen ? 'none' : '';
            const chevron = row.querySelector('.acc-chevron');
            if (chevron) chevron.innerHTML = isOpen ? '&#9658;' : '&#9660;';
            if (!isOpen) loadWeekGames(season, week, `acc-games-${season}-${week}`);
        });
    });

    panel.style.display = 'block';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// ── Data fetch ─────────────────────────────────────────────────────────────────

async function loadAccuracyData() {
    const table = document.getElementById('acc-season-table');
    table.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-secondary);">Loading accuracy data…</div>';

    try {
        const token   = localStorage.getItem('nfl_wins_token');
        const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
        const resp = await fetch('/api/predictions/accuracy', { headers });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        _accuracyData = await resp.json();

        const overall = _accuracyData.overall;
        if (overall && overall.total > 0) {
            const banner = document.getElementById('acc-overall');
            banner.style.display = 'flex';
            document.getElementById('acc-overall-pct').textContent   = `${overall.accuracy}%`;
            document.getElementById('acc-overall-games').textContent = `${overall.correct.toLocaleString()} / ${overall.total.toLocaleString()}`;
            document.getElementById('acc-overall-pct').style.color   = _colorForAccuracy(overall.accuracy);
        }

        renderSeasonTable(_accuracyData.seasons);
    } catch (err) {
        table.innerHTML = `<p style="color:var(--accent-red);">Failed to load accuracy data: ${err.message}</p>`;
    }
}

// ── Init ───────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    // Lazy-load when the ML Accuracy tab is first clicked
    document.querySelectorAll('.admin-tabs .tab-btn').forEach(btn => {
        if (btn.dataset.tab === 'accuracy-section') {
            btn.addEventListener('click', () => {
                if (!_forecastData) loadForecastData();
                if (!_accuracyData) loadAccuracyData();
            });
        }
    });

    // Modal close: X button
    const closeBtn = document.getElementById('acc-game-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', _closeModal);

    // Modal close: backdrop click
    const modal = document.getElementById('acc-game-modal');
    if (modal) {
        modal.addEventListener('click', e => {
            if (e.target === modal) _closeModal();
        });
    }

    // Modal close: Escape key
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && modal && modal.classList.contains('open')) _closeModal();
    });
});
