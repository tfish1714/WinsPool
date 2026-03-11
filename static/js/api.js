/**
 * WinsPool API Service Module (Refactored)
 * Encapsulates all backend communication with robust timeout handling.
 */

const API_BASE = '/api';
const DEFAULT_TIMEOUT = 10000; // 10 seconds

async function fetchWithTimeout(url, options = {}) {
    const { timeout = DEFAULT_TIMEOUT } = options;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(timer);
        if (!response.ok) {
            const err = await response.json().catch(() => ({ error: 'Unknown API Error' }));
            throw new Error(err.error || response.statusText);
        }
        return await response.json();
    } catch (e) {
        clearTimeout(timer);
        if (e.name === 'AbortError') throw new Error('Request timed out. Please check your connection.');
        throw e;
    }
}

export const ApiService = {
    // --- Public / Data API ---
    async fetchStandings(year) {
        return fetchWithTimeout(`${API_BASE}/standings?year=${year}`);
    },
    async fetchSchedule(year) {
        return fetchWithTimeout(`${API_BASE}/schedule?year=${year}`);
    },
    async fetchDraftOrder(year) {
        return fetchWithTimeout(`${API_BASE}/draft_order?year=${year}`);
    },
    async generateRecap(year, week) {
        return fetchWithTimeout(`${API_BASE}/ai/recap?year=${year}&week=${week}`);
    },

    // --- Admin API ---
    async fetchPlayers(playerId) {
        return fetchWithTimeout(`${API_BASE}/admin/players?playerId=${playerId}`);
    },
    async fetchSeasons(playerId) {
        return fetchWithTimeout(`${API_BASE}/admin/seasons?playerId=${playerId}`);
    },
    async createSeason(playerId, season, playerIds) {
        return fetchWithTimeout(`${API_BASE}/admin/new_season`, {
            method: 'POST',
            body: JSON.stringify({ playerId, season, playerIds })
        });
    },
    async deleteSeason(playerId, season) {
        return fetchWithTimeout(`${API_BASE}/admin/delete_season`, {
            method: 'POST',
            body: JSON.stringify({ playerId, season })
        });
    },
    async previewDraft(playerId, playerIds) {
        return fetchWithTimeout(`${API_BASE}/admin/preview_draft_order`, {
            method: 'POST',
            body: JSON.stringify({ playerId, playerIds })
        });
    },
    async createPlayer(playerId, fullName, nickName, email) {
        return fetchWithTimeout(`${API_BASE}/admin/create_player`, {
            method: 'POST',
            body: JSON.stringify({ playerId, fullName, nickName, email })
        });
    },
    async scrapePredictions(playerId) {
        return fetchWithTimeout(`${API_BASE}/admin/scrape_predictions`, {
            method: 'POST',
            body: JSON.stringify({ playerId })
        });
    },
    async previewRecapPrompt(playerId, year, week) {
        return fetchWithTimeout(`${API_BASE}/admin/recap/preview_prompt`, {
            method: 'POST',
            body: JSON.stringify({ playerId, year, week })
        });
    },
    async previewDraftRecapPrompt(playerId, year) {
        return fetchWithTimeout(`${API_BASE}/admin/draft_recap/preview_prompt`, {
            method: 'POST',
            body: JSON.stringify({ playerId, year })
        });
    },
    async generateRecapAI(playerId, prompt_data) {
        return fetchWithTimeout(`${API_BASE}/admin/recap/generate`, {
            method: 'POST',
            body: JSON.stringify({ playerId, prompt_data })
        });
    },
    async saveAndBroadcastRecap(playerId, year, week, summary) {
        return fetchWithTimeout(`${API_BASE}/admin/recap/save_and_broadcast`, {
            method: 'POST',
            body: JSON.stringify({ playerId, year, week, summary })
        });
    }
};
