/**
 * admin_accuracy.js — ML Prediction Accuracy tab for the Admin Panel.
 *
 * Fetches /api/predictions/accuracy and renders a season-by-season breakdown
 * table with click-to-expand week drill-down.
 */

let _accuracyData = null;

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

async function loadGameDetail(season, week, containerId) {
    const container = document.getElementById(containerId);
    if (!container || container.dataset.loaded === '1') return;
    container.innerHTML = '<div style="padding:8px;color:var(--text-secondary);font-size:0.8rem;">Loading…</div>';

    try {
        const _token  = localStorage.getItem('nfl_wins_token');
        const _headers = _token ? { 'Authorization': `Bearer ${_token}` } : {};
        const resp = await fetch(
            `/api/admin/predictions/games?season=${season}&week=${week}`,
            { headers: _headers }
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        container.dataset.loaded = '1';

        if (!data.games || !data.games.length) {
            container.innerHTML =
                '<div style="color:var(--text-secondary);text-align:center;padding:0.5rem 0;">No predictions for this week.</div>';
            return;
        }

        const gameRows = data.games.map(g => {
            const rowBg     = g.is_correct === false ? 'background:rgba(239,68,68,0.05);' : '';
            const pickColor = g.pred_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const actColor  = g.actual_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const debugUrl  = `/admin/predictions?season=${season}&week=${week}&home=${encodeURIComponent(g.home_team)}&away=${encodeURIComponent(g.away_team)}`;
            return `<tr style="${rowBg}">
                <td style="padding:4px 8px;">
                    <span style="font-weight:600;">${_esc(g.away_team)} @ ${_esc(g.home_team)}</span>
                    <a href="${debugUrl}" target="_blank"
                        title="Feature Debug"
                        style="margin-left:6px; color:var(--text-secondary); font-size:0.7rem; text-decoration:none;">🔍</a>
                </td>
                <td style="padding:4px 8px; color:${pickColor};">
                    ${g.pred_winner != null ? _esc(g.pred_winner) : '—'}
                    ${g.pred_su_conf != null ? `<span style="color:var(--text-secondary);font-size:0.72rem;">${g.pred_su_conf}%</span>` : ''}
                </td>
                <td style="padding:4px 8px; color:${g.actual_winner ? actColor : 'var(--text-secondary)'};">
                    ${g.actual_winner != null ? _esc(g.actual_winner) : '—'}
                </td>
                <td style="padding:4px 8px; text-align:center;">${_pgCorrectIcon(g.is_correct)}</td>
                <td style="padding:4px 8px;">${_pgFmtSpread(g.model_spread, g.home_team, g.away_team)}</td>
                <td style="padding:4px 8px;">${_pgFmtSpread(g.vegas_line, g.home_team, g.away_team)}</td>
                <td style="padding:4px 8px;">${_pgEdgeStr(g.edge_vs_vegas, g.home_team, g.away_team)}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `
            <table style="width:100%; border-collapse:collapse; font-size:0.8rem; margin-top:0.25rem;">
                <thead>
                    <tr style="color:var(--text-secondary);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;">
                        <th style="padding:4px 8px;text-align:left;">Matchup</th>
                        <th style="padding:4px 8px;text-align:left;">Model Pick</th>
                        <th style="padding:4px 8px;text-align:left;">Actual</th>
                        <th style="padding:4px 8px;text-align:center;">✓/✗</th>
                        <th style="padding:4px 8px;text-align:left;">Model Line</th>
                        <th style="padding:4px 8px;text-align:left;">Vegas</th>
                        <th style="padding:4px 8px;text-align:left;">Edge</th>
                    </tr>
                </thead>
                <tbody>${gameRows}</tbody>
            </table>`;
    } catch (err) {
        container.innerHTML =
            `<div style="color:var(--accent-red);padding:0.5rem 0;font-size:0.8rem;">Error: ${err.message}</div>`;
    }
}

function renderSeasonTable(seasons) {
    const table = document.getElementById('acc-season-table');
    if (!seasons || seasons.length === 0) {
        table.innerHTML = '<p style="color:var(--text-secondary);">No accuracy data found. Run the backfill script to generate locked predictions.</p>';
        return;
    }

    const rows = seasons.map(s => `
        <tr class="acc-season-row" data-season="${s.season}" style="cursor:pointer; transition: background 0.15s;"
            onmouseover="this.style.background='rgba(255,255,255,0.04)'"
            onmouseout="this.style.background=''"
        >
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
            const sData = _accuracyData.seasons.find(s => s.season === season);
            if (sData) renderWeekPanel(sData);
        });
    });
}

