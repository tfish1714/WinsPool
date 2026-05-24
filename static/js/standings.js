/**
 * standings.js — Chart and week-filter logic for the wins pool standings page.
 *
 * Requires STANDINGS_CONFIG to be defined inline by the template before this script loads:
 *   window.STANDINGS_CONFIG = { year: <int>, standingsOrderIds: [<playerId>, ...] };
 */

(function () {
    'use strict';

    // ---------- Week filter ----------

    window.filterByWeek = function (week) {
        // Update carousel active state
        document.querySelectorAll('.week-item').forEach(item => {
            if (String(item.dataset.week) === String(week)) {
                item.classList.add('active');
                // Safely scroll within the horizontal carousel parent to prevent page jumping
                if (item.parentElement) {
                    item.parentElement.scrollTo({
                        left: item.offsetLeft - (item.parentElement.clientWidth / 2) + (item.clientWidth / 2),
                        behavior: 'smooth'
                    });
                }
            } else {
                item.classList.remove('active');
            }
        });

        // Toggle Game Cards and Date Headers
        const cards = document.querySelectorAll('.game-card');
        const headers = document.querySelectorAll('.date-group-header');

        cards.forEach(card => {
            card.classList.toggle('hidden', String(card.dataset.week) !== String(week));
        });

        // Only show date headers if there is at least one visible card for that date
        headers.forEach(header => {
            const weekOfHeader = String(header.dataset.week);
            header.classList.toggle('hidden', weekOfHeader !== String(week));
        });
    };

    window.toggleCard = function (button) {
        const card = button.closest('.card');
        card.classList.toggle('expanded');
        button.textContent = card.classList.contains('expanded') ? 'Hide Details' : 'Show Details';
    };

    // ---------- Chart ----------

    // Index 0 = leader gold; indices 1-8 = distinct vivid colors
    const COLORS = [
        '#c9a24a',  // gold  — rank 1 only
        '#5b9cf6',  // blue
        '#4ade80',  // green
        '#f87171',  // red
        '#c084fc',  // purple
        '#fb923c',  // orange
        '#22d3ee',  // cyan
        '#f472b6',  // pink
        '#a3e635',  // lime
    ];

    function initChart(year, latestWeek, standingsOrderIds) {
        const _token = localStorage.getItem('nfl_wins_token');
        const _headers = _token ? { 'Authorization': `Bearer ${_token}` } : {};
        fetch(`/api/progress/${year}/${latestWeek}`, { headers: _headers })
            .then(res => res.json())
            .then(data => {
                const chartData = data.player_chart;
                if (!chartData || !chartData.labels || chartData.labels.length === 0) return;

                const weeks = chartData.labels.map(w => `Week ${w}`);
                const datasets = chartData.datasets;

                // Sort datasets by standings position using playerId (robust — no string matching)
                datasets.sort((a, b) => {
                    const ai = standingsOrderIds.indexOf(a.playerId);
                    const bi = standingsOrderIds.indexOf(b.playerId);
                    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
                });

                datasets.forEach((ds, i) => {
                    ds.borderColor = COLORS[i % COLORS.length];
                    ds.backgroundColor = COLORS[i % COLORS.length];
                    ds.borderWidth = 2;
                    ds.tension = 0.1;
                    ds.pointRadius = 3;
                });

                const ctx = document.getElementById('winsChart').getContext('2d');
                new Chart(ctx, {
                    type: 'line',
                    data: { labels: weeks, datasets: datasets },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        plugins: {
                            tooltip: {
                                backgroundColor: 'rgba(0,0,0,0.85)',
                                titleColor: 'rgba(255,255,255,0.9)',
                                bodyColor: 'rgba(255,255,255,0.75)',
                                borderColor: 'rgba(255,255,255,0.1)',
                                borderWidth: 1,
                                itemSort: function (a, b) {
                                    // Primary: highest wins first
                                    const winsDiff = b.raw - a.raw;
                                    if (winsDiff !== 0) return winsDiff;
                                    // Tiebreaker: use server-computed standings rank via playerId
                                    const ai = standingsOrderIds.indexOf(a.dataset.playerId);
                                    const bi = standingsOrderIds.indexOf(b.dataset.playerId);
                                    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
                                }
                            },
                            legend: { display: false },
                            title: {
                                display: true,
                                text: `Player Total Wins by Week — ${year}`,
                                color: 'rgba(255,255,255,0.7)',
                                font: { size: 13, weight: '500' }
                            }
                        },
                        scales: {
                            x: {
                                ticks: { color: 'rgba(255,255,255,0.7)' },
                                grid: { color: 'rgba(255,255,255,0.1)' }
                            },
                            y: {
                                ticks: { color: 'rgba(255,255,255,0.7)' },
                                grid: { color: 'rgba(255,255,255,0.1)' }
                            }
                        }
                    }
                });
            })
            .catch(err => console.error('Chart load error:', err));
    }

    // ---------- Boot ----------

    document.addEventListener('DOMContentLoaded', () => {
        // Apply default week filter from carousel
        const activeItem = document.querySelector('.week-item.active');
        if (activeItem) {
            const week = activeItem.dataset.week;
            filterByWeek(week);
        }

        // Init chart using config injected by Jinja template
        const cfg = window.STANDINGS_CONFIG;
        if (cfg && document.getElementById('winsChart')) {
            initChart(cfg.year, cfg.latestWeek, cfg.standingsOrderIds);
        }
    });

}());
