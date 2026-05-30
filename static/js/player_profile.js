(function () {
    'use strict';

    const CHART_DEFAULTS = {
        tickColor: 'rgba(255,255,255,0.7)',
        gridColor: 'rgba(255,255,255,0.1)',
        tooltipBg: 'rgba(0,0,0,0.85)',
    };

    function linearTrendline(values) {
        const n = values.length;
        if (n < 2) return values.slice();
        const sumX  = values.reduce((s, _, i) => s + i, 0);
        const sumY  = values.reduce((s, y) => s + y, 0);
        const sumXY = values.reduce((s, y, i) => s + i * y, 0);
        const sumX2 = values.reduce((s, _, i) => s + i * i, 0);
        const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX) || 0;
        const intercept = (sumY - slope * sumX) / n;
        return values.map((_, i) => Math.round((slope * i + intercept) * 10) / 10);
    }

    function axisConfig() {
        return {
            ticks: { color: CHART_DEFAULTS.tickColor },
            grid:  { color: CHART_DEFAULTS.gridColor },
        };
    }

    document.addEventListener('DOMContentLoaded', function () {
        const raw = document.getElementById('analyticsData');
        if (!raw) return;
        const data = JSON.parse(raw.textContent);
        const seasons = data.seasons.slice().sort((a, b) => a.year - b.year);
        const years   = seasons.map(s => String(s.year));

        // ── Chart 1: Wins Trend ───────────────────────────────────────────────
        const winsData = seasons.map(s => s.totalWins);
        new Chart(document.getElementById('winsTrendChart').getContext('2d'), {
            type: 'line',
            data: {
                labels: years,
                datasets: [
                    {
                        label: 'Wins',
                        data: winsData,
                        borderColor: '#c9a24a',
                        backgroundColor: '#c9a24a',
                        borderWidth: 2,
                        tension: 0.1,
                        pointRadius: 4,
                    },
                    {
                        label: 'Trend',
                        data: linearTrendline(winsData),
                        borderColor: 'rgba(201,162,74,0.4)',
                        borderDash: [5, 5],
                        borderWidth: 1,
                        pointRadius: 0,
                        fill: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { backgroundColor: CHART_DEFAULTS.tooltipBg },
                },
                scales: { x: axisConfig(), y: axisConfig() },
            },
        });

        // ── Chart 2: Draft ROI ────────────────────────────────────────────────
        const pickColors = values => values.map(v => v >= 0 ? '#4ade80' : '#f87171');

        const roiDatasets = [0, 1, 2].map(idx => {
            const values = seasons.map(s => s.picks[idx] ? s.picks[idx].vsProjected : 0);
            return {
                label: `Pick ${idx + 1}`,
                data: values,
                backgroundColor: pickColors(values),
                borderWidth: 0,
            };
        });

        new Chart(document.getElementById('draftRoiChart').getContext('2d'), {
            type: 'bar',
            data: { labels: years, datasets: roiDatasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: CHART_DEFAULTS.tooltipBg,
                        callbacks: {
                            label: function (ctx) {
                                const s = seasons[ctx.dataIndex];
                                const pick = s.picks[ctx.datasetIndex];
                                if (!pick) return '';
                                return `Pick #${pick.pickNum} ${pick.team}: ${pick.actualWins}W (proj ${pick.projectedWins}, slot avg ${pick.slotAvgWins ?? 'N/A'})`;
                            },
                        },
                    },
                },
                scales: {
                    x: axisConfig(),
                    y: {
                        ...axisConfig(),
                        title: {
                            display: true,
                            text: 'Wins vs Projected',
                            color: CHART_DEFAULTS.tickColor,
                        },
                    },
                },
            },
        });

        // ── Chart 3: Rank Over Years ──────────────────────────────────────────
        const rankData = seasons.map(s => s.rank);
        new Chart(document.getElementById('rankChart').getContext('2d'), {
            type: 'line',
            data: {
                labels: years,
                datasets: [
                    {
                        label: 'Rank',
                        data: rankData,
                        borderColor: '#5b9cf6',
                        backgroundColor: '#5b9cf6',
                        borderWidth: 2,
                        tension: 0.1,
                        pointRadius: 4,
                    },
                    {
                        label: 'Trend',
                        data: linearTrendline(rankData),
                        borderColor: 'rgba(91,156,246,0.4)',
                        borderDash: [5, 5],
                        borderWidth: 1,
                        pointRadius: 0,
                        fill: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { backgroundColor: CHART_DEFAULTS.tooltipBg },
                },
                scales: {
                    x: axisConfig(),
                    y: {
                        ...axisConfig(),
                        reverse: true,
                        ticks: {
                            color: CHART_DEFAULTS.tickColor,
                            stepSize: 1,
                            callback: v => `#${v}`,
                        },
                    },
                },
            },
        });
    });
}());
