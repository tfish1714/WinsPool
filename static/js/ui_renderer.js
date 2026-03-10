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
                        ✨ Win: ${game.pred_winner} (${game.pred_su_conf}%)
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

    renderDraftBoard(board, activePick, myPlayerId, draftSummary, totalPlayers = 10) {
        const list = document.getElementById('draft-list');
        if (!list) return;

        list.innerHTML = board.map(item => {
            const isActive = item.pick === activePick;
            const isCompleted = !!item.team;
            const isMe = String(item.playerId) === String(myPlayerId);

            let badges = '';
            if (isCompleted && draftSummary) {
                const rd = Math.ceil(item.pick / totalPlayers);
                if (draftSummary.best_overall === item.team) badges += `<span class="best-pick-badge">🏆 BEST OVERALL UNIT</span>`;
                else if (draftSummary.best_by_round && draftSummary.best_by_round[rd] === item.team) badges += `<span class="best-pick-badge">🌟 Best in Rd ${rd}</span>`;
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
                        </div>` : ''}
                </li>
            `;
        }).join('');

        // Auto-scroll to active
        const activeItem = list.querySelector('.active');
        if (activeItem) activeItem.scrollIntoView({ behavior: 'smooth', block: 'center' });
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

    getTeamLogo(code) {
        if (!code) return '';
        const map = { 'LA': 'LAR', 'WAS': 'WSH' };
        return `https://a.espncdn.com/i/teamlogos/nfl/500/${map[code] || code}.png`;
    }
};
