/**
 * live_refresh.js — Polls /api/live-scores and patches in-progress game
 * cards on the schedule page without a full reload.
 *
 * Requires SCHEDULE_CONFIG to be defined inline by the template before this
 * script loads: window.SCHEDULE_CONFIG = { year: <int> };
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 30000;

    function formatScore(update, liveKey, finalKey) {
        if (update.is_live && update[liveKey] !== null && update[liveKey] !== undefined) {
            return update[liveKey];
        }
        if (update[finalKey] !== null && update[finalKey] !== undefined && update[finalKey] !== -1000) {
            return update[finalKey];
        }
        return '—'; // em dash, matches the template's default
    }

    function formatStatus(update) {
        if (update.result !== null && update.result !== undefined && update.result !== -1000) {
            return { text: 'Final', live: false, final: true };
        }
        if (update.is_live) {
            const text = update.clock === 'Halftime' ? 'Halftime' : `Q${update.period} ${update.clock}`;
            return { text, live: true, final: false };
        }
        return null; // not live, not final -- leave the server-rendered kickoff time alone
    }

    function applyUpdate(card, update) {
        if (!update) return;

        const awayScoreEl = card.querySelector('[data-role="away-score"]');
        const homeScoreEl = card.querySelector('[data-role="home-score"]');
        if (awayScoreEl) awayScoreEl.textContent = formatScore(update, 'live_away_score', 'away_score');
        if (homeScoreEl) homeScoreEl.textContent = formatScore(update, 'live_home_score', 'home_score');

        const statusEl = card.querySelector('[data-role="status"]');
        const status = formatStatus(update);
        if (statusEl && status) {
            statusEl.textContent = status.text;
            statusEl.classList.toggle('status-live', status.live);
            statusEl.classList.toggle('status-final', status.final);
        }

        const awayPossessionEl = card.querySelector('[data-role="away-possession"]');
        const homePossessionEl = card.querySelector('[data-role="home-possession"]');
        if (awayPossessionEl) awayPossessionEl.hidden = !(update.is_live && update.possession === 'away');
        if (homePossessionEl) homePossessionEl.hidden = !(update.is_live && update.possession === 'home');
    }

    async function poll(year) {
        if (document.hidden) return;
        try {
            const res = await fetch(`/api/live-scores?year=${year}`);
            if (!res.ok) return;
            const data = await res.json();
            document.querySelectorAll('.game-card[data-game-id]').forEach((card) => {
                applyUpdate(card, data[card.dataset.gameId]);
            });
        } catch (e) {
            console.error('Live score refresh failed:', e);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const cfg = window.SCHEDULE_CONFIG;
        if (!cfg || !cfg.year) return;
        setInterval(() => poll(cfg.year), POLL_INTERVAL_MS);
    });
}());
