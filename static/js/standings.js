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
                // Scroll the active item into view within the horizontal carousel
                item.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
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

    const COLORS = [
        '#4aa8ff', '#f5c542', '#ff6b6b', '#6cd97e',
        '#a36be8', '#ff9f43', '#00cec9', '#fd79a8', '#e84393'
    ];

    function initChart(year, standingsOrderIds) {
        fetch(`/api/progress/${year}/latest`)
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
                                color: '#ffffff',
                                font: { size: 16 }
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
            initChart(cfg.year, cfg.standingsOrderIds);
        }
    });

}());
