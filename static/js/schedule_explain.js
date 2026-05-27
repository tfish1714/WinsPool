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

/** Render team-name advantage label. val > 0 = home advantage. */
function _teamAdv(val, home_team, away_team) {
    const hColor = 'var(--accent-green)';
    const aColor = 'var(--accent-gold)';
    const team  = val > 0 ? home_team : away_team;
    const color = val > 0 ? hColor : aColor;
    return `<span style="color:${color}; font-weight:700;">${team}</span>`;
}

/** Format a signed spread line as "TEAM -X.X" or "Pick'em". */
function _fmtLine(line, home_team, away_team) {
    if (line == null) return null;
    if (line === 0) return "Pick’em";
    const fav = line > 0 ? home_team : away_team;
    return `${fav} -${Math.abs(line).toFixed(1)}`;
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
            pred_ats_pick, model_spread, edge_vs_vegas, explanation: ex } = data;

    const isProfileOnly = ex?.source === 'profile';
    const homeFavored   = pred_winner === home_team;
    const predColor     = homeFavored ? 'var(--accent-green)' : 'var(--accent-gold)';
    const confBar       = _bar(pred_su_conf, predColor);

    const ms = model_spread ?? ex?.model_spread;
    const ev = edge_vs_vegas ?? ex?.edge_vs_vegas;

    // ── Top cards ──────────────────────────────────────────────────────────────

    // Card 1: ML Pick
    const pickCard = `
        <div style="flex:1; min-width:130px; padding:10px 12px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ML Pick</div>
            <div style="font-size:1.3rem; font-weight:800; color:${predColor};">${pred_winner}</div>
            <div style="font-size:0.75rem;">${pred_su_conf}% confidence</div>
            ${confBar}
        </div>`;

    // Card 2: Vegas vs Model lines
    const vegasLine  = ex?.vegas_line ?? null;
    const vegasStr   = _fmtLine(vegasLine,  home_team, away_team);
    const modelStr   = _fmtLine(ms,         home_team, away_team);
    let linesCard = '';
    if (vegasStr || modelStr) {
        let edgeLine = '';
        if (ev != null) {
            const edgeAbs = Math.abs(ev);
            const edgeTeam = ev > 0 ? home_team : away_team;
            const edgeColor = edgeAbs >= 3 ? 'var(--accent-green)' : (edgeAbs >= 1.5 ? 'var(--ink-2)' : 'var(--text-secondary)');
            const edgeSig   = edgeAbs >= 3 ? '⚡' : (edgeAbs >= 1.5 ? '·' : '');
            edgeLine = edgeAbs >= 0.5
                ? `<div style="margin-top:5px; font-size:0.73rem; color:${edgeColor};">${edgeSig} ${edgeTeam} +${edgeAbs.toFixed(1)} edge vs Vegas</div>`
                : `<div style="margin-top:5px; font-size:0.73rem; color:var(--text-secondary);">≈ Agrees with Vegas</div>`;
        }
        linesCard = `
            <div style="flex:1.4; min-width:150px; padding:10px 12px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
                <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em; margin-bottom:5px;">Lines</div>
                <div style="display:grid; grid-template-columns:auto 1fr; gap:2px 10px; font-size:0.82rem; align-items:center;">
                    ${vegasStr ? `<span style="color:var(--text-secondary); font-size:0.72rem;">Vegas</span><span style="font-weight:600; font-family:'JetBrains Mono',monospace;">${vegasStr}</span>` : ''}
                    ${modelStr ? `<span style="color:var(--text-secondary); font-size:0.72rem;">Model</span><span style="font-weight:600; font-family:'JetBrains Mono',monospace;">${modelStr}</span>` : ''}
                </div>
                ${edgeLine}
            </div>`;
    }

    // Card 3: ATS pick (only if it differs from SU, or always show)
    const atsDiffers = pred_ats_pick && pred_ats_pick !== pred_winner;
    const atsCard = pred_ats_pick ? `
        <div style="flex:1; min-width:110px; padding:10px 12px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ATS Pick</div>
            <div style="font-size:1.3rem; font-weight:800; color:${atsDiffers ? 'var(--accent-gold)' : predColor};">${pred_ats_pick}</div>
            <div style="font-size:0.72rem; color:var(--text-secondary);">${atsDiffers ? '⚡ differs from SU' : 'vs spread'}</div>
        </div>` : '';

    // ── Factor rows ────────────────────────────────────────────────────────────

    let eloHtml = '—';
    if (ex?.elo_diff != null) {
        const d = ex.elo_diff;
        const pts = Math.abs(d);
        eloHtml = pts < 5 ? '≈ Even'
            : `${_teamAdv(d, home_team, away_team)} +${pts.toFixed(0)} Elo pts`;
    }

    let rosterHtml = '—';
    if (ex?.roster_delta != null) {
        const d = ex.roster_delta;
        rosterHtml = Math.abs(d) < 0.2 ? '≈ Even'
            : `${_teamAdv(d, home_team, away_team)} edge (${d > 0 ? '+' : ''}${d.toFixed(2)})`;
    }

    let passHtml = '—';
    if (ex?.pass_epa_matchup != null) {
        const net = ex.pass_epa_matchup;
        passHtml = Math.abs(net) < 0.01 ? '≈ Even'
            : `${_teamAdv(net, home_team, away_team)} +${Math.abs(net).toFixed(3)} EPA/play`;
    }

    let rushHtml = '—';
    if (ex?.rush_epa_matchup != null) {
        const net = ex.rush_epa_matchup;
        rushHtml = Math.abs(net) < 0.005 ? '≈ Even'
            : `${_teamAdv(net, home_team, away_team)} +${Math.abs(net).toFixed(3)} EPA/play`;
    }

    let earlyDownHtml = '—';
    if (ex?.early_down_matchup != null) {
        const net = ex.early_down_matchup;
        earlyDownHtml = Math.abs(net) < 0.01 ? '≈ Even'
            : `${_teamAdv(net, home_team, away_team)} +${Math.abs(net).toFixed(3)} early EPA`;
    }

    let marginHtml = '—';
    if (ex?.point_diff_advantage != null) {
        const d = ex.point_diff_advantage;
        marginHtml = Math.abs(d) < 0.5 ? '≈ Even'
            : `${_teamAdv(d, home_team, away_team)} +${Math.abs(d).toFixed(1)} pts/gm`;
    }

    let toHtml = '—';
    if (ex?.turnover_margin != null) {
        const t = ex.turnover_margin;
        toHtml = Math.abs(t) < 0.1 ? '≈ Even'
            : `${_teamAdv(t, home_team, away_team)} +${Math.abs(t).toFixed(2)} TO/gm`;
    }

    let qbHtml = '—';
    {
        const hOut = ex?.home_qb_out ?? 0;
        const aOut = ex?.away_qb_out ?? 0;
        if (hOut && aOut) {
            qbHtml = `<span style="color:var(--accent-gold);">⚠ Both teams — backup QBs</span>`;
        } else if (hOut) {
            qbHtml = `<span style="color:var(--accent-red);">⚠ ${home_team} — backup QB</span>`;
        } else if (aOut) {
            qbHtml = `<span style="color:var(--accent-red);">⚠ ${away_team} — backup QB</span>`;
        } else if (ex?.home_qb_out !== null && ex?.home_qb_out !== undefined) {
            qbHtml = 'Both starters active';
        }
    }

    let restHtml = '—';
    if (ex?.rest_advantage != null) {
        const r = ex.rest_advantage;
        restHtml = Math.abs(r) < 0.5 ? '≈ Even rest'
            : `${_teamAdv(r, home_team, away_team)} +${Math.abs(r).toFixed(0)} days`;
    }

    let travelHtml = '—';
    if (ex?.travel_disadvantage != null) {
        const t = ex.travel_disadvantage;
        const awayColor = 'var(--accent-gold)';
        travelHtml = t < 0.1 ? '≈ Short haul'
            : `<span style="color:${awayColor}; font-weight:700;">${away_team}</span> ${(t * 1000).toFixed(0)} mi traveled`;
    }

    let trenchHtml = '—';
    if (ex?.trench_dominance != null) {
        const t = ex.trench_dominance;
        trenchHtml = Math.abs(t) < 0.3 ? '≈ Even'
            : `${_teamAdv(t, home_team, away_team)} trench edge`;
    }

    const sourceNote = isProfileOnly
        ? `<div style="margin-top:0.75rem; padding:6px 10px; border-radius:6px; background:rgba(255,255,255,0.04); font-size:0.72rem; color:var(--text-secondary);">
            ℹ Pre-season projection — factors reflect prior-season averages. Values update as ${data.season} game data becomes available.
           </div>`
        : '';

    const rows = [
        _row('Team Strength',  eloHtml,       isProfileOnly ? 'Prior season Elo' : 'Elo rating differential'),
        _row('Roster Quality', rosterHtml,    isProfileOnly ? 'Prior season talent delta' : 'Talent composite'),
        _row('Passing Game',   passHtml,      'Pass EPA matchup differential'),
        _row('Rushing Game',   rushHtml,      'Rush EPA matchup differential'),
        _row('Early Downs',    earlyDownHtml, 'Early-down EPA (pass 80% + rush 20%)'),
        _row('Score Margin',   marginHtml,    'Rolling avg point differential'),
        _row('Turnovers',      toHtml,        'Rolling turnover margin'),
        _row('QB Health',      qbHtml),
        _row('Trench Play',    trenchHtml,    'OL + DL performance z-score'),
        _row('Rest',           restHtml),
        _row('Travel',         travelHtml,    'Away team distance traveled'),
    ].join('');

    return `
        <h3 style="margin:0 0 0.25rem; font-size:1rem; color:var(--accent-gold);">
            <i data-lucide="sparkles" style="width:14px; height:14px;"></i>
            Why ${pred_winner}?
        </h3>
        <div style="font-size:0.78rem; color:var(--text-secondary); margin-bottom:1rem;">
            ${away_team} @ ${home_team} &nbsp;·&nbsp; Week ${data.week} ${data.season}
        </div>

        <div style="display:flex; gap:0.75rem; margin-bottom:1rem; flex-wrap:wrap;">
            ${pickCard}
            ${linesCard}
            ${atsCard}
        </div>

        <div style="font-size:0.72rem; text-transform:uppercase; letter-spacing:0.07em; color:var(--text-secondary); margin:0.75rem 0 0.25rem;">
            Key Factors <span style="font-size:0.65rem;">(home team perspective)</span>
        </div>
        ${rows}
        ${sourceNote}
    `;
}

