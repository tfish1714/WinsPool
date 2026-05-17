/**
 * schedule_explain.js — Admin-only ML prediction explanation modal.
 *
 * Reveals a "?" button on each game card with a prediction. Clicking it
 * fetches /api/predictions/explain and renders a factor breakdown modal.
 */

import { AuthService } from './auth_service.js';

const modal   = document.getElementById('pred-explain-modal');
const content = document.getElementById('pred-explain-content');
const closeBtn = document.getElementById('pred-explain-close');

function isAdmin() {
    return (AuthService.getCredentials().role || '').toLowerCase() === 'admin';
}

function showModal() { modal.style.display = 'flex'; }
function hideModal() { modal.style.display = 'none'; }

closeBtn?.addEventListener('click', hideModal);
modal?.addEventListener('click', e => { if (e.target === modal) hideModal(); });

// ── Factor rendering ──────────────────────────────────────────────────────────

function _arrow(val, higherIsBetter = true) {
    if (val === null || val === undefined) return '';
    const positive = higherIsBetter ? val > 0 : val < 0;
    const color = positive ? 'var(--accent-green)' : (val === 0 ? 'var(--text-secondary)' : 'var(--accent-red)');
    const sym   = val > 0 ? '▲' : (val < 0 ? '▼' : '–');
    return `<span style="color:${color}; font-weight:700;">${sym}</span>`;
}

function _bar(pct, color) {
    const w = Math.min(100, Math.max(0, Math.abs(pct)));
    return `<div style="height:4px; background:rgba(255,255,255,0.08); border-radius:2px; overflow:hidden; margin-top:3px;">
        <div style="width:${w}%; height:100%; background:${color}; border-radius:2px;"></div>
    </div>`;
}

function _row(label, valueHtml, subHtml = '') {
    return `<div style="display:flex; justify-content:space-between; align-items:flex-start; padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.05);">
        <span style="color:var(--text-secondary); font-size:0.8rem; flex:1;">${label}</span>
        <div style="text-align:right; font-size:0.82rem;">
            ${valueHtml}
            ${subHtml ? `<div style="font-size:0.72rem; color:var(--text-secondary);">${subHtml}</div>` : ''}
        </div>
    </div>`;
}

