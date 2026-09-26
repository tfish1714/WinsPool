/**
 * standings_refresh.js -- Polls /api/live-standings and patches the wins pool
 * standings page (Rank 1 hero card, desktop rows, mobile cards) in place, so
 * game-day changes show up without a manual reload.
 *
 * Requires window.STANDINGS_CONFIG = { year, standingsOrderIds } (set inline
 * by wins_pool.html). Polls every 30s and skips while the tab is hidden.
 *
 * Elements are found by data-player-id / data-team / data-role hooks in the
 * template. Text and classes are patched; rows are never re-ordered in place.
 * If the payload's player ordering differs from the rendered one (a rank
 * change), the page reloads once so the hero card and row order stay correct,
 * guarded against reload loops.
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 30000;
    const RELOAD_GUARD_KEY = 'standingsRefreshReloadAt';
    const RELOAD_GUARD_MS = 120000;

    function signed(n) {
        return (n >= 0 ? '+' : '') + n;
    }

    function setRole(root, role, value) {
        root.querySelectorAll('[data-role="' + role + '"]').forEach(function (el) {
            const text = el.hasAttribute('data-signed') ? signed(value) : String(value);
            if (el.textContent !== text) el.textContent = text;
        });
    }

    function patchTeam(root, team) {
        root.querySelectorAll('[data-team="' + team.abbr + '"]').forEach(function (block) {
            const wins = block.querySelector('[data-role="team-wins"]');
            if (wins) wins.textContent = team.wins + 'W';

            const pd = block.querySelector('[data-role="team-pd"]');
            if (pd) {
                pd.textContent = signed(team.pt_diff);
                pd.classList.toggle('pos', team.pt_diff >= 0);
                pd.classList.toggle('neg', team.pt_diff < 0);
            }

            const dot = block.querySelector('[data-role="live"]');
            if (dot) {
                dot.hidden = !team.is_live;
                if (team.is_live) {
                    dot.title = team.live_score || 'Live';
                } else {
                    dot.removeAttribute('title');
                }
            }
        });
    }

    function patchPlayer(root, row) {
        setRole(root, 'rank', row.rank);
        setRole(root, 'total', row.total_wins);
        ['tb1', 'tb2', 'tb3', 'tb4', 'tb5', 'tb6'].forEach(function (key) {
            setRole(root, key, row.tiebreakers[key]);
        });
        row.teams.forEach(function (team) {
            patchTeam(root, team);
        });
    }

    function orderChanged(standings) {
        const cfg = window.STANDINGS_CONFIG;
        if (!cfg || !Array.isArray(cfg.standingsOrderIds)) return false;
        const rendered = cfg.standingsOrderIds.map(Number);
        const incoming = standings.map(function (row) { return Number(row.player_id); });
        return rendered.length === incoming.length &&
            rendered.some(function (id, i) { return id !== incoming[i]; });
    }

    function reloadOnce() {
        try {
            const last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY) || 0);
            if (Date.now() - last < RELOAD_GUARD_MS) return false;
            sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
        } catch (e) {
            return false; // storage unavailable: never risk a reload loop
        }
        window.location.reload();
        return true;
    }

    function apply(data) {
        const standings = data.standings || [];
        if (!standings.length) return;
        if (orderChanged(standings) && reloadOnce()) return;

        standings.forEach(function (row) {
            document.querySelectorAll('[data-player-id="' + row.player_id + '"]').forEach(function (root) {
                patchPlayer(root, row);
            });
        });
        // Values changed in place; let listeners (tiebreaker highlights) recompute.
        document.dispatchEvent(new CustomEvent('standings:patched'));
    }

    async function poll(year) {
        if (document.hidden) return;
        try {
            const res = await fetch('/api/live-standings?year=' + encodeURIComponent(year));
            if (!res.ok) return;
            apply(await res.json());
        } catch (e) {
            console.error('Live standings refresh failed:', e);
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        const cfg = window.STANDINGS_CONFIG;
        if (!cfg || !cfg.year) return;
        setInterval(function () { poll(cfg.year); }, POLL_INTERVAL_MS);
        // Catch up right away when the tab becomes visible again.
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) poll(cfg.year);
        });
    });
}());
