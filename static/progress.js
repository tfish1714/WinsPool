let progressChartInstance = null;
let currentSeason = 'latest';
let currentWeek = 'latest';

document.addEventListener('DOMContentLoaded', () => {
    // Only fetch if on progress tab, but we can pre-fetch
    initProgress();

    // Wire up selectors
    const seasonSelect = document.getElementById('season-select');
    const weekSelect = document.getElementById('week-select');

    if (seasonSelect) {
        seasonSelect.addEventListener('change', (e) => {
            currentSeason = e.target.value;
            fetchAndRenderProgress();
        });
    }

    if (weekSelect) {
        weekSelect.addEventListener('change', (e) => {
            currentWeek = e.target.value;
            fetchAndRenderProgress();
        });
    }
});

async function initProgress() {
    await fetchAndRenderProgress();
}

async function fetchAndRenderProgress() {
    try {
        const response = await fetch(`/api/progress/${currentSeason}/${currentWeek}`);
        const data = await response.json();

        if (data.error) {
            console.error(data.error);
            return;
        }

        // Update selectors to actual season/week if it was latest
        const seasonSelect = document.getElementById('season-select');
        const weekSelect = document.getElementById('week-select');

        if (seasonSelect.options.length === 0) {
            // Populate just some dummy past years for the dropdown for now
            for (let y = data.season; y >= 2013; y--) {
                const opt = document.createElement('option');
                opt.value = y;
                opt.textContent = y;
                if (y == data.season) opt.selected = true;
                seasonSelect.appendChild(opt);
            }
        }

        // We can just set weeks 1-18 blindly
        if (weekSelect.options.length === 0) {
            for (let w = 1; w <= 18; w++) {
                const opt = document.createElement('option');
                opt.value = w;
                opt.textContent = `Week ${w}`;
                if (w == data.week) opt.selected = true;
                weekSelect.appendChild(opt);
            }
        } else {
            weekSelect.value = data.week;
            seasonSelect.value = data.season;
        }

        renderChart(data);
        renderStandings(data.standings);

    } catch (e) {
        console.error("Failed to fetch progress", e);
    }
}

function renderChart(data) {
    const ctx = document.getElementById('progressChart').getContext('2d');

    if (progressChartInstance) {
        progressChartInstance.destroy();
    }

    // Construct line chart datasets from data.player_chart
    // Add nice colors
    const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16'];

    data.player_chart.datasets.forEach((ds, i) => {
        ds.borderColor = colors[i % colors.length];
        ds.backgroundColor = 'transparent';
        ds.tension = 0.3; // Smooth lines
        ds.borderWidth = 3;
        ds.pointRadius = 4;
        ds.pointHoverRadius = 7;
    });

    progressChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.player_chart.labels,
            datasets: data.player_chart.datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                title: {
                    display: true,
                    text: `Player Wins by Week (${data.season})`,
                    color: '#fff',
                    font: { size: 18, family: 'Outfit' }
                },
                legend: {
                    labels: { color: '#cbd5e1', font: { family: 'Outfit', size: 14 } }
                }
            },
            scales: {
                x: {
                    title: { display: true, text: 'Week', color: '#94a3b8' },
                    ticks: { color: '#94a3b8' },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                },
                y: {
                    title: { display: true, text: 'Total Wins', color: '#94a3b8' },
                    ticks: { color: '#94a3b8', stepSize: 1 },
                    grid: { color: 'rgba(255,255,255,0.05)' }
                }
            }
        }
    });
}

function renderStandings(standings) {
    const tbody = document.querySelector('#standings-table tbody');
    tbody.innerHTML = '';

    // Sort by rank
    standings.sort((a, b) => a.my_ranks - b.my_ranks);

    standings.forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>#${row.my_ranks}</td>
            <td><strong>${row.nickName}</strong></td>
            <td>${row.team}</td>
            <td style="color: var(--primary-color); font-weight: bold;">${row.wins}</td>
            <td style="color: ${row.ptDiff > 0 ? 'var(--accent-green)' : 'var(--accent-red)'}">${row.ptDiff > 0 ? '+' : ''}${row.ptDiff}</td>
        `;
        tbody.appendChild(tr);
    });
}
