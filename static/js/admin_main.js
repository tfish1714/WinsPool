import { ApiService } from './api.js';
import { UiRenderer } from './ui_renderer.js';
import { AuthService } from './auth_service.js';

/**
 * WinsPool Admin Application Module (Refactored)
 * Handles draft setup, player management, and system utilities.
 */

class AdminApp {
    constructor() {
        this.playerId = AuthService.getCredentials().playerId;
        this.players = [];
        this.selectedPlayerIds = new Set();
    }

    init() {
        console.log('[Admin] Initializing modular Admin Portal...');
        this.setupTabHandlers();
        this.setupActionHandlers();

        if (this.playerId) {
            this.fetchInitialData();
        } else {
            console.error('[Admin] No playerId found. Access denied.');
        }
    }

    setupTabHandlers() {
        const tabBtns = document.querySelectorAll('.admin-tabs .tab-btn');
        const tabContents = document.querySelectorAll('.tab-content');

        tabBtns.forEach(btn => {
            btn.onclick = () => {
                const target = btn.dataset.tab;
                console.log(`[Admin] Switching to tab: ${target}`);
                tabBtns.forEach(b => b.classList.remove('active'));
                tabContents.forEach(c => c.classList.add('hidden'));
                btn.classList.add('active');
                const content = document.getElementById(target);
                if (content) {
                    content.classList.remove('hidden');
                    console.log(`[Admin] Section '${target}' visible.`);
                } else {
                    console.error(`[Admin] Target section '${target}' not found in DOM!`);
                }
                if (target === 'members-section') {
                    this.initMembersTab();
                }
            };
        });
    }

    /* ------------------------------------------------------------------
       Members Tab
       ------------------------------------------------------------------ */

    initMembersTab() {
        if (this._membersTabReady) return;
        this._membersTabReady = true;

        const sel = document.getElementById('members-season-select');
        if (!sel) return;

        // Populate season dropdown from already-fetched seasons, or re-fetch
        const populate = (seasons) => {
            sel.innerHTML = seasons.map(s => `<option value="${s}">${s} Season</option>`).join('');
            // Load the most recent season by default
            if (seasons.length) this.loadMembers(seasons[0]);
        };

        ApiService.fetchSeasons(this.playerId)
            .then(({ seasons }) => populate(seasons))
            .catch(() => {
                sel.innerHTML = '<option value="">Error loading seasons</option>';
            });

        sel.addEventListener('change', () => {
            if (sel.value) this.loadMembers(parseInt(sel.value, 10));
        });
    }

    async loadMembers(season) {
        const container = document.getElementById('members-rows');
        const meta = document.getElementById('members-meta');
        if (!container) return;

        container.innerHTML = '<div style="padding:40px 0; text-align:center; color:var(--ink-3); font-size:13px;">Loading…</div>';

        try {
            const { members } = await ApiService.fetchMembers(this.playerId, season);
            this.renderMemberRows(members, season, container, meta);
        } catch (e) {
            container.innerHTML = `<div style="padding:40px 0; text-align:center; color:var(--neg); font-size:13px;">${this._esc(e.message)}</div>`;
        }
    }

    renderMemberRows(members, season, container, meta) {
        if (!members || members.length === 0) {
            container.innerHTML = '<div style="padding:40px 0; text-align:center; color:var(--ink-3); font-size:13px;">No members found for this season.</div>';
            if (meta) meta.textContent = '';
            return;
        }

        const unpaid = members.filter(m => !m.paid).length;
        if (meta) {
            meta.textContent = `${members.length} members · ${unpaid} unpaid`;
        }

        container.innerHTML = '';
        members.forEach(m => {
            const row = document.createElement('div');
            row.className = 'members-row';
            row.dataset.playerId = m.playerId;

            const isAdmin = (m.role || '').toLowerCase() === 'admin';
            const rolePill = isAdmin
                ? `<span class="mono-pill" style="border-color:var(--leader); color:var(--leader); font-size:10px; padding:3px 8px;">${this._esc(m.role)}</span>`
                : `<span style="font-family:'JetBrains Mono',monospace; font-size:12px; color:var(--ink-3);">${this._esc(m.role || '—')}</span>`;

            const paidState = m.paid ? 'paid' : 'unpaid';
            row.innerHTML = `
                <div class="members-row-name">${this._esc(m.fullName)}</div>
                <div class="members-row-email">${this._esc(m.email || '—')}</div>
                <div>${rolePill}</div>
                <div class="members-row-order">#${m.draftOrder ?? '—'}</div>
                <div>
                    <button class="paid-toggle" data-paid="${m.paid ? '1' : '0'}" aria-label="Toggle paid">
                        <span class="paid-toggle__track" style="background:${m.paid ? 'var(--pos)' : 'var(--line-strong)'};">
                            <span class="paid-toggle__thumb" style="left:${m.paid ? '19px' : '3px'}; background:${m.paid ? '#fff' : 'var(--ink-3)'};"></span>
                        </span>
                        <span class="paid-toggle__label" style="color:${m.paid ? 'var(--pos)' : 'var(--ink-3)'};">${paidState}</span>
                    </button>
                </div>
            `;

            const toggleBtn = row.querySelector('.paid-toggle');
            toggleBtn.addEventListener('click', () => this.togglePaid(toggleBtn, m, season, meta, container));

            container.appendChild(row);
        });
    }