function renderExplanation(data) {
    const { home_team, away_team, pred_winner, pred_su_conf, pred_prob,
            pred_ats_pick, explanation: ex } = data;

    const isProfileOnly = ex?.source === 'profile';
    const homeFavored   = pred_winner === home_team;
    const predColor     = homeFavored ? 'var(--accent-green)' : 'var(--accent-gold)';
    const confBar       = _bar(pred_su_conf, predColor);

    // Vegas implied probability (from stored vegas_home_prob)
    let vegasHtml = '—';
    if (ex?.vegas_line != null) {
        const line     = ex.vegas_line;
        const favTeam  = line > 0 ? home_team : (line < 0 ? away_team : null);
        const lineAbs  = Math.abs(line);
        const lineStr  = favTeam ? `${favTeam} -${lineAbs}` : 'Pick\'em';
        const vhp      = ex.vegas_home_prob;
        const vPct     = vhp != null ? Math.round((homeFavored ? vhp : 1 - vhp) * 100) : null;
        const mlDiff   = vPct != null ? Math.round(pred_su_conf - vPct) : null;
        const diffStr  = mlDiff != null
            ? `<span style="color:${mlDiff >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}; font-size:0.72rem;"> (ML ${mlDiff >= 0 ? '+' : ''}${mlDiff}% vs Vegas)</span>`
            : '';
        vegasHtml = `${lineStr}${diffStr}`;
    }

    // Elo
    const _up   = `<span style="color:var(--accent-green); font-weight:700;">▲</span>`;
    const _down = `<span style="color:var(--accent-red);   font-weight:700;">▼</span>`;

    let eloHtml = '—';
    if (ex?.elo_diff != null) {
        const d = ex.elo_diff;
        const favTeam = d > 0 ? home_team : (d < 0 ? away_team : null);
        const pts = Math.abs(d);
        eloHtml = favTeam ? `${_up} ${favTeam} +${pts.toFixed(0)} Elo` : '≈ Even';
    }

    // Roster quality grade (z-score diff: home_grade − away_grade, range ≈ ±3)
    let rosterHtml = '—';
    if (ex?.roster_delta != null) {
        const d = ex.roster_delta;
        rosterHtml = Math.abs(d) < 0.2
            ? `≈ Even`
            : `${_up} ${d > 0 ? home_team : away_team} edge (${d > 0 ? '+' : ''}${d.toFixed(2)})`;
    }

    // Pass EPA
    let passHtml = '—';
    if (ex?.off_pass_epa != null && ex?.def_pass_epa != null) {
        const net = ex.off_pass_epa - ex.def_pass_epa;
        passHtml = `${_up} ${net >= 0 ? home_team : away_team} +${Math.abs(net).toFixed(3)} EPA/play`;
    }

    // Rush EPA
    let rushHtml = '—';
    if (ex?.off_rush_epa != null && ex?.def_rush_epa != null) {
        const net = ex.off_rush_epa - ex.def_rush_epa;
        rushHtml = `${_up} ${net >= 0 ? home_team : away_team} +${Math.abs(net).toFixed(3)} EPA/play`;
    }

    // Turnovers (rolling margin, real range ≈ ±1.5)
    let toHtml = '—';
    if (ex?.turnover_margin != null) {
        const t = ex.turnover_margin;
        toHtml = Math.abs(t) < 0.1
            ? '≈ Even'
            : `${_up} ${t > 0 ? home_team : away_team} +${Math.abs(t).toFixed(2)}/gm`;
    }

    // QB starter status (snap-count based: flags when Week 1-3 starter takes <20% of snaps)
    let qbHtml = '—';
    {
        const hOut = ex?.home_qb_out ?? (ex?.qb_injury < 0 ? 1 : 0);
        const aOut = ex?.away_qb_out ?? (ex?.qb_injury > 0 ? 1 : 0);
        if (hOut && aOut) {
            qbHtml = `<span style="color:var(--accent-gold);">⚠ Both teams using backup QBs</span>`;
        } else if (hOut) {
            qbHtml = `<span style="color:var(--accent-red);">⚠ ${home_team} backup QB</span>`;
        } else if (aOut) {
            qbHtml = `<span style="color:var(--accent-green);">${_up} ${away_team} backup QB</span>`;
        } else if (ex?.qb_injury !== null && ex?.qb_injury !== undefined) {
            qbHtml = 'Starters playing';
        }
    }

    // Rest/travel
    let restHtml = '—';
    if (ex?.rest_disadvantage != null) {
        const r = ex.rest_disadvantage;
        restHtml = Math.abs(r) < 0.1
            ? '≈ Even'
            : `${_down} ${r > 0 ? home_team : away_team} disadvantaged`;
    }

    // Trench dominance (snap-weighted O-line score, real range ≈ ±750)
    let trenchHtml = '—';
    if (ex?.trench_dominance != null) {
        const t = ex.trench_dominance;
        trenchHtml = Math.abs(t) < 50
            ? '≈ Even'
            : `${_up} ${t > 0 ? home_team : away_team} O-line edge`;
    }

    const atsNote = pred_ats_pick !== pred_winner
        ? `<div style="margin-top:4px; font-size:0.72rem; color:var(--accent-gold);">⚡ Model disagrees with its own SU pick ATS (takes ${pred_ats_pick})</div>`
        : '';

    const sourceNote = isProfileOnly
        ? `<div style="margin-top:0.75rem; padding:6px 10px; border-radius:6px; background:rgba(255,255,255,0.04); font-size:0.72rem; color:var(--text-secondary);">
            ℹ Feature data unavailable for this game — prediction based on prior-season team profiles. Detailed factors not available.
           </div>`
        : '';

    const rows = isProfileOnly
        ? `${_row('Vegas Line', vegasHtml)}`
        : [
            _row('Vegas Line',       vegasHtml),
            _row('Team Strength',    eloHtml,     'Elo rating differential'),
            _row('Roster Quality',   rosterHtml,  'Talent composite delta'),
            _row('Passing Game',     passHtml,    'Off EPA – Def EPA allowed'),
            _row('Rushing Game',     rushHtml,    'Off EPA – Def EPA allowed'),
            _row('Turnovers',        toHtml,      'Rolling turnover margin'),
            _row('QB Health',        qbHtml),
            _row('Trench Play',      trenchHtml,  'O-line / D-line dominance'),
            _row('Travel / Rest',    restHtml),
        ].join('');

    return `
        <h3 style="margin:0 0 0.25rem; font-size:1rem; color:var(--accent-gold);">
            <i data-lucide="sparkles" style="width:14px; height:14px;"></i>
            Why ${pred_winner}?
        </h3>
        <div style="font-size:0.78rem; color:var(--text-secondary); margin-bottom:1rem;">
            ${away_team} @ ${home_team} &nbsp;·&nbsp; Week ${data.week} ${data.season}
        </div>

        <div style="display:flex; gap:1rem; margin-bottom:1rem; flex-wrap:wrap;">
            <div style="flex:1; min-width:120px; padding:10px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
                <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ML Pick</div>
                <div style="font-size:1.3rem; font-weight:800; color:${predColor};">${pred_winner}</div>
                <div style="font-size:0.75rem;">${pred_su_conf}% confidence</div>
                ${confBar}
            </div>
            <div style="flex:1; min-width:120px; padding:10px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
                <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ATS Pick</div>
                <div style="font-size:1.3rem; font-weight:800; color:var(--accent-gold);">${pred_ats_pick}</div>
                <div style="font-size:0.75rem; color:var(--text-secondary);">Against the spread</div>
            </div>
        </div>
        ${atsNote}

        <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:0.07em; color:var(--text-secondary); margin:0.75rem 0 0.25rem;">
            Key Factors <span style="font-size:0.65rem;">(home team perspective)</span>
        </div>
        ${rows}
        ${sourceNote}
    `;
}

// ── Event wiring ──────────────────────────────────────────────────────────────

async function handleExplainClick(btn) {
    const { season, week, home, away } = btn.dataset;
    showModal();
    content.innerHTML = '<div style="text-align:center; padding:2rem; color:var(--text-secondary);">Loading explanation…</div>';

    try {
        const url = `/api/predictions/explain?season=${season}&week=${week}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}`;
        const resp = await fetch(url);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            content.innerHTML = `<p style="color:var(--accent-red);">${err.error || 'No prediction stored for this game yet.'}</p>`;
            return;
        }
        const data = await resp.json();
        content.innerHTML = renderExplanation(data);
        // Re-init lucide icons inside modal
        if (window.lucide) window.lucide.createIcons();
    } catch (e) {
        content.innerHTML = `<p style="color:var(--accent-red);">Failed to load: ${e.message}</p>`;
    }
}

// ── Init ──────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    if (!isAdmin()) return;

    const btns = document.querySelectorAll('.pred-explain-btn');
    btns.forEach(btn => {
        btn.style.display = 'block';
        btn.addEventListener('click', e => {
            e.stopPropagation();
            handleExplainClick(btn);
        });
    });
});
