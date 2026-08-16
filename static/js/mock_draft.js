/**
 * WinsPool Mock Draft — standalone, login-free solo draft simulator.
 * Drives the entire 30-pick loop client-side against /api/mock-draft/*.
 * Deliberately independent of main.js / websocket_service.js / auth_service.js.
 */

const TEAM_LOGO_OVERRIDES = { LA: 'LAR', WAS: 'WSH' };
function teamLogo(code) {
    return `https://a.espncdn.com/i/teamlogos/nfl/500/${TEAM_LOGO_OVERRIDES[code] || code}.png`;
}

const BOT_PICK_DELAY_MS = 600;
const TOTAL_PICKS = 30;

class MockDraft {
    constructor() {
        this.setup = null;       // { pickSequence, teams, season, projections? }
        this.mySlot = null;
        this.rosters = {};       // slot -> [team, team, team]
        this.picked = new Set(); // teams already taken
        this.pickIndex = 0;      // index into pickSequence
        this.wildcardsSoFar = 0;
        this.totalBotPicks = 0;
        this.botPicksDone = 0;

        this.$slotSelect = document.getElementById('mock-slot-select');
        this.$slotGrid = document.getElementById('mock-slot-grid');
        this.$randomSlotBtn = document.getElementById('mock-random-slot');
        this.$board = document.getElementById('mock-draft-board');
        this.$status = document.getElementById('mock-status');
        this.$botTurn = document.getElementById('mock-bot-turn');
        this.$teamGrid = document.getElementById('mock-team-grid');
        this.$results = document.getElementById('mock-results');
        this.$yourTeams = document.getElementById('mock-your-teams');
        this.$rankings = document.getElementById('mock-rankings');
        this.$again = document.getElementById('mock-again');
        this.$error = document.getElementById('mock-error');

        this.$randomSlotBtn.addEventListener('click', () => this.chooseSlot(null));
        this.$again.addEventListener('click', () => this.restart());
    }

    async init() {
        try {
            const res = await fetch('/api/mock-draft/setup');
            if (!res.ok) throw new Error((await res.json()).error || 'Setup failed.');
            this.setup = await res.json();
            this.renderSlotGrid();
        } catch (err) {
            this.showError(err.message);
        }
    }

    showError(message) {
        this.$error.innerHTML = '';
        const msg = document.createElement('div');
        msg.textContent = message;
        this.$error.appendChild(msg);

        const restartBtn = document.createElement('button');
        restartBtn.type = 'button';
        restartBtn.className = 'btn';
        restartBtn.textContent = 'Restart Draft';
        restartBtn.addEventListener('click', () => this.restart());
        this.$error.appendChild(restartBtn);

        this.$error.classList.remove('hidden');
    }

    renderSlotGrid() {
        const slots = [...new Set(this.setup.pickSequence.map(e => e.slot))].sort((a, b) => a - b);
        this.$slotGrid.innerHTML = slots.map(slot =>
            `<button class="mock-slot-btn" data-slot="${slot}">Spot ${slot}</button>`
        ).join('');
        this.$slotGrid.querySelectorAll('.mock-slot-btn').forEach(btn => {
            btn.addEventListener('click', () => this.chooseSlot(parseInt(btn.dataset.slot, 10)));
        });
    }

    chooseSlot(slot) {
        const slots = [...new Set(this.setup.pickSequence.map(e => e.slot))];
        this.mySlot = slot === null ? slots[Math.floor(Math.random() * slots.length)] : slot;
        this.totalBotPicks = this.setup.pickSequence.filter(e => e.slot !== this.mySlot).length;
        slots.forEach(s => { this.rosters[s] = []; });

        this.$slotSelect.classList.add('hidden');
        this.$board.classList.remove('hidden');
        this.advance();
    }

    availableTeams() {
        return this.setup.teams.filter(t => !this.picked.has(t));
    }

    async advance() {
        if (this.pickIndex >= this.setup.pickSequence.length) {
            return this.finish();
        }
        const entry = this.setup.pickSequence[this.pickIndex];
        this.$status.textContent = `Pick ${entry.pick} of ${TOTAL_PICKS}`;

        if (entry.slot === this.mySlot) {
            this.renderHumanTurn();
        } else {
            await this.runBotTurn(entry);
        }
    }