    async togglePaid(btn, member, season, meta, container) {
        const currentPaid = btn.dataset.paid === '1';
        const newPaid = !currentPaid;

        // Optimistic UI update
        btn.dataset.paid = newPaid ? '1' : '0';
        const track = btn.querySelector('.paid-toggle__track');
        const thumb = btn.querySelector('.paid-toggle__thumb');
        const label = btn.querySelector('.paid-toggle__label');
        track.style.background = newPaid ? 'var(--pos)' : 'var(--line-strong)';
        thumb.style.left = newPaid ? '19px' : '3px';
        thumb.style.background = newPaid ? '#fff' : 'var(--ink-3)';
        label.textContent = newPaid ? 'paid' : 'unpaid';
        label.style.color = newPaid ? 'var(--pos)' : 'var(--ink-3)';

        // Update meta count
        const rows = container.querySelectorAll('.members-row');
        const unpaidCount = Array.from(rows).filter(r => {
            const tb = r.querySelector('.paid-toggle');
            return tb && tb.dataset.paid === '0';
        }).length;
        if (meta) meta.textContent = `${rows.length} members · ${unpaidCount} unpaid`;

        try {
            const { ok } = await ApiService.setMemberPaid(this.playerId, member.playerId, season, newPaid);
            if (!ok) throw new Error('Server rejected update');
        } catch (e) {
            // Revert on failure
            btn.dataset.paid = currentPaid ? '1' : '0';
            track.style.background = currentPaid ? 'var(--pos)' : 'var(--line-strong)';
            thumb.style.left = currentPaid ? '19px' : '3px';
            thumb.style.background = currentPaid ? '#fff' : 'var(--ink-3)';
            label.textContent = currentPaid ? 'paid' : 'unpaid';
            label.style.color = currentPaid ? 'var(--pos)' : 'var(--ink-3)';
            alert(`Failed to update: ${e.message}`);
        }
    }

    async fetchInitialData() {
        try {
            const [players, { seasons }] = await Promise.all([
                ApiService.fetchPlayers(this.playerId),
                ApiService.fetchSeasons(this.playerId)
            ]);

            this.players = players;
            UiRenderer.renderPlayerSelectionGrid(players, () => this.updatePlayerCount());
            UiRenderer.renderAdminSeasonDropdown(seasons);
            this.renderPlayerList(players);
        } catch (e) {
            alert(`Fetch failed: ${e.message}`);
        }
    }

    updatePlayerCount() {
        const checks = document.querySelectorAll('.player-checkbox:checked');
        this.selectedPlayerIds = new Set(Array.from(checks).map(c => c.value));
        const countEl = document.getElementById('player-count');
        if (countEl) {
            countEl.textContent = `Selected: ${this.selectedPlayerIds.size}/10`;
            countEl.style.color = (this.selectedPlayerIds.size === 10) ? 'var(--accent-green)' : '';
        }
    }

    setupActionHandlers() {
        document.getElementById('generate-btn')?.addEventListener('click', () => this.generateSeason());
        document.getElementById('delete-season-btn')?.addEventListener('click', () => this.deleteSeason());
        document.getElementById('preview-btn')?.addEventListener('click', () => this.previewDraft());
        document.getElementById('create-player-btn')?.addEventListener('click', () => this.createPlayer());
        document.getElementById('scrape-predictions-btn')?.addEventListener('click', () => this.scrapePredictions());

        // Recap Handlers
        document.getElementById('draft-recap-preview-btn')?.addEventListener('click', () => this.previewDraftRecapPrompt());
        document.getElementById('recap-preview-prompt-btn')?.addEventListener('click', () => this.previewRecapPrompt());
        document.getElementById('recap-generate-ai-btn')?.addEventListener('click', () => this.generateRecapAI());
        document.getElementById('recap-broadcast-btn')?.addEventListener('click', () => this.broadcastRecap());
    }

