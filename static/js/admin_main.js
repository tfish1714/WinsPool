import { ApiService } from './api.js?v=1.1';
import { UiRenderer } from './ui_renderer.js?v=1.1';
import { AuthService } from './auth_service.js?v=1.1';

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
        const tabBtns = document.querySelectorAll('.tab-btn');
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
            };
        });
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
        document.getElementById('recap-type')?.addEventListener('change', (e) => {
            const weekContainer = document.getElementById('recap-week-container');
            if (weekContainer) {
                if (e.target.value === 'draft') {
                    weekContainer.classList.add('hidden');
                } else {
                    weekContainer.classList.remove('hidden');
                }
            }
        });
        document.getElementById('recap-preview-prompt-btn')?.addEventListener('click', () => this.previewRecapPrompt());
        document.getElementById('recap-generate-ai-btn')?.addEventListener('click', () => this.generateRecapAI());
        document.getElementById('recap-broadcast-btn')?.addEventListener('click', () => this.broadcastRecap());
    }

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

        if (!fullName || !email) return;

        try {
            await ApiService.createPlayer(this.playerId, fullName, nickName, email);
            alert('Player created!');
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
        const type = document.getElementById('recap-type').value;
        const year = document.getElementById('recap-year').value;
        const week = document.getElementById('recap-week').value;

        if (!year) {
            alert('Please specify Year.');
            return;
        }

        try {
            let res;
            if (type === 'draft') {
                res = await ApiService.previewDraftRecapPrompt(this.playerId, year);
            } else {
                if (!week) return alert('Please specify Week.');
                res = await ApiService.previewRecapPrompt(this.playerId, year, week);
            }
            const textEl = document.getElementById('recap-prompt-text');
            if (textEl) textEl.value = res.prompt;
            document.getElementById('recap-prompt-preview-container')?.classList.remove('hidden');
            console.log('[Admin] Preview prompt received.');
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
                btn.textContent = '🤖 Calling Gemini...';
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
        const type = document.getElementById('recap-type').value;
        const year = document.getElementById('recap-year').value;
        const week = type === 'draft' ? 0 : document.getElementById('recap-week').value;
        const summary = document.getElementById('recap-final-text').value;

        const title = type === 'draft' ? 'Season Preview / Draft Recap' : `Week ${week} recap`;
        if (!summary || !confirm(`Broadcast ${title} to all players?`)) return;

        try {
            const btn = document.getElementById('recap-broadcast-btn');
            if (btn) {
                btn.disabled = true;
                btn.textContent = '📨 Broadcasting...';
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

// Start Admin App
const admin = new AdminApp();
admin.init();