// ── Feature audit section ─────────────────────────────────────────────────

function renderFeatureAuditSection(featureData, homeTeam, awayTeam) {
    if (!featureData) return '';

    const { nn_prob, xgb_prob, lr_prob, blended_prob, feature_importance, ensemble_version } = featureData;

    // Per-model probability row
    const fmt = p => p != null ? `${Math.round(p * 100)}%` : '—';
    const modelRow = `
        <div style="background:rgba(255,255,255,0.04); border-radius:8px; padding:10px 14px; margin-bottom:12px;">
            <div style="font-size:0.72rem; color:var(--text-secondary); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.05em;">Model breakdown</div>
            <div style="display:flex; gap:16px; align-items:center; flex-wrap:wrap;">
                <span style="font-size:0.82rem;">NN <strong>${fmt(nn_prob)}</strong></span>
                <span style="color:var(--glass-border);">·</span>
                <span style="font-size:0.82rem;">XGB <strong>${fmt(xgb_prob)}</strong></span>
                <span style="color:var(--glass-border);">·</span>
                <span style="font-size:0.82rem;">LR <strong>${fmt(lr_prob)}</strong></span>
                <span style="color:var(--glass-border);">→</span>
                <span style="font-size:0.85rem; color:var(--accent-green);">Blended <strong>${fmt(blended_prob)}</strong></span>
            </div>
        </div>`;

    // Feature importance bar chart — top 5 visible, rest collapsible
    const allFeatures = feature_importance || [];
    if (!allFeatures.length) return modelRow;

    const TOP_N = 5;
    const maxScore = Math.max(...allFeatures.map(f => Math.abs(f.score)), 0.0001);
    const auditId  = `feat-audit-${Date.now()}`;

    function _featureBar(f) {
        const pct   = Math.round((Math.abs(f.score) / maxScore) * 100);
        const dir   = f.direction === 'home' ? homeTeam : awayTeam;
        const color = f.direction === 'home' ? 'var(--accent-green)' : 'var(--accent-gold)';
        const label = f.feature.replace(/_/g, ' ');
        return `
            <div style="margin-bottom:7px;">
                <div style="display:flex; justify-content:space-between; font-size:0.75rem; margin-bottom:2px;">
                    <span style="color:var(--text-secondary);">${label}</span>
                    <span style="color:${color}; font-weight:600;">${dir} <span style="font-weight:400;color:var(--text-secondary);font-size:0.68rem;">${f.score.toFixed(3)}</span></span>
                </div>
                <div style="height:5px; background:rgba(255,255,255,0.07); border-radius:3px; overflow:hidden;">
                    <div style="width:${pct}%; height:100%; background:${color}; border-radius:3px;"></div>
                </div>
            </div>`;
    }

    const topBars  = allFeatures.slice(0, TOP_N).map(_featureBar).join('');
    const restBars = allFeatures.slice(TOP_N).map(_featureBar).join('');

    const expandToggle = restBars ? `
        <div id="${auditId}-rest" style="display:none;">${restBars}</div>
        <button id="${auditId}-toggle"
            onclick="(function(btn){
                const el = document.getElementById('${auditId}-rest');
                const open = el.style.display !== 'none';
                el.style.display = open ? 'none' : '';
                btn.textContent = open ? 'Show all ${allFeatures.length} features ▾' : 'Show less ▴';
            })(this)"
            style="margin-top:6px; background:none; border:none; color:var(--text-secondary); font-size:0.72rem;
                   cursor:pointer; padding:0; text-decoration:underline;">
            Show all ${allFeatures.length} features ▾
        </button>` : '';

    const versionNote = ensemble_version
        ? `<div style="font-size:0.68rem; color:var(--text-secondary); margin-top:8px; text-align:right;">${ensemble_version}</div>`
        : '';

    return `${modelRow}
        <div style="background:rgba(255,255,255,0.04); border-radius:8px; padding:10px 14px; margin-bottom:12px;">
            <div style="font-size:0.72rem; color:var(--text-secondary); margin-bottom:8px; text-transform:uppercase; letter-spacing:0.05em;">Top factors (blended importance)</div>
            ${topBars}
            ${expandToggle}
            ${versionNote}
        </div>`;
}

