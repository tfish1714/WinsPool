/**
 * admin_predictions_games.js — Per-game predictions tab for the Admin Panel.
 *
 * Loads /api/admin/predictions/games for a chosen season + week and renders
 * a table: matchup | model pick | actual winner | ✓/✗ | model spread | vegas | edge.
 */

import { ApiService } from './api.js';

// ── DOM refs ──────────────────────────────────────────────────────────────────

const pgSeason  = document.getElementById('pg-season');
const pgWeek    = document.getElementById('pg-week');
const pgLoad    = document.getElementById('pg-load');
const pgResults = document.getElementById('pg-results');
const pgEmpty   = document.getElementById('pg-empty');
const pgLoading = document.getElementById('pg-loading');

// ── Helpers ───────────────────────────────────────────────────────────────────

function _fmtSpread(line, home, away) {
    if (line == null) return '<span style="color:var(--text-secondary);">—</span>';
    if (line === 0)   return "Pick'em";
    const fav = line > 0 ? home : away;
    return `${fav} -${Math.abs(line).toFixed(1)}`;
}

function _correctIcon(isCorrect) {
    if (isCorrect === null || isCorrect === undefined)
        return '<span style="color:var(--text-secondary);">—</span>';
    return isCorrect
        ? '<span style="color:var(--accent-green); font-weight:700;">✓</span>'
        : '<span style="color:var(--accent-red);   font-weight:700;">✗</span>';
}

function _edgeStr(ev, home, away) {
    if (ev == null) return '<span style="color:var(--text-secondary);">—</span>';
    const abs = Math.abs(ev);
    const dir = ev > 0 ? home : away;
    const cls = abs >= 3 ? 'edge-high' : abs >= 1.5 ? 'edge-mid' : 'edge-low';
    return `<span class="${cls}">${dir} +${abs.toFixed(1)}${abs >= 3 ? ' ⚡' : ''}</span>`;
}

// ── Load & render ─────────────────────────────────────────────────────────────

async function loadGames() {
    const season = Number(pgSeason.value);
    const week   = Number(pgWeek.value);
    if (!season || !week) return;

    pgResults.style.display = 'none';
    pgEmpty.style.display   = 'none';
    pgLoading.style.display = 'block';

    try {
        const data = await ApiService.fetchPredictionsGames(season, week);
        pgLoading.style.display = 'none';

        if (!data.games || !data.games.length) {
            pgEmpty.style.display = 'block';
            return;
        }

        const total      = data.games.length;
        const correct    = data.games.filter(g => g.is_correct === true).length;
        const hasResults = data.games.some(g => g.actual_winner != null);

        const summaryHtml = hasResults
            ? `<div style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:0.75rem;">
                 Week ${week} — <span style="color:var(--text-primary); font-weight:600;">${correct}/${total}</span> correct
               </div>`
            : `<div style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:0.75rem;">
                 Week ${week} — ${total} games (no results yet)
               </div>`;

        document.querySelector('#pg-table tbody').innerHTML = data.games.map(g => {
            const pickColor   = g.pred_winner   === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const actualColor = g.actual_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const rowBg       = g.is_correct === false ? 'rgba(239,68,68,0.05)' : '';
            return `<tr style="background:${rowBg}">
                <td style="font-weight:600;">${g.away_team} @ ${g.home_team}</td>
                <td style="color:${pickColor};">${g.pred_winner ?? '—'} ${g.pred_su_conf != null ? `<span style="color:var(--text-secondary);font-size:0.75rem;">${g.pred_su_conf}%</span>` : ''}</td>
                <td style="color:${g.actual_winner ? actualColor : 'var(--text-secondary)'};">${g.actual_winner ?? '—'}</td>
                <td style="text-align:center;">${_correctIcon(g.is_correct)}</td>
                <td>${_fmtSpread(g.model_spread, g.home_team, g.away_team)}</td>
                <td>${_fmtSpread(g.vegas_line,   g.home_team, g.away_team)}</td>
                <td>${_edgeStr(g.edge_vs_vegas, g.home_team, g.away_team)}</td>
            </tr>`;
        }).join('');

        document.getElementById('pg-summary').innerHTML = summaryHtml;
        pgResults.style.display = 'block';
    } catch (err) {
        pgLoading.style.display = 'none';
        pgEmpty.innerHTML = `Failed to load: ${err.message}`;
        pgEmpty.style.display = 'block';
    }
}

pgLoad?.addEventListener('click', loadGames);

// ── Init: populate season / week dropdowns ────────────────────────────────────

(function initPg() {
    if (!pgSeason || !pgWeek) return;

    const currentYear = new Date().getFullYear();
    const seasonOpts  = '<option value="">— Season —</option>' +
        Array.from({ length: currentYear - 2019 }, (_, i) => currentYear - i)
             .map(y => `<option value="${y}">${y}</option>`).join('');
    pgSeason.innerHTML = seasonOpts;

    const weekOpts = '<option value="">— Week —</option>' +
        Array.from({ length: 22 }, (_, i) => i + 1)
             .map(w => `<option value="${w}">Week ${w}</option>`).join('');
    pgWeek.innerHTML = weekOpts;
})();
