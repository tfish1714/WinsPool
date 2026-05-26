/**
 * admin_predictions.js — Prediction debug page JS.
 *
 * Two tabs:
 *   • Feature Debug — per-game feature breakdown from /api/admin/prediction_features/{season}
 *   • Vegas Edge    — week-level model-vs-Vegas table from /api/admin/predictions/vs-vegas
 */

import { ApiService } from './api.js';

// ── Tab switching ─────────────────────────────────────────────────────────────

document.querySelectorAll('.admin-tab-btn[data-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.admin-tab-btn[data-tab]').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const tab = btn.dataset.tab;
        document.getElementById('tab-feature-debug').style.display = tab === 'feature-debug' ? '' : 'none';
        document.getElementById('tab-vs-vegas').style.display       = tab === 'vs-vegas'      ? '' : 'none';
    });
});

// ── Feature Debug tab ─────────────────────────────────────────────────────────

const seasonEl  = document.getElementById('pd-season');
const weekEl    = document.getElementById('pd-week');
const matchupEl = document.getElementById('pd-matchup');
const resultsEl = document.getElementById('pd-results');
const emptyEl   = document.getElementById('pd-empty');
const loadingEl = document.getElementById('pd-loading');
const versionEl = document.getElementById('pd-version-badge');

let _doc = null;  // full season doc from API

async function loadSeason(season) {
    _doc = null;
    resultsEl.style.display = 'none';
    emptyEl.style.display   = 'none';
    loadingEl.style.display = 'block';
    weekEl.disabled    = true;
    matchupEl.disabled = true;

    try {
        _doc = await ApiService.fetchAdminPredictionFeatures(season);
        loadingEl.style.display = 'none';
        versionEl.textContent   = `Ensemble: ${_doc.ensemble_version || '—'}`;
        populateWeeks();
    } catch (_) {
        loadingEl.style.display = 'none';
        emptyEl.style.display   = 'block';
    }
}

function populateWeeks() {
    const weeks = [...new Set(Object.values(_doc.games || {}).map(g => g.week))].sort((a, b) => a - b);
    weekEl.innerHTML = '<option value="">— Week —</option>' +
        weeks.map(w => `<option value="${w}">Week ${w}</option>`).join('');
    weekEl.disabled    = false;
    matchupEl.disabled = true;
    matchupEl.innerHTML = '<option value="">— Matchup —</option>';
}

function populateMatchups(week) {
    const games = Object.values(_doc.games || {}).filter(g => g.week == week);
    matchupEl.innerHTML = '<option value="">— Matchup —</option>' +
        games.map(g =>
            `<option value="${g.game_key}">${g.away_team} @ ${g.home_team}</option>`
        ).join('');
    matchupEl.disabled = false;
    resultsEl.style.display = 'none';
}

function renderGame(gameKey) {
    const g = (_doc.games || {})[gameKey];
    if (!g) return;

    const modelRows = [
        { label: 'NN',      weight: '45%',  prob: g.nn_prob       },
        { label: 'XGB',     weight: '20%',  prob: g.xgb_prob      },
        { label: 'LR',      weight: '35%',  prob: g.lr_prob       },
        { label: 'Blended', weight: '100%', prob: g.blended_prob, bold: true },
    ];
    document.querySelector('#pd-model-table tbody').innerHTML = modelRows.map(r => `
        <tr>
            <td style="${r.bold ? 'font-weight:700;color:var(--accent-green)' : ''}">${r.label}</td>
            <td style="color:var(--text-secondary);">${r.weight}</td>
            <td style="${r.bold ? 'font-weight:700' : ''}">${r.prob != null ? (r.prob * 100).toFixed(1) + '%' : '—'}</td>
        </tr>`).join('');

    const impMap = {};
    const maxScore = Math.max(...(g.feature_importance || []).map(f => Math.abs(f.score)), 0.0001);
    (g.feature_importance || []).forEach(f => { impMap[f.feature] = f; });

    const sortedFeatures = (g.feature_importance || []).map(f => f.feature);
    Object.keys(g.features || {}).forEach(k => { if (!sortedFeatures.includes(k)) sortedFeatures.push(k); });

    document.querySelector('#pd-feat-table tbody').innerHTML = sortedFeatures.map(feat => {
        const raw    = g.features?.[feat];
        const scaled = g.scaled_features?.[feat];
        const imp    = impMap[feat];
        const score  = imp?.score ?? 0;
        const dir    = imp?.direction ?? '—';
        const pct    = Math.round((Math.abs(score) / maxScore) * 100);
        const colorCls = dir === 'home' ? 'home-color' : 'away-color';
        const dirLabel = dir === 'home' ? g.home_team : (dir === 'away' ? g.away_team : '—');
        return `<tr>
            <td style="font-family:monospace; font-size:0.75rem;">${feat}</td>
            <td>${raw    != null ? Number(raw).toFixed(4)    : '—'}</td>
            <td>${scaled != null ? Number(scaled).toFixed(4) : '—'}</td>
            <td>${score.toFixed(4)}</td>
            <td style="color:${dir === 'home' ? 'var(--accent-green)' : 'var(--accent-gold)'};">${dirLabel}</td>
            <td class="bar-cell"><div class="imp-bar"><div class="imp-fill ${colorCls}" style="width:${pct}%;"></div></div></td>
        </tr>`;
    }).join('');

    resultsEl.style.display = 'block';
}

