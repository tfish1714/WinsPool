/**
 * WinsPool UI Renderer Module
 * Handles all DOM manipulation and content generation.
 */

export const UiRenderer = {
    renderStandings(data) {
        const tbody = document.querySelector('#standings-table tbody');
        if (!tbody) return;

        tbody.innerHTML = data.map((row, idx) => `
            <tr class="${idx < 3 ? 'top-tier' : ''}">
                <td>${idx + 1}</td>
                <td class="font-bold">${row.entrant}</td>
                <td>${row.total_wins}</td>
                <td><span class="win-badge">${row.weekly_wins || 0}</span></td>
                <td>${row.possible_wins || 0}</td>
            </tr>
        `).join('');
    },

    renderSchedule(data) {
        const container = document.getElementById('schedule-container');
        if (!container) return;

        container.innerHTML = data.map(game => `
            <div class="game-card ${game.is_live ? 'live-border' : ''}">
                <div class="game-info">
                    <div class="team">
                        <span>${game.away_team}</span>
                        <span class="score">${game.away_score}</span>
                    </div>
                    <div class="vs">@</div>
                    <div class="team">
                        <span>${game.home_team}</span>
                        <span class="score">${game.home_score}</span>
                    </div>
                </div>
                ${game.is_live ? `<div class="live-badge">LIVE - Q${game.period} ${game.clock}</div>` : ''}
                ${game.pred_winner ? `
                    <div class="ai-prediction">
                        Win: ${game.pred_winner} (${game.pred_su_conf}%)
                    </div>
                ` : ''}
            </div>
        `).join('');
    },

    renderDraftOrder(data) {
        const container = document.getElementById('preview-list');
        if (!container) return;

        container.innerHTML = data.map((player, idx) => `
            <li>
                <span class="font-bold">#${idx + 1}</span>
                <span style="margin-left: 8px;">${player}</span>
            </li>
        `).join('');
    },

    renderDraftBoard(board, activePick, myPlayerId, draftSummary, totalPlayers = 10, role = null, preseasonPredictions = null) {
        const list = document.getElementById('draft-list');
        if (!list) return;

        list.innerHTML = board.map(item => {
            const isActive = item.pick === activePick;
            const isCompleted = !!item.team;
            const isMe = String(item.playerId) === String(myPlayerId);

            let badges = '';
            if (isCompleted && draftSummary) {
                const rd = Math.ceil(item.pick / totalPlayers);
                if (draftSummary.best_overall === item.team) badges += `<span class="best-pick-badge"><i data-lucide="award" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px;"></i> BEST OVERALL UNIT</span>`;
                else if (draftSummary.best_by_round && draftSummary.best_by_round[rd] === item.team) badges += `<span class="best-pick-badge"><i data-lucide="target" style="width:12px;height:12px;display:inline-block;vertical-align:middle;margin-right:4px;"></i> Best in Rd ${rd}</span>`;
            }

            return `
                <li class="draft-item ${isActive ? 'active' : ''} ${isCompleted ? 'completed' : ''}">
                    <div class="pick-main">
                        <span class="pick-number">#${item.pick}</span>
                        <span class="pick-player">${item.playerName}${isMe ? ' (YOU)' : ''}</span>
                    </div>
                    ${isCompleted ? `
                        <div class="pick-result">
                            <img src="${this.getTeamLogo(item.team)}" class="tiny-logo" alt="">
                            <span class="pick-team">${item.team}</span>
                            ${badges}
                            ${role === 'admin' ? `
                                <div class="admin-draft-pred" style="font-size: 0.75rem; color: #aaa; margin-top: 2px;">
                                    ${preseasonPredictions && preseasonPredictions[item.team] ? `<span style="margin-right:8px;">Base: ${typeof preseasonPredictions[item.team] === 'object' ? preseasonPredictions[item.team].projected_wins : preseasonPredictions[item.team]}W</span>` : ''}
                                </div>
                            ` : ''}
                        </div>` : ''}
                </li>
            `;
        }).join('');

        // Auto-scroll to active
        const activeItem = list.querySelector('.active');
        if (activeItem && list) {
            list.scrollTo({
                top: activeItem.offsetTop - (list.clientHeight / 2) + (activeItem.clientHeight / 2),
                behavior: 'smooth'
            });
        }
    },

    renderAdminPortfolio(draftBoard, allPlayers, preseasonPredictions) {
        const container = document.getElementById('admin-portfolio-content');
        if (!container) return;

        if (!allPlayers || allPlayers.length === 0) {
            container.innerHTML = '<p>No players available.</p>';
            return;
        }

        const activePlayerIds = new Set(draftBoard.map(pick => String(pick.playerId)));
        const portfolios = {};
        allPlayers.forEach(p => {
            if (!activePlayerIds.has(String(p.playerId))) return;
            portfolios[p.playerId] = { playerName: p.playerName, teams: [], totalBase: 0.0 };
        });

        draftBoard.forEach(pick => {
            if (pick.team && portfolios[pick.playerId]) {
                const team = pick.team;
                let baseWins = 0;
                if (preseasonPredictions && preseasonPredictions[team]) {
                    baseWins = typeof preseasonPredictions[team] === 'object'
                        ? parseFloat(preseasonPredictions[team].projected_wins)
                        : parseFloat(preseasonPredictions[team]);
                }
                portfolios[pick.playerId].teams.push({ team, base: baseWins });
                portfolios[pick.playerId].totalBase += baseWins;
            }
        });

        const sorted = Object.values(portfolios).sort((a, b) => b.totalBase - a.totalBase);

        let html = `
            <table class="wins-table" style="width:100%; border-collapse:collapse; text-align:left;">
                <thead>
                    <tr>
                        <th style="padding:0.5rem; border-bottom:1px solid var(--glass-border);">Player</th>
                        <th style="padding:0.5rem; border-bottom:1px solid var(--glass-border);">Teams</th>
                        <th style="padding:0.5rem; border-bottom:1px solid var(--glass-border); text-align:right;">Projected Wins</th>
                    </tr>
                </thead>
                <tbody>
        `;

        sorted.forEach(p => {
            const teamStrings = p.teams.map(t =>
                `<span style="display:inline-block; padding:2px 6px; background:rgba(255,255,255,0.1); border-radius:4px; margin:2px; font-size:0.8rem;">
                    <img src="${this.getTeamLogo(t.team)}" style="width:14px;height:14px;vertical-align:middle;margin-right:4px;">${t.team} (${t.base.toFixed(1)}W)
                </span>`
            ).join('');
            html += `
                <tr>
                    <td style="padding:0.5rem; border-bottom:1px solid rgba(255,255,255,0.05); font-weight:bold;">${p.playerName}</td>
                    <td style="padding:0.5rem; border-bottom:1px solid rgba(255,255,255,0.05);">${teamStrings || '<span style="color:#666;">No teams yet</span>'}</td>
                    <td style="padding:0.5rem; border-bottom:1px solid rgba(255,255,255,0.05); text-align:right; font-weight:bold;">${p.totalBase.toFixed(1)}</td>
                </tr>
            `;
        });

        html += `</tbody></table>`;
        container.innerHTML = html;
    },

    renderTeamGrid(teams, selectedTeam, role, predictions, schedules) {
        const grid = document.getElementById('teams-grid');
        if (!grid) return;

        let sorted = [...teams];
        if (role === 'admin' && predictions) {
            sorted.sort((a, b) => {
                const pA = (predictions[a] && typeof predictions[a] === 'object') ? predictions[a].projected_wins : (predictions[a] || 0);
                const pB = (predictions[b] && typeof predictions[b] === 'object') ? predictions[b].projected_wins : (predictions[b] || 0);
                return pB - pA;
            });
        }

        grid.innerHTML = sorted.map(team => {
            const isSelected = team === selectedTeam;
            const pred = (role === 'admin' && predictions) ? predictions[team] : null;
            const schedule = schedules && schedules[team] ? schedules[team].join('\n') : '';

            let predHtml = '';
            if (pred) {
                if (typeof pred === 'object') {
                    predHtml = `
                        <div class="pred-details">
                            <span class="preseason-wins">${pred.projected_wins}W</span>
                            <span class="std-dev" title="Standard Deviation">±${pred.std_dev}</span>
                        </div>
                    `;
                } else {
                    predHtml = `<span class="preseason-wins">${pred}W</span>`;
                }
            }

            return `
                <div class="team-card ${isSelected ? 'selected' : ''}"
                     title="${schedule}"
                     data-team="${team}">
                    <img src="${this.getTeamLogo(team)}" class="team-card-logo" alt="${team}">
                    <div class="team-card-info">
                        <span class="team-name">${team}</span>
                        ${predHtml}
                    </div>
                </div>
            `;
        }).join('');
    },

    renderPlayerSelectionGrid(players, onToggle) {
        const grid = document.getElementById('player-grid');
        if (!grid) return;

        grid.innerHTML = players.map(p => `
            <label class="admin-player-label">
                <input type="checkbox" value="${p.playerId}" class="player-checkbox">
                <span>${p.nickName || p.fullName}</span>
            </label>
        `).join('');

        const checks = grid.querySelectorAll('.player-checkbox');
        checks.forEach(c => c.onchange = onToggle);
    },

    renderAdminSeasonDropdown(seasons) {
        const select = document.getElementById('delete-season-select');
        if (!select) return;

        select.innerHTML = '<option value="">Select Season to Wipe...</option>' +
            seasons.map(s => `<option value="${s}">Season ${s}</option>`).join('');
    },

    renderPickQueue(board, activePick, allPlayers) {
        const container = document.getElementById('pick-queue');
        const footer    = document.getElementById('pick-queue-footer');
        if (!container) return;

        const totalPlayers = (allPlayers || []).length || 10;
        const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s) % 60).padStart(2, '0')}`;
        const SLOW_SECS = 120;
        const _esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

        // Windowed range: 3 past + active + 2 next
        const start  = Math.max(1, activePick - 3);
        const end    = Math.min(board.length, activePick + 2);
        const window = board.filter(x => x.pick >= start && x.pick <= end);

        container.innerHTML = window.map(item => {
            const isPast   = item.pick < activePick;
            const isActive = item.pick === activePick;
            const round    = Math.ceil(item.pick / totalPlayers);
            const took     = item.time_taken_seconds;
            const isSlow   = took != null && took >= SLOW_SECS;

            let rightHtml;
            if (isPast && item.team) {
                const tookFmt = took != null ? mmss(took) : '';
                rightHtml = `
                    <div style="display:flex;align-items:center;gap:10px">
                        ${tookFmt ? `<span class="mono" style="font-size:11px;color:${isSlow ? 'var(--warn)' : 'var(--ink-3)'}">${tookFmt}</span>` : ''}
                        <span style="background:#444;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace">${_esc(item.team)}</span>
                    </div>`;
            } else if (isActive) {
                rightHtml = `<span class="mono-pill"><span class="dot pulse"></span>picking</span>`;
            } else {
                rightHtml = `<span class="mono" style="color:var(--ink-3);font-size:11px">—</span>`;
            }

            return `
                <div class="q-row" style="
                    opacity:${isPast ? 0.6 : 1};
                    background:${isActive ? 'rgba(255,255,255,0.025)' : 'transparent'};
                    border-color:${isActive ? 'var(--line-strong)' : 'var(--line)'}">
                    <span class="numeral" style="font-size:22px;color:${isActive ? 'var(--ink)' : 'var(--ink-3)'};min-width:28px">${item.pick}</span>
                    <div style="flex:1;min-width:0">
                        <div style="font-size:14px;font-weight:600">${_esc(item.playerName)}</div>
                        <div class="mono" style="font-size:11px;color:var(--ink-3)">R${round}&middot;P${item.pick}</div>
                    </div>
                    ${rightHtml}
                </div>`;
        }).join('');

        if (!footer) return;

        // Stats footer: avg + slowest for the current round
        const currentRound    = Math.ceil(activePick / totalPlayers);
        const roundPastPicks  = board.filter(x =>
            x.pick < activePick &&
            Math.ceil(x.pick / totalPlayers) === currentRound &&
            x.time_taken_seconds != null
        );

        if (roundPastPicks.length === 0) { footer.innerHTML = ''; return; }

        const avg     = roundPastPicks.reduce((s, x) => s + x.time_taken_seconds, 0) / roundPastPicks.length;
        const slowest = roundPastPicks.reduce((a, b) => b.time_taken_seconds > a.time_taken_seconds ? b : a);
        const slowColor = slowest.time_taken_seconds >= SLOW_SECS ? 'var(--warn)' : 'var(--ink-2)';

        footer.innerHTML = `Avg pick this round &middot; <b>${mmss(avg)}</b> &nbsp;&middot;&nbsp; Slowest &middot; <b style="color:${slowColor}">${_esc(slowest.playerName)} ${mmss(slowest.time_taken_seconds)}</b>`;
    },

    getTeamLogo(code) {
        if (!code) return '';
        const map = { 'LA': 'LAR', 'WAS': 'WSH' };
        return `https://a.espncdn.com/i/teamlogos/nfl/500/${map[code] || code}.png`;
    }
};