    /* ------------------------------------------------------------------
       Player List Rendering
       ------------------------------------------------------------------ */

    renderPlayerList(players) {
        const container = document.getElementById('admin-player-list');
        if (!container) return;

        if (!players || players.length === 0) {
            container.innerHTML = '<p style="color: var(--text-secondary); font-size: 0.9rem;">No players found.</p>';
            return;
        }

        container.innerHTML = '';
        players.forEach(p => {
            const card = document.createElement('div');
            card.className = 'player-mgmt-card';
            card.style.cssText = 'border: 1px solid var(--glass-border); border-radius: 8px; padding: 0.75rem 1rem; background: rgba(0,0,0,0.15);';
            card.setAttribute('data-player-id', p.playerId);

            // Display mode
            const displayHtml = `
                <div class="player-mgmt-display" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem;">
                    <div>
                        <strong style="color: var(--text-primary);">${this._esc(p.fullName)}</strong>
                        <span style="color: var(--text-secondary); font-size: 0.85rem; margin-left: 0.5rem;">(${this._esc(p.nickName || '')})</span>
                        <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 2px;">
                            ${this._esc(p.email || '')}${p.cell ? ' | ' + this._esc(p.cell) : ''}
                        </div>
                    </div>
                    <div style="display: flex; gap: 0.35rem; flex-wrap: wrap;">
                        <button class="btn-edit-player btn-primary" style="padding: 0.35rem 0.75rem; font-size: 0.8rem; min-height: 44px; background: #444; border-color: #666;">Edit</button>
                        <button class="btn-reset-pw btn-primary" style="padding: 0.35rem 0.75rem; font-size: 0.8rem; min-height: 44px; background: #6b4c9a; border-color: #5a3c85;">Reset Password</button>
                        <button class="btn-temp-pw btn-primary" style="padding: 0.35rem 0.75rem; font-size: 0.8rem; min-height: 44px; background: var(--accent-red); border-color: var(--accent-red);">Set Temp Password</button>
                    </div>
                </div>
            `;

            // Edit mode (hidden by default)
            const editHtml = `
                <div class="player-mgmt-edit" style="display: none; flex-direction: column; gap: 0.5rem; margin-top: 0.5rem;">
                    <input type="text" class="admin-input edit-fullname" placeholder="Full Name" value="${this._esc(p.fullName)}">
                    <input type="text" class="admin-input edit-nickname" placeholder="Nickname" value="${this._esc(p.nickName || '')}">
                    <input type="email" class="admin-input edit-email" placeholder="Email" value="${this._esc(p.email || '')}">
                    <input type="tel" class="admin-input edit-phone" placeholder="Phone" value="${this._esc(p.cell || '')}">
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn-save-edit btn-primary" style="flex: 1; min-height: 44px;">Save Changes</button>
                        <button class="btn-cancel-edit btn-primary" style="flex: 1; min-height: 44px; background: #333; border-color: #555;">Cancel</button>
                    </div>
                </div>
            `;

            // Temp password input (hidden)
            const tempPwHtml = `
                <div class="player-mgmt-temppw" style="display: none; flex-direction: column; gap: 0.5rem; margin-top: 0.5rem;">
                    <input type="text" class="admin-input temp-pw-input" placeholder="Temporary Password (min 8 chars)">
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn-confirm-temppw btn-primary" style="flex: 1; min-height: 44px; background: var(--accent-red); border-color: var(--accent-red);">Confirm Set Password</button>
                        <button class="btn-cancel-temppw btn-primary" style="flex: 1; min-height: 44px; background: #333; border-color: #555;">Cancel</button>
                    </div>
                </div>
            `;

            card.innerHTML = displayHtml + editHtml + tempPwHtml;
            container.appendChild(card);

            // Wire events
            const editBtn = card.querySelector('.btn-edit-player');
            const resetBtn = card.querySelector('.btn-reset-pw');
            const tempPwBtn = card.querySelector('.btn-temp-pw');
            const saveBtn = card.querySelector('.btn-save-edit');
            const cancelBtn = card.querySelector('.btn-cancel-edit');
            const confirmTempBtn = card.querySelector('.btn-confirm-temppw');
            const cancelTempBtn = card.querySelector('.btn-cancel-temppw');
            const editPanel = card.querySelector('.player-mgmt-edit');
            const tempPwPanel = card.querySelector('.player-mgmt-temppw');

            editBtn.addEventListener('click', () => {
                editPanel.style.display = 'flex';
                tempPwPanel.style.display = 'none';
            });

            cancelBtn.addEventListener('click', () => {
                editPanel.style.display = 'none';
            });

            saveBtn.addEventListener('click', () => this.savePlayerEdit(p.playerId, card));

            resetBtn.addEventListener('click', () => this.resetPlayerPassword(p.playerId, p.fullName));

            tempPwBtn.addEventListener('click', () => {
                tempPwPanel.style.display = 'flex';
                editPanel.style.display = 'none';
            });

            cancelTempBtn.addEventListener('click', () => {
                tempPwPanel.style.display = 'none';
            });

            confirmTempBtn.addEventListener('click', () => this.setTempPassword(p.playerId, card));
        });
    }

