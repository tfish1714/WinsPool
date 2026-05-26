/**
 * admin_predictions.js — Prediction debug page JS.
 *
 * Populates season/week/matchup pickers from /api/admin/prediction_features/{season},
 * then renders the feature table and per-model output when a game is selected.
 */

const seasonEl  = document.getElementById('pd-season');
const weekEl    = document.getElementById('pd-week');
const matchupEl = document.getElementById('pd-matchup');
const resultsEl = document.getElementById('pd-results');
const emptyEl   = document.getElementById('pd-empty');
const loadingEl = document.getElementById('pd-loading');
const versionEl = document.getElementById('pd-version-badge');

let _doc = null;  // full season doc from API

function authHeaders() {
    const token = localStorage.getItem('nfl_wins_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Fetch season doc ──────────────────────────────────────────────────────────

async function loadSeason(season) {
    _doc = null;
    resultsEl.style.display = 'none';
    emptyEl.style.display   = 'none';
    loadingEl.style.display = 'block';
    weekEl.disabled    = true;
    matchupEl.disabled = true;

    try {
        const resp = await fetch(`/api/admin/prediction_features/${season}`, { headers: authHeaders() });
        if (!resp.ok) {
            loadingEl.style.display = 'none';
            emptyEl.style.display   = 'block';
            return;
        }
        _doc = await resp.json();
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

// ── Render game detail ────────────────────────────────────────────────────────

function renderGame(gameKey) {
    const g = (_doc.games || {})[gameKey];
    if (!g) return;

    // Per-model output table
    const modelRows = [
        { label: 'NN',      weight: '45%', prob: g.nn_prob  },
        { label: 'XGB',     weight: '20%', prob: g.xgb_prob },
        { label: 'LR',      weight: '35%', prob: g.lr_prob  },
        { label: 'Blended', weight: '100%', prob: g.blended_prob, bold: true },
    ];
    document.querySelector('#pd-model-table tbody').innerHTML = modelRows.map(r => `
        <tr>
            <td style="${r.bold ? 'font-weight:700;color:var(--accent-green)' : ''}">${r.label}</td>
            <td style="color:var(--text-secondary);">${r.weight}</td>
            <td style="${r.bold ? 'font-weight:700' : ''}">${r.prob != null ? (r.prob * 100).toFixed(1) + '%' : '—'}</td>
        </tr>`).join('');

    // Feature importance as lookup for bars
    const impMap = {};
    const maxScore = Math.max(...(g.feature_importance || []).map(f => Math.abs(f.score)), 0.0001);
    (g.feature_importance || []).forEach(f => { impMap[f.feature] = f; });

    // Feature table — sorted by importance score
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
            <td>${raw != null ? Number(raw).toFixed(4) : '—'}</td>
            <td>${scaled != null ? Number(scaled).toFixed(4) : '—'}</td>
            <td>${score.toFixed(4)}</td>
            <td style="color:${dir === 'home' ? 'var(--accent-green)' : 'var(--accent-gold)'};">${dirLabel}</td>
            <td class="bar-cell">
                <div class="imp-bar">
                    <div class="imp-fill ${colorCls}" style="width:${pct}%;"></div>
                </div>
            </td>
        </tr>`;
    }).join('');

    resultsEl.style.display = 'block';
}

// ── Event listeners ───────────────────────────────────────────────────────────

seasonEl.addEventListener('change', () => {
    if (seasonEl.value) loadSeason(Number(seasonEl.value));
});
weekEl.addEventListener('change', () => {
    if (weekEl.value) populateMatchups(Number(weekEl.value));
    resultsEl.style.display = 'none';
});
matchupEl.addEventListener('change', () => {
    if (matchupEl.value) renderGame(matchupEl.value);
});

// ── Init: populate season dropdown ───────────────────────────────────────────

(function init() {
    const currentYear = new Date().getFullYear();
    const seasons = [];
    for (let y = currentYear; y >= 2020; y--) seasons.push(y);
    seasonEl.innerHTML = '<option value="">— Season —</option>' +
        seasons.map(y => `<option value="${y}">${y}</option>`).join('');
})();