    renderHumanTurn() {
        this.$botTurn.classList.add('hidden');
        this.$teamGrid.classList.remove('hidden');
        const projections = this.setup.projections;
        this.$teamGrid.innerHTML = this.availableTeams().map(team => {
            const proj = projections && projections[team];
            const sub = proj ? `<div class="team-btn-sub">${proj.projected_wins}W</div>` : '';
            return `
                <button class="team-btn" data-team="${team}">
                    <img src="${teamLogo(team)}" alt="${team}" style="width:32px;height:32px;object-fit:contain;">
                    <div style="flex:1;min-width:0"><div class="team-btn-city">${team}</div>${sub}</div>
                </button>`;
        }).join('');
        this.$teamGrid.querySelectorAll('.team-btn').forEach(btn => {
            btn.addEventListener('click', () => this.applyPick(this.mySlot, btn.dataset.team, false));
        });
    }

    async runBotTurn(entry) {
        this.$teamGrid.classList.add('hidden');
        this.$botTurn.classList.remove('hidden');
        this.$botTurn.textContent = `Bot ${entry.slot} is picking…`;
        await new Promise(resolve => setTimeout(resolve, BOT_PICK_DELAY_MS));

        const remaining = this.totalBotPicks - this.botPicksDone;
        let team, wasWildcard;
        try {
            const res = await fetch('/api/mock-draft/pick', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    season: this.setup.season,
                    availableTeams: this.availableTeams(),
                    wildcardsSoFar: this.wildcardsSoFar,
                    botPicksRemaining: remaining,
                }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.error || 'Bot pick failed.');
            }
            ({ team, wasWildcard } = await res.json());
        } catch (err) {
            return this.showError(err.message || 'Bot pick failed. Please restart the draft.');
        }
        if (wasWildcard) this.wildcardsSoFar += 1;
        this.botPicksDone += 1;
        this.$botTurn.textContent = `Bot ${entry.slot} picks ${team}${wasWildcard ? ' (wildcard!)' : ''}`;
        await new Promise(resolve => setTimeout(resolve, BOT_PICK_DELAY_MS));
        this.applyPick(entry.slot, team, wasWildcard);
    }

    applyPick(slot, team, _wasWildcard) {
        this.rosters[slot].push(team);
        this.picked.add(team);
        this.pickIndex += 1;
        this.advance();
    }

    async finish() {
        this.$board.classList.add('hidden');
        this.$results.classList.remove('hidden');

        this.$yourTeams.innerHTML = `<h3>Your teams</h3>` + this.rosters[this.mySlot].map(team =>
            `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:10px;">
                <img src="${teamLogo(team)}" style="width:20px;height:20px;">${team}
            </span>`
        ).join('');

        try {
            const res = await fetch('/api/mock-draft/results', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ season: this.setup.season, rosters: this.rosters }),
            });
            if (!res.ok) throw new Error((await res.json()).error || 'Ranking failed.');
            const { rankings } = await res.json();
            rankings.sort((a, b) => a.rank - b.rank);
            this.$rankings.innerHTML = `<h3>Final Rankings</h3>` + rankings.map(r => {
                const isYou = r.slot === this.mySlot;
                const label = isYou ? 'You' : `Bot ${r.slot}`;
                const totalText = 'totalProjectedWins' in r ? ` — ${r.totalProjectedWins}W` : '';
                return `<div class="mock-rank-row${isYou ? ' is-you' : ''}">#${r.rank} ${label}${totalText}</div>`;
            }).join('');
        } catch (err) {
            this.showError(err.message);
        }
    }

    restart() {
        this.$results.classList.add('hidden');
        this.$error.classList.add('hidden');
        this.mySlot = null;
        this.rosters = {};
        this.picked = new Set();
        this.pickIndex = 0;
        this.wildcardsSoFar = 0;
        this.botPicksDone = 0;
        this.$slotSelect.classList.remove('hidden');
        this.init();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new MockDraft().init();
});