seasonEl.addEventListener('change', () => { if (seasonEl.value) loadSeason(Number(seasonEl.value)); });
weekEl.addEventListener('change',   () => {
    if (weekEl.value) populateMatchups(Number(weekEl.value));
    resultsEl.style.display = 'none';
});
matchupEl.addEventListener('change', () => { if (matchupEl.value) renderGame(matchupEl.value); });

// ── Vegas Edge tab ────────────────────────────────────────────────────────────

const vvSeason  = document.getElementById('vv-season');
const vvWeek    = document.getElementById('vv-week');
const vvLoad    = document.getElementById('vv-load');
const vvResults = document.getElementById('vv-results');
const vvEmpty   = document.getElementById('vv-empty');
const vvLoading = document.getElementById('vv-loading');

function _fmtSpread(line, home, away) {
    if (line == null) return '<span style="color:var(--text-secondary);">N/A</span>';
    if (line === 0) return "Pick'em";
    const fav = line > 0 ? home : away;
    return `${fav} -${Math.abs(line).toFixed(1)}`;
}

async function loadVsVegas() {
    const season = Number(vvSeason.value);
    const week   = Number(vvWeek.value);
    if (!season || !week) return;

    vvResults.style.display = 'none';
    vvEmpty.style.display   = 'none';
    vvLoading.style.display = 'block';

    try {
        const data = await ApiService.fetchVsVegas(season, week);
        vvLoading.style.display = 'none';

        if (!data.games || !data.games.length) {
            vvEmpty.style.display = 'block';
            return;
        }

        document.querySelector('#vv-table tbody').innerHTML = data.games.map(g => {
            const ev       = g.edge_vs_vegas;
            const evAbs    = ev != null ? Math.abs(ev) : null;
            const edgeCls  = evAbs == null ? 'edge-low' : (evAbs >= 3 ? 'edge-high' : (evAbs >= 1.5 ? 'edge-mid' : 'edge-low'));
            const edgeStr  = ev == null ? '—'
                : `${ev > 0 ? g.home_team : g.away_team} +${evAbs.toFixed(1)}`;
            const sigIcon  = evAbs != null && evAbs >= 3 ? ' ⚡' : '';
            const atsColor = g.pred_ats_pick === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const mlColor  = g.pred_winner   === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            return `<tr>
                <td style="font-weight:600;">${g.away_team} @ ${g.home_team}</td>
                <td>${_fmtSpread(g.vegas_line,   g.home_team, g.away_team)}</td>
                <td>${_fmtSpread(g.model_spread, g.home_team, g.away_team)}</td>
                <td class="${edgeCls}">${edgeStr}${sigIcon}</td>
                <td style="color:${atsColor};">${g.pred_ats_pick ?? '—'}</td>
                <td style="color:${mlColor};">${g.pred_winner ?? '—'}</td>
                <td style="color:var(--text-secondary);">${g.pred_su_conf != null ? g.pred_su_conf + '%' : '—'}</td>
            </tr>`;
        }).join('');

        vvResults.style.display = 'block';
    } catch (_) {
        vvLoading.style.display = 'none';
        vvEmpty.style.display   = 'block';
    }
}

vvLoad.addEventListener('click', loadVsVegas);

// ── Init: populate season / week dropdowns ────────────────────────────────────

(function init() {
    const currentYear = new Date().getFullYear();
    const seasonOpts  = '<option value="">— Season —</option>' +
        Array.from({length: currentYear - 2019}, (_, i) => currentYear - i)
             .map(y => `<option value="${y}">${y}</option>`).join('');

    seasonEl.innerHTML = seasonOpts;
    vvSeason.innerHTML = seasonOpts;

    const weekOpts = '<option value="">— Week —</option>' +
        Array.from({length: 22}, (_, i) => i + 1)
             .map(w => `<option value="${w}">Week ${w}</option>`).join('');
    vvWeek.innerHTML = weekOpts;
})();
