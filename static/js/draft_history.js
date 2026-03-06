/**
 * draft_history.js — Player / Team view toggle for the draft history page.
 */

(function () {
    'use strict';

    window.switchView = function (view) {
        const playerBtn = document.getElementById('btn-player-view');
        const teamBtn = document.getElementById('btn-team-view');
        const playerView = document.getElementById('player-view');
        const teamView = document.getElementById('team-view');

        if (playerBtn) playerBtn.classList.remove('active');
        if (teamBtn) teamBtn.classList.remove('active');

        const activeBtn = document.getElementById('btn-' + view + '-view');
        if (activeBtn) activeBtn.classList.add('active');

        if (playerView) playerView.style.display = (view === 'player') ? 'block' : 'none';
        if (teamView) teamView.style.display = (view === 'team') ? 'block' : 'none';
    };

}());
