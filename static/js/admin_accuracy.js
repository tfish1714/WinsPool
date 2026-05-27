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
        <tr>
            <td style="padding:8px 14px; font-weight:600;">Week ${w.week}</td>
            <td style="padding:8px 14px; text-align:right;">${w.correct}/${w.total}</td>
            <td style="padding:8px 14px; min-width:160px;">${_bar(w.accuracy)}</td>
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
