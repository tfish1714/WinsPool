(function () {
    'use strict';

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = text;
        return n;
    }

    function fmtRecord(r) {
        return r.wins + '-' + r.losses + (r.ties ? '-' + r.ties : '');
    }

    function renderSummary(data) {
        var box = document.getElementById('team-summary');
        var cur = data.current;
        box.appendChild(el('span', null, data.current_season + ' record: ' + fmtRecord(cur.record)));
        if (cur.projected_wins !== null && cur.projected_wins !== undefined) {
            box.appendChild(el('span', 'team-page__muted', 'Projected wins: ' + cur.projected_wins.toFixed(1)));
        }
    }

    function renderHistory(data) {
        var box = document.getElementById('team-history');
        if (!data.history.length) {
            box.appendChild(el('p', 'team-page__muted', 'This team has not been drafted in any pool season.'));
            return;
        }
        data.history.forEach(function (h) {
            var row = el('div', 'team-page__hrow');
            row.appendChild(el('div', 'team-page__season', String(h.season)));
            row.appendChild(el('div', 'team-page__cell', fmtRecord(h)));
            var drafter = el('div', 'team-page__cell');
            drafter.appendChild(document.createTextNode('Drafted by '));
            if (h.drafter) {
                var a = el('a', 'team-page__link', h.drafter.name);
                a.href = '/player/' + encodeURIComponent(h.drafter.playerId);
                drafter.appendChild(a);
            }
            row.appendChild(drafter);
            row.appendChild(el('div', 'team-page__cell team-page__muted', h.pick ? 'Pick ' + h.pick : ''));
            if (h.pool_winner) {
                row.appendChild(el('div', 'team-page__note',
                    'Pool winner: ' + h.pool_winner.name + ' (' + h.pool_winner.wins + ' wins)'));
            }
            box.appendChild(row);
        });
    }

    function renderSchedule(data) {
        var cur = data.current;
        var note = document.getElementById('team-projected-record');
        if (cur.projected_record) {
            note.textContent = 'Projected record: ' + cur.projected_record.wins + '-' + cur.projected_record.losses;
        }
        var box = document.getElementById('team-schedule');
        if (!cur.schedule.length) {
            box.appendChild(el('p', 'team-page__muted', 'No schedule available yet.'));
            return;
        }
        cur.schedule.forEach(function (g) {
            var row = el('div', 'team-page__grow');
            row.appendChild(el('div', 'team-page__week', 'Wk ' + g.week));
            if (g.status === 'bye') {
                row.appendChild(el('div', 'team-page__cell team-page__muted', 'Bye'));
                box.appendChild(row);
                return;
            }
            row.appendChild(el('div', 'team-page__cell', (g.home ? 'vs ' : '@ ') + g.opponent));
            var badge;
            if (g.status === 'played') {
                badge = el('span', 'team-page__badge team-page__badge--' + g.result.toLowerCase(),
                    g.result + (g.score ? ' ' + g.score : ''));
            } else if (g.win_prob !== null && g.win_prob !== undefined && !g.projected) {
                badge = el('span', 'team-page__badge team-page__badge--proj', 'Toss-up (50%)');
            } else if (g.projected) {
                badge = el('span', 'team-page__badge team-page__badge--proj',
                    g.projected + ' (proj ' + Math.round(g.win_prob * 100) + '%)');
            } else {
                badge = el('span', 'team-page__badge team-page__badge--proj', 'TBD');
            }
            row.appendChild(badge);
            box.appendChild(row);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        var raw = document.getElementById('teamData');
        if (!raw) return;
        var data;
        try {
            data = JSON.parse(raw.textContent);
        } catch (e) {
            console.error('team_page.js: failed to parse teamData JSON', e);
            return;
        }
        var select = document.getElementById('team-select');
        if (select) {
            select.addEventListener('change', function () {
                window.location.href = '/team/' + encodeURIComponent(select.value);
            });
        }
        renderSummary(data);
        renderHistory(data);
        renderSchedule(data);
    });
})();