function renderWeekPanel(seasonData) {
    const panel = document.getElementById('acc-week-panel');
    const title = document.getElementById('acc-week-title');
    const table = document.getElementById('acc-week-table');

    title.textContent = `${seasonData.season} — Week-by-Week Accuracy`;

    const rows = seasonData.by_week.map(w => `
        <tr class="acc-week-row" data-season="${seasonData.season}" data-week="${w.week}"
            style="cursor:pointer; transition:background 0.15s;"
            onmouseover="this.style.background='rgba(255,255,255,0.04)'"
            onmouseout="this.style.background=''">
            <td style="padding:8px 14px; font-weight:600;">
                Week ${w.week}
                <span style="font-size:0.65rem; color:var(--text-secondary); margin-left:4px;">▶</span>
            </td>
            <td style="padding:8px 14px; text-align:right;">${w.correct}/${w.total}</td>
            <td style="padding:8px 14px; min-width:160px;">${_bar(w.accuracy)}</td>
        </tr>
        <tr class="acc-game-expansion" data-expansion-week="${w.week}" data-season="${seasonData.season}" style="display:none;">
            <td colspan="3" style="padding:0.5rem 1rem 0.75rem 2rem; background:rgba(0,0,0,0.2);">
                <div id="acc-games-${seasonData.season}-${w.week}" style="font-size:0.82rem;"></div>
            </td>
        </tr>
    `).join('');

    table.innerHTML = `
        <table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
            <thead>
                <tr style="border-bottom:1px solid var(--glass-border); color:var(--text-secondary); font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em;">
                    <th style="padding:6px 14px; text-align:left;">Week</th>
                    <th style="padding:6px 14px; text-align:right;">Correct / Total</th>
                    <th style="padding:6px 14px; text-align:left;">SU Accuracy</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;

    table.querySelectorAll('.acc-week-row').forEach(row => {
        row.addEventListener('click', () => {
            const season = parseInt(row.dataset.season);
            const week   = parseInt(row.dataset.week);
            const expansion = table.querySelector(`[data-expansion-week="${week}"][data-season="${season}"]`);
            if (!expansion) return;
            const isOpen = expansion.style.display !== 'none';
            expansion.style.display = isOpen ? 'none' : '';
            if (!isOpen) {
                loadGameDetail(season, week, `acc-games-${season}-${week}`);
            }
            // Toggle chevron direction
            const chevron = row.querySelector('span');
            if (chevron) chevron.textContent = isOpen ? '▶' : '▼';
        });
    });

    panel.style.display = 'block';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function loadAccuracyData() {
    const table = document.getElementById('acc-season-table');
    table.innerHTML = '<div style="padding:2rem; text-align:center; color:var(--text-secondary);">Loading accuracy data…</div>';

    try {
        const _token = localStorage.getItem('nfl_wins_token');
        const _headers = _token ? { 'Authorization': `Bearer ${_token}` } : {};
        const resp = await fetch('/api/predictions/accuracy', { headers: _headers });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        _accuracyData = await resp.json();

        // Render overall banner
        const overall = _accuracyData.overall;
        if (overall && overall.total > 0) {
            const banner = document.getElementById('acc-overall');
            banner.style.display = 'flex';
            document.getElementById('acc-overall-pct').textContent = `${overall.accuracy}%`;
            document.getElementById('acc-overall-games').textContent = `${overall.correct.toLocaleString()} / ${overall.total.toLocaleString()}`;
            const pctEl = document.getElementById('acc-overall-pct');
            pctEl.style.color = _colorForAccuracy(overall.accuracy);
        }

        renderSeasonTable(_accuracyData.seasons);
    } catch (err) {
        table.innerHTML = `<p style="color:var(--accent-red);">Failed to load accuracy data: ${err.message}</p>`;
    }
}

// Hook into the tab-switch event (piggyback on admin_main.js tab handler)
document.addEventListener('DOMContentLoaded', () => {
    // Watch for clicks on the ML Accuracy tab button
    const tabBtns = document.querySelectorAll('.admin-tabs .tab-btn');
    tabBtns.forEach(btn => {
        if (btn.dataset.tab === 'accuracy-section') {
            btn.addEventListener('click', () => {
                if (!_accuracyData) loadAccuracyData();
            });
        }
    });
});