    _esc(str) {
        const el = document.createElement('span');
        el.textContent = str || '';
        return el.innerHTML;
    }

    /* ------------------------------------------------------------------
       CRUD Actions
       ------------------------------------------------------------------ */

    async savePlayerEdit(targetPlayerId, card) {
        const fields = {
            fullName: card.querySelector('.edit-fullname').value.trim(),
            nickName: card.querySelector('.edit-nickname').value.trim(),
            email: card.querySelector('.edit-email').value.trim(),
            cell: card.querySelector('.edit-phone').value.trim()
        };

        if (!fields.fullName || !fields.email) {
            alert('Name and Email are required.');
            return;
        }

        try {
            await ApiService.updatePlayer(this.playerId, targetPlayerId, fields);
            alert('Player updated successfully.');
            this.fetchInitialData();
        } catch (e) {
            alert(`Update failed: ${e.message}`);
        }
    }

    async resetPlayerPassword(targetPlayerId, playerName) {
        if (!confirm(`Reset password for ${playerName}? They will be prompted to set a new password on next login.`)) return;

        try {
            const data = await ApiService.resetPassword(this.playerId, targetPlayerId);
            alert(data.message);
        } catch (e) {
            alert(`Reset failed: ${e.message}`);
        }
    }

    async setTempPassword(targetPlayerId, card) {
        const pw = card.querySelector('.temp-pw-input').value;
        if (!pw || pw.length < 8) {
            alert('Temporary password must be at least 8 characters.');
            return;
        }

        try {
            const data = await ApiService.setTempPassword(this.playerId, targetPlayerId, pw);
            alert(data.message);
            card.querySelector('.player-mgmt-temppw').style.display = 'none';
            card.querySelector('.temp-pw-input').value = '';
        } catch (e) {
            alert(`Failed: ${e.message}`);
        }
    }

    /* ------------------------------------------------------------------
       Existing Admin Actions
       ------------------------------------------------------------------ */

    async generateSeason() {
        const season = document.getElementById('season-input').value;
        if (!season || this.selectedPlayerIds.size !== 10) {
            alert('Specify season and select exactly 10 players.');
            return;
        }

        if (!confirm(`Finalize draft order for Season ${season}?`)) return;

        try {
            const data = await ApiService.createSeason(this.playerId, season, Array.from(this.selectedPlayerIds));
            alert(data.message);
        } catch (e) {
            alert(`Generation failed: ${e.message}`);
        }
    }

    async deleteSeason() {
        const season = document.getElementById('delete-season-select').value;
        if (!season || !confirm(`Permanently WIPE Season ${season}?`)) return;

        try {
            const data = await ApiService.deleteSeason(this.playerId, season);
            alert(data.message);
            this.fetchInitialData();
        } catch (e) {
            alert(`Deletion failed: ${e.message}`);
        }
    }

    async previewDraft() {
        if (this.selectedPlayerIds.size === 0) return;
        try {
            const { preview } = await ApiService.previewDraft(this.playerId, Array.from(this.selectedPlayerIds));
            UiRenderer.renderDraftOrder(preview.map(p => p.playerName));
            document.getElementById('draft-preview-container')?.classList.remove('hidden');
        } catch (e) {
            alert(`Preview failed: ${e.message}`);
        }
    }

    async createPlayer() {
        const fullName = document.getElementById('new-player-name').value;
        const nickName = document.getElementById('new-player-nick').value;
        const email = document.getElementById('new-player-email').value;
        const phone = document.getElementById('new-player-phone')?.value || '';

        if (!fullName || !email) return;

        try {
            await ApiService.createPlayer(this.playerId, fullName, nickName, email, phone);
            alert('Player created!');
            document.getElementById('new-player-name').value = '';
            document.getElementById('new-player-nick').value = '';
            document.getElementById('new-player-email').value = '';
            if (document.getElementById('new-player-phone')) {
                document.getElementById('new-player-phone').value = '';
            }
            this.fetchInitialData();
        } catch (e) {
            alert(`Creation failed: ${e.message}`);
        }
    }