// ── Event wiring ──────────────────────────────────────────────────────────────

async function handleExplainClick(btn) {
    const { season, week, home, away } = btn.dataset;
    showModal();
    content.innerHTML = '<div style="text-align:center; padding:2rem; color:var(--text-secondary);">Loading explanation…</div>';

    try {
        const url = `/api/predictions/explain?season=${season}&week=${week}&home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}`;
        const token = AuthService.getToken();
        const resp = await fetch(url, {
            headers: token ? { 'Authorization': `Bearer ${token}` } : {}
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            content.innerHTML = `<p style="color:var(--accent-red);">${err.error || 'No prediction stored for this game yet.'}</p>`;
            return;
        }
        const data = await resp.json();

        // Feature audit fetch (graceful — 404 is normal for older games)
        let featureData = null;
        try {
            const featResp = await fetch(
                `/api/prediction_features/${season}/${week}/${encodeURIComponent(away)}/${encodeURIComponent(home)}`,
                { headers: token ? { 'Authorization': `Bearer ${token}` } : {} }
            );
            if (featResp.ok) featureData = await featResp.json();
        } catch (_) { /* no feature data — silently skip */ }

        const auditHtml = renderFeatureAuditSection(featureData, home, away);
        content.innerHTML = auditHtml + renderExplanation(data);
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

// Delegated listener — handles .pred-explain-btn buttons added dynamically after DOMContentLoaded
document.addEventListener('click', e => {
    const btn = e.target.closest('.pred-explain-btn');
    if (!btn) return;
    e.stopPropagation();
    handleExplainClick(btn);
});
