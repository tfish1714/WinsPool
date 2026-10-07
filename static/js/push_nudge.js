// Standings-page nudge to turn on weekly recap/standings pushes. Never asks for
// browser permission itself; it only links to the player-page Notifications card.
import { STORAGE_KEYS, getAuthHeaders } from './auth_service.js';
import { currentPushSupport, shouldShowPushNudge } from './push_client.js';

const DISMISSED_KEY = 'nfl_wins_push_nudge_dismissed';

function readDismissed() {
    try { return localStorage.getItem(DISMISSED_KEY) === '1'; } catch (e) { return false; }
}

function writeDismissed() {
    try { localStorage.setItem(DISMISSED_KEY, '1'); } catch (e) { /* storage unavailable */ }
}

function render(playerId) {
    const slot = document.getElementById('push-nudge-slot');
    if (!slot || slot.querySelector('.push-nudge')) return;
    const banner = document.createElement('div');
    banner.className = 'push-nudge';
    banner.setAttribute('role', 'status');
    const text = document.createElement('span');
    text.className = 'push-nudge__text';
    text.textContent = 'Get notified when the weekly recap and standings are ready';
    const link = document.createElement('a');
    link.className = 'push-nudge__link';
    link.href = `/player/${encodeURIComponent(playerId)}#notifications`;
    link.textContent = 'Turn on';
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'push-nudge__dismiss';
    close.setAttribute('aria-label', 'Dismiss');
    close.textContent = '×';
    close.addEventListener('click', () => { writeDismissed(); banner.remove(); });
    banner.append(text, link, close);
    slot.appendChild(banner);
}

async function init() {
    let playerId = null;
    try { playerId = localStorage.getItem(STORAGE_KEYS.PLAYER_ID); } catch (e) { /* ignore */ }
    if (!playerId) return;
    const support = currentPushSupport();
    const permission = typeof Notification !== 'undefined' ? Notification.permission : 'default';
    const dismissed = readDismissed();
    // Cheap local checks first so unsupported/dismissed viewers never hit the API.
    if (!shouldShowPushNudge({ support, subscribed: false, permission, dismissed })) return;
    try {
        const res = await fetch('/api/profile/push-status', { headers: getAuthHeaders(), credentials: 'same-origin' });
        if (!res.ok) return;
        const status = await res.json();
        if (!status.configured) return;
        if (shouldShowPushNudge({ support, subscribed: !!status.subscribed, permission, dismissed })) render(playerId);
    } catch (e) { /* nudge is optional */ }
}

init();