    async scrapePredictions() {
        if (!confirm('Run Preseason Predictor Scraper?')) return;
        try {
            const data = await ApiService.scrapePredictions(this.playerId);
            alert(data.message);
        } catch (e) {
            alert(`Scraper failed: ${e.message}`);
        }
    }

    async previewRecapPrompt() {
        console.log('[Admin] Requesting recap prompt preview...');
        const year = document.getElementById('recap-year').value;
        const week = document.getElementById('recap-week').value;
        if (!year || !week) {
            alert('Please specify Year and Week.');
            return;
        }

        try {
            const { prompt } = await ApiService.previewRecapPrompt(this.playerId, year, week);
            const textEl = document.getElementById('recap-prompt-text');
            if (textEl) textEl.value = prompt;
            document.getElementById('recap-prompt-preview-container')?.classList.remove('hidden');
            console.log('[Admin] Preview prompt received.');
        } catch (e) {
            alert(`Preview failed: ${e.message}`);
        }
    }

    async previewDraftRecapPrompt() {
        console.log('[Admin] Requesting draft recap prompt preview...');
        const year = document.getElementById('recap-year').value;
        if (!year) {
            alert('Please specify Year.');
            return;
        }

        try {
            const { prompt } = await ApiService.previewDraftRecapPrompt(this.playerId, year);
            const textEl = document.getElementById('recap-prompt-text');
            if (textEl) textEl.value = prompt;
            document.getElementById('recap-prompt-preview-container')?.classList.remove('hidden');
            console.log('[Admin] Draft Preview prompt received.');
        } catch (e) {
            alert(`Preview failed: ${e.message}`);
        }
    }

    async generateRecapAI() {
        const prompt_data = document.getElementById('recap-prompt-text').value;
        const btn = document.getElementById('recap-generate-ai-btn');
        if (!prompt_data) return;

        try {
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Calling Gemini...';
            }
            const { summary } = await ApiService.generateRecapAI(this.playerId, prompt_data);
            const textEl = document.getElementById('recap-final-text');
            if (textEl) textEl.value = summary;
            document.getElementById('recap-final-preview-container')?.classList.remove('hidden');
        } catch (e) {
            alert(`Generation failed: ${e.message}`);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Step 2: Generate AI Summary';
            }
        }
    }

    async broadcastRecap() {
        const year = document.getElementById('recap-year').value;
        const week = document.getElementById('recap-week').value;
        const summary = document.getElementById('recap-final-text').value;

        if (!summary || !confirm(`Broadcast Week ${week} recap to all players?`)) return;

        try {
            const btn = document.getElementById('recap-broadcast-btn');
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Broadcasting...';
            }
            const data = await ApiService.saveAndBroadcastRecap(this.playerId, year, week, summary);
            alert(data.message);
        } catch (e) {
            alert(`Broadcast failed: ${e.message}`);
        } finally {
            const btn = document.getElementById('recap-broadcast-btn');
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Step 3: Save & Broadcast to Players';
            }
        }
    }
}

// ── Draft Active Toggle ──────────────────────────────────
async function initDraftActiveToggle() {
    const toggle = document.getElementById('draft-active-toggle');
    const knob = document.getElementById('draft-active-knob');
    const sub = document.getElementById('draft-active-sub');
    if (!toggle || !knob || !sub) return;

    function applyState(active) {
        toggle.setAttribute('aria-pressed', active ? 'true' : 'false');
        toggle.style.background = active ? 'var(--pos)' : 'rgba(255,255,255,0.12)';
        knob.style.left = active ? '21px' : '3px';
        sub.textContent = active
            ? 'Live Draft visible in nav · all users'
            : 'Shows Live Draft in nav for all users';
        sub.style.color = active ? 'var(--pos)' : 'var(--ink-3)';
    }

    // Load current state
    try {
        const cfg = await fetch('/api/config/settings').then(r => r.json());
        applyState(cfg.draft_active === true);
    } catch (e) {
        console.warn('[Admin] Could not load config/settings', e);
    }

    // Toggle on click
    toggle.addEventListener('click', async () => {
        const current = toggle.getAttribute('aria-pressed') === 'true';
        const next = !current;
        applyState(next); // optimistic
        try {
            await fetch('/api/admin/config/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ draft_active: next }),
            });
        } catch (e) {
            console.error('[Admin] Failed to save draft_active', e);
            applyState(current); // revert on error
        }
    });
}

initDraftActiveToggle();

// Start Admin App
const admin = new AdminApp();
admin.init();
