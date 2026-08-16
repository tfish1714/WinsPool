/**
 * WinsPool Mock Draft — standalone, login-free solo draft simulator.
 * Drives the entire pick loop client-side against /api/mock-draft/* (30
 * picks under a full draft order, fewer if the season's draft_order_rules
 * data is partial — see get_pick_sequence()).
 * Deliberately independent of main.js / websocket_service.js / auth_service.js.
 */

const TEAM_LOGO_OVERRIDES = { LA: 'LAR', WAS: 'WSH' };
function teamLogo(code) {
    return `https://a.espncdn.com/i/teamlogos/nfl/500/${TEAM_LOGO_OVERRIDES[code] || code}.png`;
}

function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const BOT_PICK_DELAY_MS = 600;

// Cosmetic only — assigned per slot for the session, always shown alongside
// the slot number so it's still clear which pick is which.
const BOT_NAME_POOL = [
    'Couch Quarterback', 'Waiver Wire Wizard', 'The Armchair GM', 'Blitzkrieg Bob',
    'Sunday Scaries', 'Sleeper Pick Steve', 'Mock Draft Menace', 'Two-Minute Drill',
    'The Tailgate Legend', 'Prime Time Pete', 'Chalk Eater', 'Upset Special',
    'The Stat Nerd', 'The Homer', 'Hot Take Machine', 'Turf Monster',
    'Fantasy Phenom', 'The Underdog', 'Garbage Time Gary', 'The Analytics Guy',
];

class MockDraft {
    constructor() {
        this.setup = null;       // { pickSequence, teams, season, teamSchedules, projections? }
        this.mySlot = null;
        this.rosters = {};       // slot -> [team, team, team]
        this.botNames = {};      // slot -> cosmetic name (non-human slots only)
        this.pickHistory = [];   // [{ pick, slot, team, wasWildcard }] in draft order
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
        this.$rosters = document.getElementById('mock-rosters');
        this.$pickHistory = document.getElementById('mock-pick-history');
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
        restartBtn.className = 'btn-primary';
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
        this.assignBotNames(slots);

        this.$slotSelect.classList.add('hidden');
        this.$board.classList.remove('hidden');
        this.renderRosters();
        this.renderPickHistory();
        this.advance();
    }

    assignBotNames(slots) {
        const pool = [...BOT_NAME_POOL];
        for (let i = pool.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [pool[i], pool[j]] = [pool[j], pool[i]];
        }
        this.botNames = {};
        let i = 0;
        slots.forEach(s => {
            if (s !== this.mySlot) {
                this.botNames[s] = pool[i % pool.length];
                i += 1;
            }
        });
    }

    slotLabel(slot) {
        return slot === this.mySlot ? 'You' : `${this.botNames[slot]} – Slot ${slot}`;
    }

    availableTeams() {
        return this.setup.teams.filter(t => !this.picked.has(t));
    }

    async advance() {
        if (this.pickIndex >= this.setup.pickSequence.length) {
            return this.finish();
        }
        const entry = this.setup.pickSequence[this.pickIndex];
        this.$status.textContent = `Pick ${entry.pick} of ${this.setup.pickSequence.length}`;

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
        const schedules = this.setup.teamSchedules;
        this.$teamGrid.innerHTML = this.availableTeams().map(team => {
            const proj = projections && projections[team];
            const sub = proj ? `<div class="team-btn-sub">${proj.projected_wins}W</div>` : '';
            const schedule = schedules && schedules[team] ? schedules[team].join('\n') : '';
            return `
                <button class="team-btn" data-team="${team}" title="${esc(schedule)}">
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
        this.$botTurn.textContent = `${this.slotLabel(entry.slot)} is picking…`;
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
        this.$botTurn.textContent = `${this.slotLabel(entry.slot)} picks ${team}${wasWildcard ? ' (wildcard!)' : ''}`;
        await new Promise(resolve => setTimeout(resolve, BOT_PICK_DELAY_MS));
        this.applyPick(entry.slot, team, wasWildcard);
    }

    applyPick(slot, team, wasWildcard) {
        const entry = this.setup.pickSequence[this.pickIndex];
        this.pickHistory.push({ pick: entry.pick, slot, team, wasWildcard });
        this.rosters[slot].push(team);
        this.picked.add(team);
        this.pickIndex += 1;
        this.renderRosters();
        this.renderPickHistory();
        this.advance();
    }

    renderRosters() {
        const slots = [...new Set(this.setup.pickSequence.map(e => e.slot))].sort((a, b) => a - b);
        this.$rosters.innerHTML = slots.map(slot => {
            const isYou = slot === this.mySlot;
            const teams = (this.rosters[slot] || []).map(t =>
                `<img src="${teamLogo(t)}" alt="${t}" title="${t}" style="width:18px;height:18px;object-fit:contain;">`
            ).join('') || '<span style="opacity:0.5;">—</span>';
            return `
                <div class="mock-roster-row${isYou ? ' is-you' : ''}">
                    <div style="flex:1;min-width:0;">${esc(this.slotLabel(slot))}</div>
                    <div class="mock-roster-teams">${teams}</div>
                </div>`;
        }).join('');
    }

    renderPickHistory() {
        this.$pickHistory.innerHTML = this.setup.pickSequence.map((entry, idx) => {
            const done = this.pickHistory[idx];
            const isCurrent = !done && idx === this.pickIndex;
            let right;
            if (done) {
                right = `<span class="mock-history-team">
                    <img src="${teamLogo(done.team)}" alt="${done.team}" style="width:16px;height:16px;object-fit:contain;">
                    ${done.team}${done.wasWildcard ? ' 🎲' : ''}
                </span>`;
            } else if (isCurrent) {
                right = '<span style="opacity:0.7;">on the clock</span>';
            } else {
                right = '<span style="opacity:0.4;">—</span>';
            }
            return `
                <div class="mock-history-row${isCurrent ? ' is-current' : ''}">
                    <span>#${entry.pick} ${esc(this.slotLabel(entry.slot))}</span>
                    ${right}
                </div>`;
        }).join('');
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
            const graded = rankings.length > 0 && rankings.every(r => r.graded);
            if (!graded) {
                this.$rankings.innerHTML = `<h3>Final Rankings</h3>
                    <p>Rankings unavailable — no projection data for this season yet.</p>`;
            } else {
                rankings.sort((a, b) => a.rank - b.rank);
                this.$rankings.innerHTML = `<h3>Final Rankings</h3>` + rankings.map(r => {
                    const isYou = r.slot === this.mySlot;
                    const totalText = 'totalProjectedWins' in r ? ` — ${r.totalProjectedWins}W` : '';
                    return `<div class="mock-rank-row${isYou ? ' is-you' : ''}">#${r.rank} ${esc(this.slotLabel(r.slot))}${totalText}</div>`;
                }).join('');
            }
        } catch (err) {
            this.showError(err.message);
        }
    }

    restart() {
        this.$board.classList.add('hidden');
        this.$results.classList.add('hidden');
        this.$error.classList.add('hidden');
        this.mySlot = null;
        this.rosters = {};
        this.botNames = {};
        this.pickHistory = [];
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
