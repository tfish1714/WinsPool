(function () {
    'use strict';

    var STATUS_COLORS = {
        ok:      'var(--accent-green)',
        warn:    '#f5a623',
        error:   'var(--accent-red)',
        unknown: 'var(--text-secondary)',
    };

    function relTime(unixTs) {
        if (!unixTs) return '?';
        var diffSec = Math.floor(Date.now() / 1000 - unixTs);
        if (diffSec < 3600)  return Math.round(diffSec / 60) + 'm ago';
        if (diffSec < 86400) return (diffSec / 3600).toFixed(1) + 'h ago';
        return Math.floor(diffSec / 86400) + 'd ago';
    }

    function dot(status) {
        var color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
        return '<span style="color:' + color + '; margin-right:4px;">●</span>';
    }

    function buildChips(data) {
        var chips = [];

        var g = data.nfl_games || {};
        chips.push({
            label: 'NFL Games',
            text: g.season
                ? g.season + ' · W' + g.current_week + ' · ' + g.games_with_results + '/' + g.games_total + ' · ' + (g.last_game_date || '?')
                : (g.error || 'unavailable'),
            status: g.status || 'unknown',
            error: g.error,
        });

        var s = data.standings || {};
        chips.push({
            label: 'Standings',
            text: s.season
                ? s.season + ' · W' + s.week + ' · ' + s.teams_count + ' teams'
                : (s.error || 'unavailable'),
            status: s.status || 'unknown',
            error: s.error,
        });

        var e = data.elo || {};
        chips.push({
            label: 'Elo',
            text: e.season
                ? e.season + ' · W' + e.week + ' · ' + (e.games_processed || 0).toLocaleString() + ' games'
                : (e.status === 'unknown' ? 'never run' : (e.error || 'unavailable')),
            status: e.status || 'unknown',
            error: e.error,
        });

        var p = data.predictions || {};
        chips.push({
            label: 'Predictions',
            text: p.season
                ? p.season + ' · locked thru W' + p.locked_through_week + ' · ' + p.coverage_pct + '%'
                : (p.error || 'unavailable'),
            status: p.status || 'unknown',
            error: p.error,
        });

        var c = data.analytics_cache || {};
        chips.push({
            label: 'Cache',
            text: c.age_hours != null
                ? c.age_hours + 'h ago'
                : (c.status === 'unknown' ? 'never built' : (c.error || 'unavailable')),
            status: c.status || 'unknown',
            error: c.error,
        });

        var n = data.nflverse || {};
        chips.push({
            label: 'nflverse',
            text: n.season
                ? n.season + ' · ' + n.datasets_synced + ' synced · ' + relTime(n.completed_at)
                : (n.status === 'unknown' ? 'never run' : (n.error || 'unavailable')),
            status: n.status || 'unknown',
            error: n.error,
        });

        return chips;
    }

    function render(chips) {
        var bar = document.getElementById('sync-health-bar');
        if (!bar) return;
        bar.innerHTML = chips.map(function (c) {
            var title = c.error ? ' title="' + c.error + '"' : '';
            return '<div class="health-chip"' + title + '>' + dot(c.status) + '<strong>' + c.label + '</strong>&nbsp;' + c.text + '</div>';
        }).join('');
    }

    function renderError() {
        var bar = document.getElementById('sync-health-bar');
        if (!bar) return;
        bar.innerHTML = '<div class="health-chip"><span style="color:var(--text-secondary);">●</span>&nbsp;Health check unavailable</div>';
    }

    function refresh() {
        fetch('/api/admin/sync_status')
            .then(function (resp) {
                if (!resp.ok) { renderError(); return null; }
                return resp.json();
            })
            .then(function (data) {
                if (data) render(buildChips(data));
            })
            .catch(function () {
                renderError();
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        refresh();
        setInterval(refresh, 300000);
    });
}());
